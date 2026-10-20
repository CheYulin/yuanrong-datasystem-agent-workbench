#!/usr/bin/env python3
"""Two isolated Mooncake queue-capacity correctness probes, never performance repeats."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import traceback

from profile_put import atomic_json, digest, drain, stop_process, utc

QUEUE_ENV = ("MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER", "MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER")
CGROUP = Path("/sys/fs/cgroup/kvcfair20261020")


def record(path):
    path = Path(path)
    return {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}


def frozen_inputs(root, stage, destination=None):
    records = {}
    for name, expected in stage["inputs"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
            raise ValueError("Unsafe staged input path: " + name)
        source = root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError("Missing regular staged input: " + name)
        actual = record(source)
        if any(actual[field] != expected[field] for field in ("sha256", "bytes")):
            raise ValueError("Frozen input changed: " + name)
        records[name] = actual
        if destination is not None:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied = record(target)
            if any(copied[field] != expected[field] for field in ("sha256", "bytes")):
                raise ValueError("Frozen snapshot changed while copying: " + name)
    native = {}
    for path, expected in stage.get("build_artifacts", {}).items():
        actual = record(Path(path))
        if actual["sha256"] != expected["sha256"]:
            raise ValueError("Pinned native artifact changed: " + path)
        native[path] = actual
    return {"runtime": records, "native": native}


def execute(argv, log, env, root, timeout):
    result = {"argv": list(map(str, argv)), "cwd": str(root), "start_wall_ns": time.time_ns(),
              "start_mono_ns": time.monotonic_ns(), "returncode": None, "timed_out": False}
    proc = None
    try:
        with log.open("x") as output:
            proc = subprocess.Popen(result["argv"], stdout=output, stderr=subprocess.STDOUT,
                                    cwd=root, env=env, start_new_session=True)
            try:
                result["returncode"] = proc.wait(timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                stop_process(proc)
                result["returncode"] = proc.returncode
            except BaseException:
                stop_process(proc)
                raise
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        result.update(end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
        result["duration_s"] = (result["end_mono_ns"] - result["start_mono_ns"]) / 1e9
        result["log"] = record(log) if log.exists() else None
    return result


def environments(product, variables, profile):
    declared = {key: value.format(**variables) for key, value in product.get("env", {}).items()}
    env = dict(os.environ)
    env.update(declared)
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1", GLOG_minloglevel="1")
    for key in QUEUE_ENV:
        env.pop(key, None)
    if profile == "queue8192":
        env.update({key: "8192" for key in QUEUE_ENV})
    return env, {"manifest": declared, "effective_selected": {
        key: env.get(key) for key in sorted(set(declared) | set(QUEUE_ENV) |
                                            {"GLOG_minloglevel", "MC_TCP_ADMISSION_TIMEOUT_MS", "OMP_NUM_THREADS",
                                             "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "PYTHONHASHSEED"})},
                 "all_tcp_overrides": {key: value for key, value in env.items() if key.startswith("MC_TCP_")},
                 "queue_override": {key: env.get(key) for key in QUEUE_ENV},
                 "boundary": "Client warning level is 1; existing server env.sh may set level 2. Live server environment is saved separately. No native lane/timeout override is introduced."}


def live_servers(run_dir):
    rows = []
    path = run_dir / "pids"
    for value in path.read_text().split() if path.exists() else []:
        if not value.isdigit():
            continue
        proc = Path("/proc") / value
        try:
            stat = (proc / "stat").read_text()
            fields = stat[stat.rfind(")") + 2:].split()
            pairs = (proc / "environ").read_bytes().split(b"\0")
            env = {key.decode(errors="replace"): val.decode(errors="replace")
                   for pair in pairs if b"=" in pair for key, val in [pair.split(b"=", 1)]}
            rows.append({"pid": int(value), "start_ticks": fields[19],
                         "argv": [item.decode(errors="replace") for item in (proc / "cmdline").read_bytes().split(b"\0") if item],
                         "cgroup": (proc / "cgroup").read_text(),
                         "environment": {key: env.get(key) for key in ("GLOG_minloglevel", *QUEUE_ENV)},
                         "all_tcp_overrides": {key: val for key, val in env.items() if key.startswith("MC_TCP_")}})
        except OSError as error:
            rows.append({"pid": int(value), "error": str(error)})
    return rows


def cgroup_controls():
    result = {}
    for role in ("server", "client", "monitor"):
        result[role] = {}
        for name in ("cpuset.cpus.effective", "cpuset.mems.effective", "cpu.max", "memory.max", "memory.swap.max"):
            path = CGROUP / role / name
            result[role][name] = path.read_text().strip() if path.is_file() else None
    return result


def log_evidence(directory):
    files, mentions = [], []
    for path in sorted(directory.rglob("*.log")):
        files.append(record(path))
        for number, line in enumerate(path.read_bytes().splitlines(), 1):
            text = line.decode(errors="replace")
            if any(word in text.lower() for word in ("queue", "admission", "reject", "transfer_fail", "-800")) and len(mentions) < 2000:
                mentions.append({"path": str(path), "line": number, "line_sha256": hashlib.sha256(line).hexdigest(), "text": text})
    return {"files": files, "candidate_mentions": mentions,
            "interpretation": "Raw candidate lines only. TRANSFER_FAIL or improvement alone does not confirm QUEUE_FULL or its cause."}


def run(args):
    root, out = args.root.resolve(strict=True), args.out.resolve()
    if out.parent != root or out.exists() or args.out.is_symlink():
        raise ValueError("Use a fresh direct child output directory of the existing frozen root")
    stage_path = root / "batch-stage.json"
    stage = json.loads(stage_path.read_text())
    if stage.get("root") != str(root):
        raise ValueError("The frozen stage root differs from --root")
    manifest = json.loads((root / "products.json").read_text())
    products = manifest.get("products", []) if isinstance(manifest, dict) else manifest
    matches = [product for product in products if product["name"] == "mooncake"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one pinned Mooncake product")
    product = matches[0]
    lock = os.open(root / "suite.lock", os.O_RDWR | os.O_NOFOLLOW)
    summary = {"kind": "mooncake_tcp_queue_capacity_diagnostic", "benchmark_eligible": False,
               "root": str(root), "out": str(out), "profiles": [], "errors": [], "completed": [],
               "started": utc(), "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(),
               "scope": "Two fresh-server mechanism/correctness probes. Native validation may differ; never performance repeats or inferred queue-full confirmation."}
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        initial = drain(CGROUP)
        out.mkdir()
        atomic_json(out / "initial-drain.json", initial)
        shutil.copy2(Path(__file__).resolve(), out / "probe-source.py")
        summary["probe_source"] = record(out / "probe-source.py")
        summary["stage"] = record(stage_path)
        summary["cgroup_controls_before"] = cgroup_controls()
        summary["product"] = product
        summary["source_inputs"] = frozen_inputs(root, stage, out / "frozen-runtime")
        atomic_json(out / "inputs.json", {"stage": stage, "product": product, "probe": summary["probe_source"],
                                         "inputs": summary["source_inputs"]})
        for name in ("default", "queue8192"):
            directory = out / name
            directory.mkdir()
            run_dir = root / "run" / (out.name + "-" + name)
            variables = {"run_dir": str(run_dir), "runtime_dir": str(run_dir), "server_logs": str(directory / "server-logs")}
            env, env_evidence = environments(product, variables, name)
            atomic_json(directory / "environment.json", env_evidence)
            profile = {"name": name, "commands": {}, "native_valid": None, "cleanup_ok": False,
                       "run_dir": str(run_dir), "environment": env_evidence, "errors": []}
            summary["profiles"].append(profile)
            stop = [value.format(**variables) for value in product["stop"]]
            start_attempted = False
            try:
                drain(CGROUP)
                if run_dir.exists():
                    raise ValueError("Refusing to reuse an existing diagnostic runtime directory")
                start = [value.format(**variables) for value in product["start"]]
                start_attempted = True
                profile["commands"]["start"] = execute(start, directory / "start.log", env, root, 180)
                if profile["commands"]["start"]["returncode"] != 0:
                    raise RuntimeError("Native lifecycle start failed")
                time.sleep(2)
                profile["live_servers"] = live_servers(run_dir)
                output = directory / "benchmark.json"
                command = ["python3", str(root / "cgroup.py"), "--group", "client", "exec", "--",
                           product["python"], str(root / "batch_benchmark.py"), "--adapter", product["adapter"],
                           "--config", product["config"], "--op", "upsert", "--batch", "4096",
                           "--concurrency", "8", "--size", "1024", "--pool-keys", "65536",
                           "--duration", "1", "--warmup", ".1", "--put-keys", "32768",
                           "--run-id", out.name + "-" + name, "--out", str(output)]
                profile["commands"]["benchmark"] = execute(command, directory / "benchmark.log", env, root, 600)
                if output.is_file():
                    profile["benchmark_file"] = record(output)
                    profile["benchmark"] = json.loads(output.read_text())
                    profile["native_valid"] = profile["benchmark"].get("valid") is True
                execution = profile["commands"]["benchmark"]
                if execution["timed_out"] or execution.get("error") or not output.is_file():
                    raise RuntimeError("Native probe did not terminate with its raw benchmark JSON")
                summary["completed"].append(name)
            except Exception:
                profile["errors"].append(traceback.format_exc())
            finally:
                profile["commands"]["stop"] = (execute(stop, directory / "stop.log", env, root, 180)
                                               if start_attempted else {"skipped": "No lifecycle start was attempted; preexisting runtime is not owned"})
                try:
                    profile["drained"] = drain(CGROUP, timeout=10)
                    profile["cleanup_ok"] = not start_attempted or profile["commands"]["stop"]["returncode"] == 0
                except Exception:
                    profile["errors"].append(traceback.format_exc())
                if run_dir.exists():
                    shutil.copytree(run_dir, directory / "runtime", symlinks=True)
                profile["logs"] = log_evidence(directory)
                atomic_json(directory / "profile.json", profile)
                atomic_json(out / "summary.json", summary)
            if not profile["cleanup_ok"]:
                raise RuntimeError("Owned lifecycle cleanup failed; refusing a subsequent profile")
        summary["source_inputs_after"] = frozen_inputs(root, stage)
        summary["cgroup_controls_after"] = cgroup_controls()
        if summary["cgroup_controls_after"] != summary["cgroup_controls_before"]:
            raise RuntimeError("Cgroup resource controls changed during the diagnostic")
        summary["final_drain"] = drain(CGROUP)
    except BaseException:
        summary["errors"].append(traceback.format_exc())
    finally:
        os.close(lock)
        summary.update(ended=utc(), end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
        summary["duration_s"] = (summary["end_mono_ns"] - summary["start_mono_ns"]) / 1e9
        summary["completed_and_clean"] = (summary["completed"] == ["default", "queue8192"] and not summary["errors"]
                                          and all(row["cleanup_ok"] and not row["errors"] for row in summary["profiles"]))
        if out.exists():
            atomic_json(out / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    result = run(args)
    print(json.dumps({"completed_and_clean": result["completed_and_clean"], "duration_s": result["duration_s"],
                      "profiles": [{"name": row["name"], "native_valid": row["native_valid"], "errors": row["errors"]} for row in result["profiles"]]}))
    raise SystemExit(0 if result["completed_and_clean"] else 1)


if __name__ == "__main__":
    main()
