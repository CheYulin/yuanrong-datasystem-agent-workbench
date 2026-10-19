#!/usr/bin/env python3
"""Serialize product/cell execution with immutable evidence and resumable results."""
import argparse
import datetime
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import time
import traceback


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/home/worktrees/kvc-fair-20261020"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--warmup", type=float, default=3)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1024, 65536, 1048576])
    parser.add_argument("--concurrencies", type=int, nargs="+", default=[2, 8, 16])
    parser.add_argument("--products", nargs="+")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.root / "suite.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    products = json.loads(args.manifest.read_text())["products"]
    if args.products:
        products = [p for p in products if p["name"] in args.products]
    if not products:
        raise RuntimeError("no product selected")
    (args.output / "products.json").write_text(json.dumps(products, indent=2))
    (args.output / "parameters.json").write_text(json.dumps({k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()}, indent=2))
    log = (args.output / "events.jsonl").open("a", buffering=1)
    def event(kind, **values):
        row = {"utc": utc(), "wall_ns": time.time_ns(), "mono_ns": time.monotonic_ns(), "event": kind, **values}
        log.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)
        (args.output / "status.json").write_text(json.dumps(row, indent=2))
    def execute(command, path, env=None, timeout=600):
        with path.open("a") as out:
            proc = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            try:
                proc.wait(timeout)
            except BaseException:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise
        if proc.returncode:
            raise RuntimeError("command failed rc=" + str(proc.returncode) + ", log=" + str(path))
    hashes = {str(p.relative_to(args.root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.root.glob("*.py")}
    (args.output / "script-hashes.json").write_text(json.dumps(hashes, indent=2))
    monitor_cmd = ["python3", str(args.root / "cgroup.py"), "--group", "monitor", "exec", "--", "python3", str(args.root / "monitor.py"), "--out", str(args.output / "resources")]
    monitor_log = (args.output / "monitor.log").open("a")
    monitor = subprocess.Popen(monitor_cmd, stdout=monitor_log, stderr=subprocess.STDOUT)
    interrupted = False
    def interrupt(*_):
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    summary = {"started": utc(), "completed": [], "failed": []}
    try:
        plan = []
        for repeat in range(args.repeats):
            order = list(products)
            random.Random(20261020 + repeat).shuffle(order)
            cells = list(itertools.product(args.sizes, args.concurrencies))
            random.Random(20261020 + 100 + repeat).shuffle(cells)
            for product in order:
                for size, concurrency in cells:
                    plan.append((repeat, product, size, concurrency))
        (args.output / "plan.json").write_text(json.dumps([{"repeat": r,"product":p["name"],"size":s,"concurrency":c} for r,p,s,c in plan], indent=2))
        for number, (repeat, product, size, concurrency) in enumerate(plan):
            cell_id = f"r{repeat}-{product['name']}-s{size}-c{concurrency}"
            cell = args.output / "cells" / cell_id
            cell.mkdir(parents=True, exist_ok=True)
            ops = ["put", "get"] + (["exist_hit", "exist_miss"] if size == min(args.sizes) else [])
            if all((cell / (op + ".json")).exists() and json.loads((cell / (op + ".json")).read_text()).get("valid") for op in ops):
                summary["completed"].append(cell_id)
                event("resume_skip", cell=cell_id)
                continue
            attempt = str(time.time_ns())[-10:]
            variables = {"run_dir": str(args.root / "run" / f"{product['name']}-{number:03}-{attempt}"), "server_logs": str(cell / "server-logs")}
            variables["runtime_dir"] = variables["run_dir"]
            start_command = [value.format(**variables) for value in product["start"]]
            stop_command = [value.format(**variables) for value in product["stop"]]
            env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONHASHSEED="0", **{k:v.format(**variables) for k,v in product.get("env", {}).items()})
            event("cell_start", cell=cell_id, number=number + 1, total=len(plan))
            try:
                execute(stop_command, cell / "lifecycle.log", env)
                execute(start_command, cell / "lifecycle.log", env, 180)
                time.sleep(2)
                for op in ops:
                    result_path = cell / (op + ".json")
                    if result_path.exists():
                        old = result_path.with_name(op + ".previous." + str(time.time_ns()) + ".json")
                        result_path.rename(old)
                    total_bytes = min(2 << 30, size * 65536)
                    if args.smoke:
                        total_bytes = max(size * concurrency * 4, 1 << 20)
                    run_id = f"r{repeat}-s{size}-c{concurrency}"
                    command = ["python3", str(args.root / "cgroup.py"), "--group", "client", "exec", "--", product["python"], str(args.root / "benchmark.py"), "--adapter", product["adapter"], "--config", product["config"], "--op", op, "--size", str(size), "--concurrency", str(concurrency), "--duration", str(args.duration), "--warmup", str(args.warmup), "--total-bytes", str(total_bytes), "--preload-bytes", str(1 << 20 if args.smoke else 64 << 20), "--run-id", run_id, "--out", str(result_path)]
                    if op.startswith("exist"):
                        command.append("--skip-preload")
                    event("measure_prepare", cell=cell_id, operation=op, command=command)
                    execute(command, cell / (op + ".log"), env, 900)
                    result = json.loads(result_path.read_text())
                    if not result.get("valid"):
                        raise RuntimeError("invalid benchmark result " + str(result_path))
                    event("measure_complete", cell=cell_id, operation=op, qps=result["qps"], duration_s=result["duration_s"], success=result["success"])
                summary["completed"].append(cell_id)
                event("cell_complete", cell=cell_id)
            except Exception:
                failure = {"cell": cell_id, "error": traceback.format_exc()}
                summary["failed"].append(failure)
                (cell / "failure.json").write_text(json.dumps(failure, indent=2))
                event("cell_failed", **failure)
            finally:
                try:
                    execute(stop_command, cell / "lifecycle.log", env)
                except Exception:
                    event("stop_failed", cell=cell_id, error=traceback.format_exc())
                    raise
                for group in ["server", "client"]:
                    deadline = time.monotonic() + 3
                    while True:
                        pids = Path("/sys/fs/cgroup/kvcfair20261020", group, "cgroup.procs").read_text().strip()
                        if not pids or time.monotonic() >= deadline:
                            break
                        time.sleep(0.1)
                    if pids:
                        raise RuntimeError("Task cgroup contains leftover processes: " + group + ": " + pids)
            (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
        event("suite_complete", completed=len(summary["completed"]), failed=len(summary["failed"]))
    except BaseException:
        summary["fatal"] = traceback.format_exc()
        event("suite_interrupted" if interrupted else "suite_fatal", error=summary["fatal"])
    finally:
        monitor.send_signal(signal.SIGINT)
        try:
            monitor.wait(15)
        except subprocess.TimeoutExpired:
            monitor.terminate(); monitor.wait(10)
        monitor_log.close()
        summary["ended"] = utc()
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    raise SystemExit(1 if summary.get("failed") or summary.get("fatal") else 0)


if __name__ == "__main__":
    main()
