#!/usr/bin/env python3
"""Isolate rc44 GET/EXIST deadline lifetime; correctness diagnostics, never performance."""

import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback
import uuid


CASES = {"A": "EXIST-only", "B": "GET-then-EXIST", "C": "GET-sleep-B16-EXIST",
         "D": "GET-sleep-B1-EXIST"}
REQUIRED_FLAGS = {"rocksdb_write_mode": "none", "sc_regular_socket_num": "0",
                  "sc_stream_socket_num": "0", "ipc_through_shared_memory": "false",
                  "enable_rdma": "false", "enable_urma": "false"}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def save(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def load_adapter(path, config):
    spec = importlib.util.spec_from_file_location("_ds_deadline_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.new_adapter(config)


def process_snapshot():
    fds = {}
    for path in sorted(Path("/proc/self/fd").iterdir()):
        try:
            fds[path.name] = os.readlink(path)
        except OSError as error:
            fds[path.name] = {"unobserved": type(error).__name__}
    maps = Path("/proc/self/maps").read_text()
    return {"pid": os.getpid(), "fds": fds, "maps": maps,
            "maps_sha256": hashlib.sha256(maps.encode()).hexdigest()}


def step(rows, name, function):
    row = {"name": name, "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns()}
    rows.append(row)
    try:
        row["result"] = function()
        row["status"] = "passed"
        return True
    except Exception as error:
        row.update(status="failed", error={"type": type(error).__name__, "message": str(error),
                                           "traceback": traceback.format_exc()})
        return False
    finally:
        row.update(end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
        row["duration_s"] = (row["end_mono_ns"] - row["start_mono_ns"]) / 1e9


def require_equal(observed, expected):
    if observed != expected:
        raise AssertionError("Native SDK returned incorrect bytes or EXISTS vector")
    return {"exact": True, "returned_keys": len(observed)}


def run_case(adapter_path, config, case, keys, duration, sleep_s, interval, factory=load_adapter):
    values = [hashlib.shake_256(("ds-deadline-v1\0" + key).encode()).digest(1024) for key in keys]
    rows, adapter = [], None
    result = {"case": case, "description": CASES[case], "benchmark_eligible": False,
              "keys": keys, "key_payload_sha256": {key: hashlib.sha256(value).hexdigest()
                                                     for key, value in zip(keys, values)},
              "parameters": {"batch": 1 if case == "D" else 16, "value_size": 1024,
                             "duration_s": duration if case in ["A", "B"] else None,
                             "sleep_s": sleep_s if case in ["C", "D"] else 0,
                             "call_interval_s": interval, "config": config},
              "steps": rows, "before_sdk": process_snapshot(), "started_mono_ns": time.monotonic_ns()}
    try:
        adapter = factory(adapter_path, config)
        metadata = adapter.metadata() if callable(adapter.metadata) else adapter.metadata
        result["metadata"] = metadata
        if not step(rows, "put-initial", lambda: adapter.put_batch(keys, values)):
            return result
        if case != "A" and not step(rows, "get-full", lambda: require_equal(adapter.get_batch(keys), values)):
            return result
        if case in ["C", "D"]:
            step(rows, "sleep", lambda: time.sleep(sleep_s))
        selected = keys[:1] if case == "D" else keys
        start = time.monotonic_ns()
        deadline = start + int(duration * 1e9)
        while True:
            if not step(rows, "exists", lambda: require_equal(adapter.exists_batch(selected), [True] * len(selected))):
                break
            if case in ["C", "D"] or time.monotonic_ns() >= deadline:
                break
            time.sleep(interval)
        result["exist_elapsed_s"] = (time.monotonic_ns() - start) / 1e9
    except Exception as error:
        result["setup_error"] = {"type": type(error).__name__, "message": str(error),
                                 "traceback": traceback.format_exc()}
    finally:
        result["before_close"] = process_snapshot()
        if adapter is not None:
            step(rows, "close", adapter.close)
            adapter = None
        gc.collect()
        result["after_close"] = process_snapshot()
        result["ended_mono_ns"] = time.monotonic_ns()
        result["complete"] = not result.get("setup_error") and any(row["name"] == "exists" for row in rows)
        result["all_sdk_steps_passed"] = result["complete"] and all(row["status"] == "passed" for row in rows)
    return result


def live_evidence(run_dir):
    manifest_path = Path(run_dir) / "processes.json"
    manifest = json.loads(manifest_path.read_text())
    records = []
    for index in range(2):
        worker = next(row for row in manifest["processes"] if row["name"] == f"worker{index}")
        path = Path(run_dir) / f"w{index}/worker.flags"
        flags = dict(line[2:].split("=", 1) for line in path.read_text().splitlines() if line.startswith("--"))
        if any(flags.get(key) != value for key, value in REQUIRED_FLAGS.items()):
            raise ValueError("Diagnostic requires the explicit pure-KV TCP worker profile")
        pid = worker["pid"]
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        command = Path(f"/proc/{pid}/cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
        if stat[19] != worker["start_ticks"] or command != worker["command"]:
            raise ValueError("Worker PID identity or command differs from launch evidence")
        directory = Path(flags["rocksdb_store_dir"])
        files = [{"path": str(item), "bytes": item.stat().st_size, "sha256": sha(item)}
                 for item in sorted(directory.rglob("*")) if item.is_file() and not item.is_symlink()]
        artifacts = [row for row in files if re.fullmatch(r"(?:LOCK|CURRENT|IDENTITY|MANIFEST-.*|OPTIONS-.*|.*\.(?:sst|ldb)|[0-9]+\.log)",
                                                        Path(row["path"]).name)]
        records.append({"worker_index": index, "pid": pid, "start_ticks": stat[19], "command": command,
                        "flags_path": str(path), "flags_sha256": sha(path), "flags": flags,
                        "rocksdb_files": files, "rocksdb_database_artifacts": artifacts,
                        "stream_scope": "Explicit disabled flags and pinned conditional-init source; no service introspection"})
    return {"manifest_path": str(manifest_path), "manifest_sha256": sha(manifest_path), "workers": records}


def wire_endpoints(directory, steps):
    windows = [row for row in steps if row["name"] == "exists"]
    observed = set()
    matched = 0
    for path in sorted(directory.glob("strace.*")):
        with path.open(errors="replace") as stream:
            for line in stream:
                stamp = re.match(r"(\d+)\.(\d+)\s+(?:write|writev|sendto|sendmsg)\(", line)
                if not stamp or not re.search(r"=\s*[1-9][0-9]*(?:\s|$)", line):
                    continue
                ns = int(stamp[1]) * 10**9 + int(stamp[2].ljust(9, "0")[:9])
                if not any(row["start_wall_ns"] <= ns <= row["end_wall_ns"] for row in windows):
                    continue
                tcp = re.search(r"<TCP:\[127\.0\.0\.1:\d+->(127\.0\.0\.1:\d+)\]>", line)
                if tcp:
                    observed.add(tcp[1])
                    matched += 1
    return {"outgoing_tcp_endpoints": sorted(observed), "successful_send_lines": matched,
            "scope": "Positive outgoing TCP syscalls in recorded EXISTS windows, not protobuf-method decoding or continuous tracing"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--duration", type=float, default=13)
    parser.add_argument("--sleep", type=float, default=10.1)
    parser.add_argument("--interval", type=float, default=.05)
    parser.add_argument("--client-index", type=int, choices=[0, 1], default=0)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--child", choices=CASES)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    if args.duration < 12 or args.sleep <= 10 or args.interval <= 0:
        parser.error("Require duration>=12, sleep>10 and positive rate-limit interval; SDK timeout remains unchanged")
    config_path = args.config or args.root / "configs/datasystem.json"
    adapter_path = args.adapter or args.root / "batch_datasystem.py"
    config = json.loads(config_path.read_text())
    if int(config.get("timeout_ms", 10000)) != 10000:
        raise ValueError("Diagnostic requires the unchanged 10000ms SDK request timeout")
    config.update(value_size=1024, client_index=args.client_index)
    run_id = args.run_id or ("diag" + uuid.uuid4().hex[:12])
    keys = [f"{run_id}-key-{index:02d}" for index in range(16)]
    if args.child:
        config["log_dir"] = str(args.out.parent / (args.child + "-sdk-logs"))
        Path(config["log_dir"]).mkdir(exist_ok=False)
        save(args.out, run_case(adapter_path, config, args.child, keys, args.duration, args.sleep, args.interval))
        return 0
    args.out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic_ns()
    summary = {"schema_version": 1, "benchmark_eligible": False, "action": "deadline_lifetime_diagnostic",
               "run_id": run_id, "helper_sha256": sha(__file__), "adapter_sha256": sha(adapter_path),
               "config_sha256": sha(config_path), "start_wall_ns": time.time_ns(), "cases": [],
               "before": live_evidence(args.run_dir),
               "scope": "Four fresh SDK processes, same sixteen key/payload pairs, rate-limited calls; no product start/stop or product modification. Observations cannot establish performance ranking or prove TLS contents."}
    save(args.out / "executed-inputs.json", {"helper": {"path": str(Path(__file__).resolve()), "sha256": sha(__file__)},
                                             "adapter": {"path": str(adapter_path), "sha256": sha(adapter_path)},
                                             "config": {"path": str(config_path), "sha256": sha(config_path)}})
    (args.out / "executed-helper.py").write_bytes(Path(__file__).read_bytes())
    try:
        for case in CASES:
            child_out = args.out / f"case-{case}.json"
            command = [sys.executable, str(Path(__file__).resolve()), "--root", str(args.root), "--run-dir", str(args.run_dir),
                       "--out", str(child_out), "--config", str(config_path), "--adapter", str(adapter_path), "--child", case,
                       "--run-id", run_id, "--duration", str(args.duration), "--sleep", str(args.sleep),
                       "--interval", str(args.interval), "--client-index", str(args.client_index)]
            wire = args.out / f"wire-{case}"
            if args.trace:
                wire.mkdir()
                command = ["strace", "-ff", "-ttt", "-T", "-yy", "-s", "128", "-e",
                           "trace=network,read,write,readv,writev", "-o", str(wire / "strace"), *command]
            record = {"case": case, "command": command, "start_mono_ns": time.monotonic_ns(), "start_wall_ns": time.time_ns()}
            with (args.out / f"case-{case}.stdout.log").open("x") as stream:
                proc = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    record["returncode"] = proc.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    record["timed_out"] = True
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=5)
                    record["returncode"] = proc.returncode
                except BaseException:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait(timeout=5)
                    raise
                finally:
                    record.update(end_mono_ns=time.monotonic_ns(), end_wall_ns=time.time_ns(), reaped=proc.poll() is not None)
            if child_out.exists():
                child = json.loads(child_out.read_text())
                record.update(child_path=str(child_out), child_sha256=sha(child_out), complete=child["complete"],
                              all_sdk_steps_passed=child["all_sdk_steps_passed"])
                if args.trace:
                    record["wire"] = wire_endpoints(wire, child["steps"])
            summary["cases"].append(record)
            save(args.out / f"receipt-{case}.json", record)
    finally:
        summary.update(end_wall_ns=time.time_ns(), duration_s=(time.monotonic_ns() - started) / 1e9)
        try:
            summary["after"] = live_evidence(args.run_dir)
        except Exception as error:
            summary["after_error"] = {"type": type(error).__name__, "message": str(error)}
        summary["diagnostic_complete"] = len(summary["cases"]) == 4 and all(
            row.get("returncode") == 0 and row.get("complete") and row.get("reaped") for row in summary["cases"])
        summary["all_sdk_steps_passed"] = summary["diagnostic_complete"] and all(
            row["all_sdk_steps_passed"] for row in summary["cases"])
        summary["rocksdb_artifacts_absent"] = (all(not worker["rocksdb_database_artifacts"]
                                                   for phase in ["before", "after"] for worker in summary[phase]["workers"])
                                               if "after" in summary else None)
        summary["artifacts"] = [{"path": str(path.relative_to(args.out)), "bytes": path.stat().st_size, "sha256": sha(path)}
                                for path in sorted(args.out.rglob("*")) if path.is_file() and not path.is_symlink()]
        save(args.out / "summary.json", summary)
    print(json.dumps({key: summary[key] for key in ["diagnostic_complete", "all_sdk_steps_passed", "duration_s"]}))
    return 0 if summary["diagnostic_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
