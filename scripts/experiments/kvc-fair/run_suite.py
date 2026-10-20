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
import shutil
import signal
import subprocess
import tempfile
import time
import traceback


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def plan_cells(args, products):
    plan = []
    for repeat in range(args.repeats):
        order = list(products)
        random.Random(20261020 + repeat).shuffle(order)
        cells = list(itertools.product(args.sizes, args.concurrencies))
        random.Random(20261020 + 100 + repeat).shuffle(cells)
        for product in order:
            for size, concurrency in cells:
                plan.append((repeat, product, size, concurrency))
    return plan


def operations(args, size):
    return ["put", "get"] + (["exist_hit", "exist_miss"] if size == min(args.sizes) else [])


def measurement_parameters(args, product, repeat, size, concurrency, op, result_path):
    total_bytes = min(2 << 30, size * 65536)
    if args.smoke:
        total_bytes = max(size * concurrency * 4, 1 << 20)
    return {"adapter": product["adapter"], "config": product["config"], "op": op,
            "size": size, "concurrency": concurrency, "duration": args.duration,
            "warmup": args.warmup, "total_bytes": total_bytes,
            "preload_bytes": 1 << 20 if args.smoke else 64 << 20,
            "skip_preload": op.startswith("exist"),
            "run_id": f"r{repeat}-s{size}-c{concurrency}", "out": str(result_path)}


def provenance(args, products):
    parameters = {k: str(v.resolve()) if isinstance(v, Path) else v for k, v in vars(args).items()}
    files = {Path(__file__).resolve(), *(args.root / name for name in ["benchmark.py", "cgroup.py", "monitor.py"])}
    helpers = {"mooncake": ["env.sh", "launch_master.sh", "peer.py", "process_identity.py"],
               "lmcache": ["start_server.sh"]}
    for product in products:
        files.update(Path(product[key]).absolute() for key in ["adapter", "config", "python"])
        for action in ["start", "stop"]:
            command = product[action]
            executable = shutil.which(command[0])
            if executable:
                files.add(Path(executable).absolute())
            for token in command:
                if "{" not in token and (Path(token).is_absolute() or token.endswith((".py", ".sh"))):
                    candidate = Path(token).absolute()
                    if candidate.is_file() or token.endswith((".py", ".sh")):
                        files.add(candidate)
        for name in helpers.get(product["name"], []):
            files.add(args.root / product["name"] / name)
    inventory = {}
    for path in sorted(files):
        if not path.is_file():
            raise RuntimeError("Missing execution input for resume provenance: " + str(path))
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 << 20), b""):
                digest.update(chunk)
        inventory[str(path)] = {"resolved_path": str(path.resolve()), "sha256": digest.hexdigest(),
                                "size": path.stat().st_size}
    return {"schema_version": 1, "parameters": parameters, "products": products, "files": inventory}


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".tmp-", delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def validate_resume(args, products, plan):
    expected = provenance(args, products)
    saved_path = args.output / "resume-provenance.json"
    legacy_paths = [args.output / name for name in ["products.json", "parameters.json", "script-hashes.json",
                    "plan.json", "summary.json", "events.jsonl", "resources"]]
    if saved_path.exists():
        saved = json.loads(saved_path.read_text())
        if {k: v for k, v in saved.items() if k != "created_utc"} != expected:
            raise RuntimeError("Resume provenance mismatch; preserve the original output and use a new suite directory")
    elif any(p.exists() for p in legacy_paths) or any((args.output / "cells").glob("*/*.json")):
        raise RuntimeError("Legacy suite has evidence but no resume-provenance.json; automatic resume is refused. "
                           "Preserve the evidence and independently verify parameters and hashes with the original driver")
    metadata = {"products.json": products,
                "parameters.json": expected["parameters"],
                "script-hashes.json": {str(p.relative_to(args.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in [*args.root.glob("*.py"), *(args.root / "kvcbench").rglob("*.py")]},
                "plan.json": [{"repeat": r, "product": p["name"], "size": s, "concurrency": c} for r, p, s, c in plan]}
    for name, value in metadata.items():
        path = args.output / name
        if path.exists() and json.loads(path.read_text()) != value:
            raise RuntimeError("Resume metadata mismatch: " + str(path) + "; original metadata was not replaced")
    for repeat, product, size, concurrency in plan:
        cell = args.output / "cells" / f"r{repeat}-{product['name']}-s{size}-c{concurrency}"
        for op in operations(args, size):
            path = cell / (op + ".json")
            if path.exists():
                raw = json.loads(path.read_text())
                if raw.get("valid") and raw.get("parameters") != measurement_parameters(args, product, repeat, size, concurrency, op, path):
                    raise RuntimeError("Valid result parameters do not match the requested workload: " + str(path))
    if not saved_path.exists():
        write_json(saved_path, dict(expected, created_utc=utc()))
    for name, value in metadata.items():
        path = args.output / name
        if not path.exists():
            write_json(path, value)


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
    args.root = args.root.resolve()
    args.output = args.output.resolve()
    args.manifest = args.manifest.resolve()
    lock = (args.root / "suite.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    products = json.loads(args.manifest.read_text())["products"]
    if args.products:
        products = [p for p in products if p["name"] in args.products]
    if not products:
        raise RuntimeError("no product selected")
    plan = plan_cells(args, products)
    validate_resume(args, products, plan)
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
        for number, (repeat, product, size, concurrency) in enumerate(plan):
            cell_id = f"r{repeat}-{product['name']}-s{size}-c{concurrency}"
            cell = args.output / "cells" / cell_id
            cell.mkdir(parents=True, exist_ok=True)
            ops = operations(args, size)
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
                    parameters = measurement_parameters(args, product, repeat, size, concurrency, op, result_path)
                    total_bytes = parameters["total_bytes"]
                    run_id = parameters["run_id"]
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
