#!/usr/bin/env python3
"""Independent untraced correctness diagnosis; never a performance repeat."""

import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in {None, ""}:
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from kvcbench.diagnostics.put import atomic_json, digest, drain, stop_process, utc


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--products", nargs="+", default=["mooncake", "lmcache"])
    args = p.parse_args()
    cgroup = Path("/sys/fs/cgroup/kvcfair20261020")
    with (args.root / "suite.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        drain(cgroup)
        args.out.mkdir(parents=True, exist_ok=False)
        products = json.loads((args.root / "products.json").read_text())["products"]
        result = {"kind": "untraced_correctness_diagnostic", "benchmark_eligible": False,
                  "started": utc(), "start_mono_ns": time.monotonic_ns(), "start_wall_ns": time.time_ns(),
                  "diagnostic_script_sha256": digest(Path(__file__)), "products": [], "commands": [], "failed": []}
        def execute(argv, path, env, timeout):
            start, mono, rc = time.time_ns(), time.monotonic_ns(), None
            try:
                with path.open("a") as log:
                    proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
                    try:
                        rc = proc.wait(timeout)
                    except BaseException:
                        stop_process(proc)
                        raise
                if rc:
                    raise RuntimeError(f"rc={rc}, log={path}")
            finally:
                result["commands"].append({"argv": argv, "start_wall_ns": start, "end_wall_ns": time.time_ns(),
                                           "duration_s": (time.monotonic_ns() - mono) / 1e9, "returncode": rc})
        for product in products:
            if product["name"] not in args.products:
                continue
            name = product["name"]
            cell = args.out / name
            cell.mkdir()
            variables = {"run_dir": str(args.root / "run" / (args.out.name + "-" + name)), "server_logs": str(cell / "server-logs")}
            variables["runtime_dir"] = variables["run_dir"]
            env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONHASHSEED="0",
                       **{k: v.format(**variables) for k, v in product.get("env", {}).items()})
            start = [v.format(**variables) for v in product["start"]]
            stop = [v.format(**variables) for v in product["stop"]]
            try:
                execute(stop, cell / "lifecycle.log", env, 180)
                execute(start, cell / "lifecycle.log", env, 180)
                for index in [0, 1]:
                    for mode in ["insert"] if name == "lmcache" else ["insert", "upsert"]:
                        output = cell / f"client-{index}-{mode}.json"
                        execute(["python3", str(args.root / "cgroup.py"), "--group", "client", "exec", "--",
                                 product["python"], str(args.root / "batch_gate.py"), "--adapter", product["adapter"],
                                 "--config", product["config"], "--client-index", str(index), "--write-mode", mode,
                                 "--run-id", args.out.name + "-" + name + "-c" + str(index) + "-" + mode,
                                 "--out", str(output)], output.with_suffix(".log"), env, 600)
                result["products"].append({"name": name, "valid": True})
            except Exception:
                result["failed"].append({"name": name, "error": traceback.format_exc()})
            finally:
                execute(stop, cell / "lifecycle.log", env, 180)
                drain(cgroup)
        result.update(ended=utc(), end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
        result["duration_s"] = (result["end_mono_ns"] - result["start_mono_ns"]) / 1e9
        result["valid"] = not result["failed"] and len(result["products"]) == len(args.products)
        atomic_json(args.out / "summary.json", result)
        print(json.dumps(result))
        raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
