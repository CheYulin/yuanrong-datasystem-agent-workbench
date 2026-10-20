#!/usr/bin/env python3
"""Run source-pinned TCP batch gates and counterbalanced, serialized measurements."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import traceback

from kvcbench.platform.files import atomic_json
from kvcbench.platform.command import stop_owned as stop_process
from kvcbench.platform.files import digest
from kvcbench.platform.cgroup import drain
from kvcbench.platform.locks import open_path
from kvcbench.core.timing import utc

CGROUP = Path("/sys/fs/cgroup/kvcfair20261020")
from kvcbench.run.plan import BATCHES, OPS, PROTOCOL, supported, plan


class Runner:
    def __init__(self, args, *, observer_factory=None, workflow_observer=None):
        self.args = args
        self.observer_factory = observer_factory
        self.workflow_observer = workflow_observer
        self.root, self.out = args.root.resolve(), args.out.absolute()
        self.out.mkdir(parents=True, exist_ok=False)
        self.log = (self.out / "events.jsonl").open("x", buffering=1)
        self.products = {p["name"]: p for p in json.loads((self.root / "products.json").read_text())["products"]}
        stage = json.loads((self.root / "batch-stage.json").read_text())
        for name, row in stage["inputs"].items():
            if digest(self.root / name) != row["sha256"]:
                raise RuntimeError("Frozen stage input changed: " + name)
            if "mode" in row and (self.root / name).stat().st_mode & 0o777 != row["mode"]:
                raise RuntimeError("Frozen stage input permissions changed: " + name)
        atomic_json(self.out / "batch-stage.json", stage)
        parameters = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
        parameters["workload_protocol"] = PROTOCOL
        atomic_json(self.out / "parameters.json", parameters)
        atomic_json(self.out / "products.json", list(self.products.values()))

    def event(self, kind, **details):
        row = {"event": kind, "utc": utc(), "wall_ns": time.time_ns(), "mono_ns": time.monotonic_ns(), **details}
        self.log.write(json.dumps(row) + "\n")
        atomic_json(self.out / "status.json", row)
        print(json.dumps(row), flush=True)

    def execute(self, argv, log, env, timeout=1200, accepted_returncodes=(0,)):
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
            if rc not in accepted_returncodes:
                raise RuntimeError(f"rc={rc}; inspect {log}")
            return rc
        finally:
            self.event("command", argv=argv, log=str(log), start_wall_ns=start, end_wall_ns=time.time_ns(),
                       duration_s=(time.monotonic_ns() - mono) / 1e9, returncode=rc)

    def lifecycle(self, row, number):
        cell = self.out / f"r{row['repeat']}-{row['product']}"
        cell.mkdir()
        self.event("group_start", group=cell.name, number=number)
        if self.args.action == "gate":
            self.epoch(row, cell, {"id": "gate", "kind": "correctness", "operations": []})
        else:
            for spec in row["operations"]:
                if not spec["supported"]:
                    atomic_json(cell / f"b{spec['batch']}-{spec['operation']}.json",
                                {"product": row["product"], "repeat": row["repeat"],
                                 "batch": spec["batch"], "operation": spec["operation"], "status": "not_supported",
                                 "reason": "No equivalent native independent-key batch CPU TCP API in pinned release"})
            for epoch in row["epochs"]:
                self.epoch(row, cell, epoch)
        self.event("group_complete", group=cell.name)
        return cell.name

    def epoch(self, row, cell, epoch):
        product = self.products[row["product"]]
        is_gate = self.args.action == "gate"
        directory = cell if is_gate else cell / "epochs" / epoch["id"]
        directory.mkdir(parents=True, exist_ok=True)
        suffix = "" if is_gate else "-" + epoch["id"]
        variables = {"run_dir": str(self.root / "run" / (self.args.action + "-" + cell.name + suffix)),
                     "server_logs": str(directory / "server-logs")}
        variables["runtime_dir"] = variables["run_dir"]
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONHASHSEED="0",
                   **{k: v.format(**variables) for k, v in product.get("env", {}).items()})
        start = [v.format(**variables) for v in product["start"]]
        stop = [v.format(**variables) for v in product["stop"]]
        epoch_identity = cell.name + "/" + epoch["id"]
        ledger = {"identity": epoch_identity, "kind": epoch["kind"], "operations": epoch["operations"],
                  "run_dir": variables["run_dir"], "workload_protocol": PROTOCOL,
                  "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(), "valid": False,
                  "execution_complete": False, "measurement_failures": []}
        self.event("epoch_start", group=cell.name, epoch=epoch["id"], run_dir=variables["run_dir"], epoch_kind=epoch["kind"])
        completed = False
        try:
            drain(CGROUP, ["server", "client"])
            self.execute(stop, directory / "lifecycle.log", env, 180)
            self.execute(start, directory / "lifecycle.log", env, 180)
            time.sleep(2)
            if self.args.action == "gate":
                if row["product"] == "datasystem":
                    from tcp_probe_ds_sizes import configuration_evidence
                    evidence = configuration_evidence(Path(variables["run_dir"]), ["127.0.0.1:31531", "127.0.0.1:31532"])
                    for worker in evidence["workers"]:
                        destination = cell / f"worker-{worker['worker_index']}.flags"
                        shutil.copy2(worker["path"], destination)
                        worker["collected_copy"] = str(destination)
                    shutil.copy2(evidence["processes_path"], cell / "processes.json")
                    evidence["collected_processes_copy"] = str(cell / "processes.json")
                    atomic_json(cell / "tcp-configuration.json", evidence)
                for index in [0, 1]:
                    for mode in ["insert"] if row["product"] == "lmcache" else ["insert", "upsert"]:
                        output = cell / f"client-{index}-{mode}.json"
                        wire = cell / f"wire-client-{index}-{mode}"
                        wire.mkdir()
                        command = ["python3", str(self.root / "cgroup.py"), "--group", "client", "exec", "--",
                                   "strace", "-ff", "-ttt", "-T", "-yy", "-xx", "-s", "2048",
                                   "-e", "trace=network,read,write,readv,writev", "-o", str(wire / "strace"),
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
                for spec in epoch["operations"]:
                    batch, op = spec["batch"], spec["operation"]
                    output = cell / f"b{batch}-{op}.json"
                    if not spec["supported"]:
                        raise RuntimeError("Unsupported operations must not enter a service epoch")
                    command = ["python3", str(self.root / "cgroup.py"), "--group", "client", "exec", "--",
                               product["python"], str(self.root / "batch_benchmark.py"),
                               "--adapter", product["adapter"], "--config", product["config"], "--op", op,
                               "--batch", str(batch), "--run-id", self.out.name + "-r" + str(row["repeat"]),
                               "--out", str(output), "--duration", str(self.args.duration),
                               "--warmup", str(self.args.warmup), "--put-keys", str(self.args.put_keys)]
                    if preloaded:
                        command.append("--skip-preload")
                    self.event("measure_prepare", group=cell.name, epoch=epoch["id"], batch=batch, operation=op)
                    continue_errors = getattr(self.args, "continue_readonly_errors", False) and op in {"get", "exist_hit", "exist_miss"}
                    rc = self.execute(command, output.with_suffix(".log"), env, accepted_returncodes=(0, 1)) if continue_errors else self.execute(command, output.with_suffix(".log"), env)
                    raw = json.loads(output.read_text())
                    if not raw.get("valid"):
                        if not continue_errors or rc != 1:
                            raise RuntimeError("Invalid batch result " + str(output))
                        from kvcbench.core.case_validation import measured_case
                        case_identity = cell.name + "/" + output.stem
                        checked = measured_case(raw, {"identity": case_identity, "product": row["product"],
                            "repeat": row["repeat"], "batch": batch, "operation": op, "supported": True},
                            {"continue_readonly_errors": True, "isolated_epochs": True,
                             "adapter": product["adapter"], "config": product["config"],
                             "run_id": self.out.name + "-r" + str(row["repeat"]), "out": str(output),
                             "skip_preload": preloaded})
                        if checked.get("status") != "failed" or not checked.get("evidence_verified"):
                            raise RuntimeError("Read-only failure evidence is incomplete: " + repr(checked.get("issues")))
                        if not hasattr(self, "measurement_failures"):
                            self.measurement_failures = []
                        ledger["measurement_failures"].append(case_identity)
                        self.measurement_failures.append(case_identity)
                        outcome = {"identity": case_identity, "returncode": rc, "status": "failed",
                                   "read_only": True, "errors": checked["native_errors"], "result": str(output),
                                   "result_sha256": digest(output), "evidence_verified": True}
                        (cell / "case-outcomes").mkdir(exist_ok=True)
                        atomic_json(cell / "case-outcomes" / (output.stem + ".json"), outcome)
                        self.event("measure_failed", group=cell.name, epoch=epoch["id"], batch=batch, operation=op,
                                   identity=case_identity, returncode=rc, failures=raw["failures"],
                                   success=raw["success"], duration_s=raw["duration_s"])
                        preloaded = True
                        continue
                    if continue_errors and rc != 0:
                        raise RuntimeError("Successful result contradicts command exit status")
                    preloaded = True
                    self.event("measure_complete", group=cell.name, epoch=epoch["id"], batch=batch, operation=op,
                               success=raw["success"], keys_s=raw["keys_s"], duration_s=raw["duration_s"])
            completed = True
        finally:
            try:
                self.execute(stop, directory / "lifecycle.log", env, 180)
                ledger["drained"] = drain(CGROUP, ["server", "client"], timeout=10)
                ledger["execution_complete"] = completed
                ledger["valid"] = completed and not ledger["measurement_failures"]
            finally:
                ledger.update(end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
                ledger["duration_s"] = (ledger["end_mono_ns"] - ledger["start_mono_ns"]) / 1e9
                atomic_json(directory / "epoch-summary.json", ledger)
                self.event("epoch_complete", group=cell.name, epoch=epoch["id"], valid=ledger["valid"],
                           execution_complete=ledger["execution_complete"], measurement_failures=ledger["measurement_failures"], duration_s=ledger["duration_s"])
        self.epochs_completed.append(epoch_identity)

    def run(self):
        rows = plan(1 if self.args.action == "gate" else self.args.repeats)
        atomic_json(self.out / "plan.json", rows)
        expected_epochs = [f"r{row['repeat']}-{row['product']}/" + epoch["id"] for row in rows
                           for epoch in ([{"id": "gate"}] if self.args.action == "gate" else row["epochs"])]
        self.epochs_completed = []
        self.measurement_failures = []
        summary = {"started": utc(), "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(),
                   "completed": [], "failed": [], "profile": "native-batch-tcp", "action": self.args.action,
                   "workload_protocol": PROTOCOL, "epochs_expected": expected_epochs, "epochs_completed": self.epochs_completed,
                   "measurement_failures": self.measurement_failures}
        observation = None
        try:
            if self.args.action != "gate":
                self.check_gate()
                if self.observer_factory is None:
                    raise ValueError("Explicit suite observer factory is required")
                observation = self.observer_factory(self.out / "resources", CGROUP)
                ready = observation.start()
                self.event("monitor_ready", evidence=str(ready), scope="startup only")
                if self.workflow_observer is not None:
                    self.workflow_observer.measurement_started(ready)
            for number, row in enumerate(rows):
                summary["completed"].append(self.lifecycle(row, number + 1))
                atomic_json(self.out / "summary.json", summary)
        except BaseException:
            summary["failed"].append(traceback.format_exc())
            self.event("suite_failed", error=summary["failed"][-1])
        finally:
            if self.workflow_observer is not None:
                try:
                    self.workflow_observer.measurement_finished(summary, rows, expected_epochs)
                except BaseException:
                    summary["failed"].append(traceback.format_exc())
            if observation is not None:
                try:
                    final = observation.stop()
                    if final is None and (self.out / "resources/final.json").exists():
                        final = json.loads((self.out / "resources/final.json").read_text())
                    if final:
                        summary["monitor_exit"] = final["children"].get("host", {}).get("returncode")
                        summary["interference_exit"] = final["children"].get("interference", {}).get("returncode")
                        if not final["normal_exit"] or not final["closed"]:
                            summary["failed"].append("Suite observation did not stop normally")
                except BaseException:
                    summary["failed"].append(traceback.format_exc())
            summary.update(ended=utc(), end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
            summary["duration_s"] = (summary["end_mono_ns"] - summary["start_mono_ns"]) / 1e9
            try:
                summary["drained"] = drain(CGROUP)
            except BaseException:
                summary["failed"].append(traceback.format_exc())
            summary["execution_complete"] = (len(summary["completed"]) == len(rows) and self.epochs_completed == expected_epochs
                                             and not summary["failed"] and summary.get("monitor_exit", 0) == 0 and summary.get("interference_exit", 0) == 0)
            summary["valid"] = summary["execution_complete"] and not self.measurement_failures
            atomic_json(self.out / "summary.json", summary)
            self.event("suite_complete", valid=summary["valid"], completed=len(summary["completed"]), duration_s=summary["duration_s"])
            self.log.close()
            if self.workflow_observer is not None:
                self.workflow_observer.cleanup_finished(summary)
        return 0 if summary["valid"] else 1

    def check_gate(self):
        from kvcbench.run.admission import verify_gate
        verify_gate(self.root, self.args.gate, self.products, self.out)


def main(argv=None, *, observer_factory=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["gate", "run", "smoke"])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--gate", default="batch-gate-001")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--duration", type=float, default=20)
    p.add_argument("--warmup", type=float, default=3)
    p.add_argument("--put-keys", type=int, default=262144)
    p.add_argument("--continue-readonly-errors", action="store_true")
    args = p.parse_args(argv)
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if args.action == "smoke":
        args.repeats, args.duration, args.warmup, args.put_keys = 1, 1, .1, 32768
    if args.continue_readonly_errors and args.action != "run":
        p.error("Read-only native failures may only be collected in the formal run")
    if args.repeats < 1:
        p.error("positive repeats required")
    with open_path(args.root / "suite.lock") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from kvcbench.run.admission import verify_resource_lock
        verify_resource_lock(args.root, lock.fileno(), CGROUP)
        drain(CGROUP)
        raise SystemExit(Runner(args, observer_factory=observer_factory).run())


if __name__ == "__main__":
    main()
