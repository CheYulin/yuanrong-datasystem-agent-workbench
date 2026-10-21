#!/usr/bin/env python3
"""Run the pure-KV deadline diagnostic under the shared experiment lock."""

import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in {None, ""}:
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

from kvcbench.diagnostics.put import atomic_json, digest, drain, stop_process


def run(args):
    root, out = args.root.resolve(strict=True), args.out.absolute()
    stage = json.loads((root / "batch-stage.json").read_text())
    if stage.get("feature_profile") != "pure-kv":
        raise ValueError("The frozen pure-KV stage is required")
    for name, expected in stage["inputs"].items():
        if digest(root / name) != expected["sha256"]:
            raise ValueError("Frozen diagnostic input changed: " + name)
        if "mode" in expected and (root / name).stat().st_mode & 0o777 != expected["mode"]:
            raise ValueError("Frozen diagnostic permissions changed: " + name)
    run_dir = root / "run/diagnostic-purekv"
    if run_dir.exists() or run_dir.is_symlink():
        raise FileExistsError("Preserve the existing diagnostic service directory")
    out.mkdir(parents=True, exist_ok=False)
    product = next(item for item in json.loads((root / "products.json").read_text())["products"] if item["name"] == "datasystem")
    variables = {"run_dir": str(run_dir), "runtime_dir": str(run_dir), "server_logs": str(out / "server-logs")}
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONHASHSEED="0",
               **{key: value.format(**variables) for key, value in product.get("env", {}).items()})
    start = [item.format(**variables) for item in product["start"]]
    stop = [item.format(**variables) for item in product["stop"]]
    ledger = {"benchmark_eligible": False, "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(),
              "run_dir": str(run_dir), "commands": [], "errors": [], "diagnostic_complete": False}

    def execute(argv, timeout=180):
        record = {"argv": argv, "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(), "returncode": None}
        ledger["commands"].append(record)
        try:
            with (out / "lifecycle.log").open("a") as stream:
                process = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, env=env, start_new_session=True)
                try:
                    record["returncode"] = process.wait(timeout)
                except BaseException:
                    stop_process(process)
                    raise
            if record["returncode"] != 0:
                raise RuntimeError("Diagnostic command failed: " + repr(record))
        finally:
            record.update(end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
            record["duration_s"] = (record["end_mono_ns"] - record["start_mono_ns"]) / 1e9
            atomic_json(out / "status.json", ledger)

    monitor, monitor_stream, owned = None, None, False
    try:
        ledger["before"] = drain(Path("/sys/fs/cgroup/kvcfair20261020"))
        owned = True
        execute(stop)
        monitor_stream = (out / "monitor.log").open("x")
        monitor = subprocess.Popen(["python3", str(root / "cgroup.py"), "--group", "monitor", "exec", "--",
                                    "python3", str(root / "monitor.py"), "--interval", ".2", "--out", str(out / "resources")],
                                   stdout=monitor_stream, stderr=subprocess.STDOUT, start_new_session=True)
        execute(start)
        time.sleep(2)
        execute(["python3", str(root / "cgroup.py"), "--group", "client", "exec", "--", product["python"],
                 str(root / "batch_ds_deadline_diagnostic.py"), "--root", str(root), "--run-dir", str(run_dir),
                 "--out", str(out / "sdk"), "--trace"], timeout=180)
        result = json.loads((out / "sdk/summary.json").read_text())
        ledger["diagnostic_complete"] = result.get("diagnostic_complete") is True
        ledger["all_sdk_steps_passed"] = result.get("all_sdk_steps_passed")
    except BaseException:
        ledger["errors"].append(traceback.format_exc())
    finally:
        if owned:
            try:
                execute(stop)
            except BaseException:
                ledger["errors"].append(traceback.format_exc())
        try:
            if monitor is not None:
                if monitor.poll() is None:
                    monitor.send_signal(signal.SIGINT)
                    try:
                        monitor.wait(30)
                    except subprocess.TimeoutExpired:
                        stop_process(monitor)
                ledger["monitor_exit"] = monitor.returncode
        except BaseException:
            ledger["errors"].append(traceback.format_exc())
        finally:
            if monitor_stream is not None:
                try:
                    monitor_stream.close()
                except BaseException:
                    ledger["errors"].append(traceback.format_exc())
        try:
            ledger["after"] = drain(Path("/sys/fs/cgroup/kvcfair20261020"))
        except BaseException:
            ledger["errors"].append(traceback.format_exc())
        ledger.update(end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
        ledger["duration_s"] = (ledger["end_mono_ns"] - ledger["start_mono_ns"]) / 1e9
        atomic_json(out / "summary.json", ledger)
    print(json.dumps(ledger))
    return 0 if ledger["diagnostic_complete"] and not ledger["errors"] and ledger.get("monitor_exit") == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with (args.root / "suite.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise SystemExit(run(args))


if __name__ == "__main__":
    main()
