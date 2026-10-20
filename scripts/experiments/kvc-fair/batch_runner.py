#!/usr/bin/env python3
"""Run source-pinned TCP batch gates and counterbalanced, serialized measurements."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

from profile_put import atomic_json, digest, drain, stop_process, utc

CGROUP = Path("/sys/fs/cgroup/kvcfair20261020")
BATCHES = [1, 16, 256, 4096]
OPS = ["put", "upsert", "get", "exist_hit", "exist_miss"]


def supported(product, batch, op):
    return product != "lmcache" or op.startswith("exist") or batch == 1 and op in {"put", "get"}


def plan(repeats):
    products = ["datasystem", "mooncake", "lmcache"]
    rows = []
    for repeat in range(repeats):
        order = products[repeat % 3:] + products[:repeat % 3]
        batches = BATCHES[repeat % 4:] + BATCHES[:repeat % 4]
        for product in order:
            rows.append({"repeat": repeat, "product": product, "batches": batches,
                         "operations": [{"batch": b, "operation": op, "supported": supported(product, b, op)}
                                        for b in batches for op in OPS]})
    return rows


class Runner:
    def __init__(self, args):
        self.args = args
        self.root, self.out = args.root.resolve(), args.out.absolute()
        self.out.mkdir(parents=True, exist_ok=False)
        self.log = (self.out / "events.jsonl").open("x", buffering=1)
        self.products = {p["name"]: p for p in json.loads((self.root / "products.json").read_text())["products"]}
        stage = json.loads((self.root / "batch-stage.json").read_text())
        for name, row in stage["inputs"].items():
            if digest(self.root / name) != row["sha256"]:
                raise RuntimeError("Frozen stage input changed: " + name)
        atomic_json(self.out / "batch-stage.json", stage)
        atomic_json(self.out / "parameters.json", {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()})
        atomic_json(self.out / "products.json", list(self.products.values()))

    def event(self, kind, **details):
        row = {"event": kind, "utc": utc(), "wall_ns": time.time_ns(), "mono_ns": time.monotonic_ns(), **details}
        self.log.write(json.dumps(row) + "\n")
        atomic_json(self.out / "status.json", row)
        print(json.dumps(row), flush=True)

    def execute(self, argv, log, env, timeout=1200):
        start, mono = time.time_ns(), time.monotonic_ns()
        rc = None
        try:
            with log.open("a") as stream:
                proc = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, env=env, start_new_session=True)
                try:
                    rc = proc.wait(timeout)
                except BaseException:
                    stop_process(proc)
                    raise
            if rc:
                raise RuntimeError(f"rc={rc}; inspect {log}")
        finally:
            self.event("command", argv=argv, log=str(log), start_wall_ns=start, end_wall_ns=time.time_ns(),
                       duration_s=(time.monotonic_ns() - mono) / 1e9, returncode=rc)

    def lifecycle(self, row, number):
        product = self.products[row["product"]]
        cell = self.out / f"r{row['repeat']}-{row['product']}"
        cell.mkdir()
        variables = {"run_dir": str(self.root / "run" / (self.args.action + "-" + cell.name)),
                     "server_logs": str(cell / "server-logs")}
        variables["runtime_dir"] = variables["run_dir"]
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONHASHSEED="0",
                   **{k: v.format(**variables) for k, v in product.get("env", {}).items()})
        start = [v.format(**variables) for v in product["start"]]
        stop = [v.format(**variables) for v in product["stop"]]
        self.event("group_start", group=cell.name, number=number)
        try:
            drain(CGROUP, ["server", "client"])
            self.execute(stop, cell / "lifecycle.log", env, 180)
            self.execute(start, cell / "lifecycle.log", env, 180)
            time.sleep(2)
            if self.args.action == "gate":
                for index in [0, 1]:
                    for mode in ["insert"] if row["product"] == "lmcache" else ["insert", "upsert"]:
                        output = cell / f"client-{index}-{mode}.json"
                        command = ["python3", str(self.root / "cgroup.py"), "--group", "client", "exec", "--",
                                   "strace", "-f", "-ttt", "-T", "-yy", "-s", "256",
                                   "-e", "trace=network,read,write,readv,writev", "-o", str(output.with_suffix(".strace")),
                                   product["python"], str(self.root / "batch_gate.py"), "--adapter", product["adapter"],
                                   "--config", product["config"], "--client-index", str(index), "--write-mode", mode,
                                   "--run-id", self.out.name + "-" + cell.name + "-c" + str(index) + "-" + mode,
                                   "--out", str(output)]
                        self.execute(command, output.with_suffix(".log"), env, 600)
                        raw = json.loads(output.read_text())
                        if not raw.get("valid"):
                            raise RuntimeError("Native batch gate did not pass")
            else:
                preloaded = False
                for spec in row["operations"]:
                    batch, op = spec["batch"], spec["operation"]
                    output = cell / f"b{batch}-{op}.json"
                    if not spec["supported"]:
                        atomic_json(output, {"product": row["product"], "repeat": row["repeat"],
                                             "batch": batch, "operation": op, "status": "not_supported",
                                             "reason": "No equivalent native independent-key batch CPU TCP API in pinned release"})
                        continue
                    command = ["python3", str(self.root / "cgroup.py"), "--group", "client", "exec", "--",
                               product["python"], str(self.root / "batch_benchmark.py"),
                               "--adapter", product["adapter"], "--config", product["config"], "--op", op,
                               "--batch", str(batch), "--run-id", self.out.name + "-" + cell.name,
                               "--out", str(output), "--duration", str(self.args.duration),
                               "--warmup", str(self.args.warmup), "--put-keys", str(self.args.put_keys)]
                    if preloaded:
                        command.append("--skip-preload")
                    self.event("measure_prepare", group=cell.name, batch=batch, operation=op)
                    self.execute(command, output.with_suffix(".log"), env)
                    raw = json.loads(output.read_text())
                    if not raw.get("valid"):
                        raise RuntimeError("Invalid batch result " + str(output))
                    preloaded = True
                    self.event("measure_complete", group=cell.name, batch=batch, operation=op,
                               success=raw["success"], keys_s=raw["keys_s"], duration_s=raw["duration_s"])
            self.event("group_complete", group=cell.name)
            return cell.name
        finally:
            self.execute(stop, cell / "lifecycle.log", env, 180)
            drain(CGROUP, ["server", "client"], timeout=10)

    def run(self):
        rows = plan(1 if self.args.action == "gate" else self.args.repeats)
        atomic_json(self.out / "plan.json", rows)
        summary = {"started": utc(), "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(),
                   "completed": [], "failed": [], "profile": "native-batch-tcp", "action": self.args.action}
        monitor = interference = None
        try:
            if self.args.action != "gate":
                self.check_gate()
                monitor_log = (self.out / "monitor.log").open("x")
                monitor = subprocess.Popen(["python3", str(self.root / "cgroup.py"), "--group", "monitor", "exec", "--",
                                            "python3", str(self.root / "monitor.py"), "--interval", ".2",
                                            "--out", str(self.out / "resources")], stdout=monitor_log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                interference = subprocess.Popen(["python3", str(self.root / "cgroup.py"), "--group", "monitor", "exec", "--",
                                                 "python3", str(self.root / "batch_interference.py"),
                                                 "--out", str(self.out / "resources/interference.jsonl")],
                                                stdout=monitor_log, stderr=subprocess.STDOUT, start_new_session=True)
            for number, row in enumerate(rows):
                summary["completed"].append(self.lifecycle(row, number + 1))
                atomic_json(self.out / "summary.json", summary)
        except BaseException:
            summary["failed"].append(traceback.format_exc())
            self.event("suite_failed", error=summary["failed"][-1])
        finally:
            if monitor is not None:
                monitor.send_signal(signal.SIGINT)
                try:
                    monitor.wait(30)
                except subprocess.TimeoutExpired:
                    stop_process(monitor)
                summary["monitor_exit"] = monitor.returncode
                if interference is not None:
                    interference.send_signal(signal.SIGINT)
                    try:
                        interference.wait(10)
                    except subprocess.TimeoutExpired:
                        stop_process(interference)
                    summary["interference_exit"] = interference.returncode
                monitor_log.close()
            summary.update(ended=utc(), end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
            summary["duration_s"] = (summary["end_mono_ns"] - summary["start_mono_ns"]) / 1e9
            try:
                summary["drained"] = drain(CGROUP)
            except BaseException:
                summary["failed"].append(traceback.format_exc())
            summary["valid"] = len(summary["completed"]) == len(rows) and not summary["failed"] and summary.get("monitor_exit", 0) == 0 and summary.get("interference_exit", 0) == 0
            atomic_json(self.out / "summary.json", summary)
            self.event("suite_complete", valid=summary["valid"], completed=len(summary["completed"]), duration_s=summary["duration_s"])
        return 0 if summary["valid"] else 1

    def check_gate(self):
        root = self.root / self.args.gate
        gate = json.loads((root / "summary.json").read_text())
        if (not gate.get("valid") or not gate.get("ended") or gate.get("failed")
                or set(gate.get("completed", [])) != {"r0-" + name for name in self.products}):
            raise RuntimeError("A complete successful native batch gate is required")
        for filename in ["batch-stage.json", "products.json"]:
            if (root / filename).read_bytes() != (self.out / filename).read_bytes():
                raise RuntimeError("Gate execution inputs differ: " + filename)
        for name in self.products:
            for index in [0, 1]:
                for mode in ["insert"] if name == "lmcache" else ["insert", "upsert"]:
                    raw = json.loads((root / ("r0-" + name) / f"client-{index}-{mode}.json").read_text())
                    if (not raw.get("valid") or raw.get("client_index") != index
                            or raw.get("value_size") != 1024 or raw.get("batch_sizes") != BATCHES
                            or not all(case.get("valid") for case in raw.get("cases", []))
                            or len(raw.get("cases", [])) != len(BATCHES)):
                        raise RuntimeError("Gate endpoint/batch evidence incomplete")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["gate", "run", "smoke"])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--gate", default="batch-gate-001")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--duration", type=float, default=20)
    p.add_argument("--warmup", type=float, default=3)
    p.add_argument("--put-keys", type=int, default=262144)
    args = p.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if args.action == "smoke":
        args.repeats, args.duration, args.warmup, args.put_keys = 1, 1, .1, 32768
    if args.repeats < 1:
        p.error("positive repeats required")
    with (args.root / "suite.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        drain(CGROUP)
        raise SystemExit(Runner(args).run())


if __name__ == "__main__":
    main()
