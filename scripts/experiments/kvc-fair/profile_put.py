#!/usr/bin/env python3
"""Nine isolated PUT resource observations; never a fourth performance repeat.

Run only after the main suite has stopped, for example:
python3 profile_put.py --root /home/worktrees/kvc-fair-20261020 \
  --baseline-suite /home/worktrees/kvc-fair-20261020/formal-001 \
  --out /home/worktrees/kvc-fair-20261020/put-resource-profile-001
"""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import random
import shlex
import signal
import subprocess
import sys
import time
import traceback

from kvcbench.platform.files import digest
from kvcbench.platform.cgroup import drain, members, placement

PURPOSE = "supplemental_resource_only"
SIZES = [1024, 65536, 1048576]
PRODUCTS = {"datasystem", "mooncake", "lmcache"}


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def stop_process(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(5)


def inputs(root, manifest, baseline):
    summary = json.loads((baseline / "summary.json").read_text())
    plan = json.loads((baseline / "plan.json").read_text())
    expected_cells = {f"r{row['repeat']}-{row['product']}-s{row['size']}-c{row['concurrency']}" for row in plan}
    if not summary.get("ended") or summary.get("fatal") or summary.get("failed") or not expected_cells or set(summary.get("completed", [])) != expected_cells:
        raise RuntimeError("Primary suite must have ended successfully with completed cells matching its full original plan")
    recorded = json.loads((baseline / "script-hashes.json").read_text())
    frozen = {}
    for name in ["benchmark.py", "cgroup.py"]:
        expected = recorded[name]
        if digest(root / name) != expected:
            raise RuntimeError("Frozen main-suite input changed: " + name)
        frozen[name] = expected
    products = json.loads(manifest.read_text())["products"]
    if len(products) != 3 or {row["name"] for row in products} != PRODUCTS:
        raise ValueError("Profile requires exactly datasystem, mooncake and lmcache")
    if products != json.loads((baseline / "products.json").read_text()):
        raise RuntimeError("Profile product configuration differs from the original formal suite")
    files = {root / "benchmark.py", root / "cgroup.py", root / "profile_put.py", root / "light_monitor.py", manifest}
    for product in products:
        files.update(Path(product[key]) for key in ["adapter", "config", "python"])
        for action in ["start", "stop"]:
            if not product[action]:
                raise ValueError("Product lifecycle command is empty: " + product["name"])
            files.update(Path(token) for token in product[action] if "{" not in token and Path(token).is_absolute() and Path(token).is_file())
        adapter = Path(product["adapter"])
        files.update(adapter.parent.glob("*.py")); files.update(adapter.parent.glob("*.sh"))
    hashes = {}
    for path in sorted(files):
        if not path.is_absolute():
            raise ValueError("Profile execution inputs must have absolute paths: " + str(path))
        if not path.is_file():
            raise FileNotFoundError("Missing profile input: " + str(path))
        hashes[str(path)] = {"sha256": digest(path), "resolved_path": str(path.resolve()), "bytes": path.stat().st_size}
    return products, frozen, hashes


def run(args):
    root, baseline, output, cg = [Path(path).resolve() for path in [args.root, args.baseline_suite, args.out, args.cgroup_root]]
    manifest = Path(args.manifest).resolve() if args.manifest else root / "products.json"
    if not root.is_dir() or not baseline.is_dir():
        raise ValueError("Root and baseline suite must exist")
    if output == baseline or output in baseline.parents or baseline in output.parents:
        raise ValueError("Supplemental output must be separate from the main suite")
    lock = os.open(root / "suite.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        initial_drain = drain(cg, timeout=0)
        products, frozen, hashes = inputs(root, manifest, baseline)
        output.mkdir(parents=True, exist_ok=False)
        parameters = {"root": str(root), "manifest": str(manifest), "baseline_suite": str(baseline), "output": str(output),
                      "cgroup_root": str(cg), "purpose": PURPOSE, "included_in_main_performance_repeats": False,
                      "sizes": SIZES, "concurrencies": [8], "repeats": 1, "operations": ["put"],
                      "duration": 20, "warmup": 3, "preload_bytes": 64 << 20, "settle_s": 2,
                      "total_bytes_rule": "min(2GiB, size*65536)", "requested_monitor_interval_s": 0.01,
                      "frozen_main_script_hashes": frozen, "initial_cgroup_drain": initial_drain,
                      "resource_scope": "Supplemental PUT SDK resource windows only; native QPS/P99 are retained as evidence and excluded from main performance aggregation."}
        plan = [{"repeat": 0, "product": product["name"], "size": size, "concurrency": 8} for product in products for size in SIZES]
        random.Random(20261020).shuffle(plan)
        for name, value in [("parameters.json", parameters), ("products.json", products), ("plan.json", plan), ("script-hashes.json", hashes)]:
            atomic_json(output / name, value)
        summary = {"started": utc(), "purpose": PURPOSE, "included_in_main_performance_repeats": False,
                   "completed": [], "failed": [], "planned_cells": 9}
        atomic_json(output / "summary.json", summary)
        monitor = None
        interrupted = False
        previous_handlers = {}
        def interrupt(*_):
            nonlocal interrupted
            interrupted = True
            raise KeyboardInterrupt()
        for number in [signal.SIGINT, signal.SIGTERM]:
            previous_handlers[number] = signal.signal(number, interrupt)
        with (output / "events.jsonl").open("x", buffering=1) as events, (output / "monitor.log").open("x") as monitor_log:
            def event(kind, **values):
                row = {"utc": utc(), "wall_ns": time.time_ns(), "mono_ns": time.monotonic_ns(), "event": kind, "purpose": PURPOSE, **values}
                events.write(json.dumps(row) + "\n")
                atomic_json(output / "status.json", row)
            def execute(command, log, env, stage, cell, timeout=600, role=None):
                event("command_start", cell=cell, stage=stage, argv=command, cgroup=role)
                with log.open("a") as stream:
                    process = subprocess.Popen(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                               start_new_session=True, preexec_fn=placement(cg / role) if role else None)
                    try:
                        process.wait(timeout)
                    except BaseException:
                        stop_process(process)
                        raise
                event("command_end", cell=cell, stage=stage, returncode=process.returncode)
                if process.returncode:
                    raise RuntimeError("Command failed rc=" + str(process.returncode) + ": " + shlex.join(command))
            try:
                command = [sys.executable, str(root / "light_monitor.py"), "--out", str(output / "resources"), "--cgroup-root", str(cg), "--interval", "0.01", "--include-monitor"]
                event("monitor_start", argv=command)
                monitor = subprocess.Popen(command, cwd=root, stdout=monitor_log, stderr=subprocess.STDOUT,
                                           start_new_session=True, preexec_fn=placement(cg / "monitor"))
                deadline = time.monotonic() + 5
                while not (output / "resources/samples.jsonl").exists():
                    if monitor.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Light monitor failed to become ready")
                    time.sleep(0.01)
                event("monitor_ready", pid=monitor.pid)
                by_name = {row["name"]: row for row in products}
                for ordinal, planned in enumerate(plan, 1):
                    if monitor.poll() is not None:
                        raise RuntimeError("Light monitor exited before all cells completed")
                    for name, expected in frozen.items():
                        if digest(root / name) != expected:
                            raise RuntimeError("Frozen input changed during profile: " + name)
                    product, size = by_name[planned["product"]], planned["size"]
                    identity = f"r0-{product['name']}-s{size}-c8"
                    cell = output / "cells" / identity
                    cell.mkdir(parents=True)
                    variables = {"run_dir": str(root / "run" / ("put-profile-" + identity + "-" + str(time.time_ns()))), "server_logs": str(cell / "server-logs")}
                    variables["runtime_dir"] = variables["run_dir"]
                    start, stop = [[token.format(**variables) for token in product[action]] for action in ["start", "stop"]]
                    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONHASHSEED="0",
                               **{key: value.format(**variables) for key, value in product.get("env", {}).items()})
                    command = [product["python"], str(root / "benchmark.py"), "--adapter", product["adapter"], "--config", product["config"],
                               "--op", "put", "--size", str(size), "--concurrency", "8", "--duration", "20", "--warmup", "3",
                               "--total-bytes", str(min(2 << 30, size * 65536)), "--preload-bytes", str(64 << 20),
                               "--run-id", f"r0-s{size}-c8", "--out", str(cell / "put.json")]
                    atomic_json(cell / "parameters.json", {**planned, "purpose": PURPOSE, "argv": command, "start": start, "stop": stop, "env_overrides": {key: env[key] for key in product.get("env", {})}})
                    event("cell_start", cell=identity, number=ordinal, total=9)
                    try:
                        execute(stop, cell / "lifecycle.log", env, "pre_stop", identity)
                        event("cgroup_drain", cell=identity, stage="pre_start", result=drain(cg, ["server", "client"]))
                        execute(start, cell / "lifecycle.log", env, "start", identity, timeout=180, role="server")
                        event("settle_start", cell=identity, seconds=2)
                        time.sleep(2)
                        event("measure_prepare", cell=identity, operation="put", command=command)
                        execute(command, cell / "put.log", env, "put", identity, timeout=900, role="client")
                        result = json.loads((cell / "put.json").read_text())
                        if not result.get("valid"):
                            raise RuntimeError("Invalid supplemental PUT result")
                        if monitor.poll() is not None:
                            raise RuntimeError("Light monitor exited during the supplemental PUT cell")
                        event("measure_complete", cell=identity, operation="put", duration_s=result.get("duration_s"), success=result.get("success"))
                        summary["completed"].append(identity)
                        event("cell_complete", cell=identity)
                    except Exception:
                        failure = {"cell": identity, "error": traceback.format_exc()}
                        summary["failed"].append(failure)
                        atomic_json(cell / "failure.json", failure)
                        event("cell_failed", **failure)
                    finally:
                        stop_error = None
                        try:
                            execute(stop, cell / "lifecycle.log", env, "stop", identity)
                        except BaseException:
                            stop_error = traceback.format_exc()
                            event("stop_failed", cell=identity, error=stop_error)
                        event("cgroup_drain", cell=identity, stage="after_stop", result=drain(cg, ["server", "client"]))
                        if stop_error:
                            raise RuntimeError("Product stop failed: " + stop_error)
                    atomic_json(output / "summary.json", summary)
                event("suite_complete", completed=len(summary["completed"]), failed=len(summary["failed"]))
            except BaseException:
                summary["fatal"] = traceback.format_exc()
                event("suite_interrupted" if interrupted else "suite_fatal", error=summary["fatal"])
            finally:
                if monitor is not None:
                    event("monitor_stop", pid=monitor.pid)
                    if monitor.poll() is None:
                        monitor.send_signal(signal.SIGINT)
                        try:
                            monitor.wait(10)
                        except subprocess.TimeoutExpired:
                            stop_process(monitor)
                    summary["monitor_exit"] = monitor.returncode
                    if monitor.returncode:
                        summary["monitor_error"] = "Light monitor exited nonzero; inspect monitor.log and monitor-manifest.json"
                    try:
                        summary["monitor_observation"] = json.loads((output / "resources/monitor-manifest.json").read_text())
                        times = [json.loads(line)["mono_ns"] for line in (output / "resources/samples.jsonl").read_text().splitlines()]
                        coverage = []
                        for result_path in sorted((output / "cells").glob("*/put.json")):
                            raw = json.loads(result_path.read_text())
                            start, end = raw.get("measure_start_mono_ns"), raw.get("measure_end_mono_ns")
                            inside = [stamp for stamp in times if start is not None and end is not None and start <= stamp <= end]
                            coverage.append({"cell": result_path.parent.name, "samples_inside_sdk_window": len(inside),
                                             "observed_span_s": (inside[-1] - inside[0]) / 1e9 if len(inside) >= 2 else None})
                        summary["resource_window_coverage"] = coverage
                        if len(coverage) != 9 or any(row["samples_inside_sdk_window"] < 2 for row in coverage):
                            summary["resource_coverage_error"] = "Supplemental resource windows require at least two actual samples per PUT cell; no performance result was rewritten."
                    except (OSError, ValueError):
                        summary["monitor_error"] = traceback.format_exc()
                try:
                    summary["final_cgroup_drain"] = drain(cg)
                    event("cgroup_drain", stage="final", result=summary["final_cgroup_drain"])
                except Exception:
                    summary["drain_error"] = traceback.format_exc()
                    event("drain_failed", error=summary["drain_error"])
                summary["ended"] = utc()
                summary["valid"] = len(summary["completed"]) == 9 and not any(summary.get(key) for key in ["failed", "fatal", "monitor_error", "drain_error", "resource_coverage_error"])
                atomic_json(output / "summary.json", summary)
                for number, previous in previous_handlers.items():
                    signal.signal(number, previous)
        return 0 if summary["valid"] else 1
    finally:
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path("/home/worktrees/kvc-fair-20261020"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--baseline-suite", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    args = parser.parse_args()
    if args.out is None:
        args.out = args.root / "put-resource-profile-001"
    try:
        raise SystemExit(run(args))
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "purpose": PURPOSE}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
