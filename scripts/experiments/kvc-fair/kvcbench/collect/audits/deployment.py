#!/usr/bin/env python3
"""Audit collected DS launch isolation and RocksDB errors, without contacting services."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import re
import time


LEGACY_CLUSTERS = ["gate-r0-datasystem", "smoke-r0-datasystem", *[f"run-r{i}-datasystem" for i in range(3)]]
PROTOCOL = "separate-read-and-fresh-put-v1"
BATCHES = [1, 16, 256, 4096]
PRODUCTS = ["datasystem", "mooncake", "lmcache"]
SOURCE_SHA = "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6"
STORAGE = re.compile(rb"rocks(?:db|_store)|object_metadata|\.(?:sst|ldb)\b", re.I)
FILE_ERROR = re.compile(rb"no such file|size mismatch|corrupt|(?:i/o|io) error|failed|cannot open|too short|lock.*(?:error|busy)", re.I)
PURE_KV_FLAGS = {"rocksdb_write_mode": "none", "sc_regular_socket_num": "0", "sc_stream_socket_num": "0",
                 "json_log_monitor": "false", "enable_perf_trace_log": "false",
                 "request_sample_rate": "0", "access_sample_rate": "0"}
ROCKS_ARTIFACT = re.compile(r"(?:LOCK|CURRENT|IDENTITY|LOG(?:\.old.*)?|MANIFEST-.*|OPTIONS-.*|.*\.(?:sst|ldb|wal)|[0-9]+\.log|WAL.*)", re.I)


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def path_value(value):
    if not isinstance(value, str):
        raise ValueError("Path must be a string")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("Path must be absolute and normalized")
    return path


def one_flag(argv, name):
    prefix = "--" + name + "="
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise ValueError("Launch argv must be a string list")
    values = [item[len(prefix):] for item in argv if item.startswith(prefix)]
    if len(values) != 1:
        raise ValueError(f"Expected exactly one explicit {name} flag")
    return values[0]


def validate_plan(rows, repeats):
    expected_groups = {(repeat, product) for repeat in range(repeats) for product in PRODUCTS}
    seen = set()
    for group in rows:
        identity = group["repeat"], group["product"]
        if identity not in expected_groups or identity in seen or group.get("workload_protocol") != PROTOCOL:
            raise ValueError("Unexpected, duplicate or legacy plan group")
        seen.add(identity)
        batches = group["batches"]
        if len(batches) != 4 or set(batches) != set(BATCHES):
            raise ValueError("Each group must contain all four batch sizes exactly once")
        def supported(batch, operation):
            return group["product"] != "lmcache" or operation.startswith("exist") or batch == 1 and operation in {"put", "get"}
        specs = [{"batch": batch, "operation": operation, "supported": supported(batch, operation), "epoch": "read"}
                 for batch in batches for operation in ["upsert", "get", "exist_hit", "exist_miss"]]
        specs += [{"batch": batch, "operation": "put", "supported": supported(batch, "put"), "epoch": f"put-b{batch}"}
                  for batch in batches]
        epochs = []
        for name in ["read", *[f"put-b{batch}" for batch in batches]]:
            operations = [spec for spec in specs if spec["epoch"] == name and spec["supported"]]
            if operations:
                epochs.append({"id": name, "kind": "read" if name == "read" else "fresh_put", "operations": operations})
        if group.get("operations") != specs or group.get("epochs") != epochs:
            raise ValueError("Read/fresh-PUT epoch layout, supported-only operations or N/A semantics differ")
    if seen != expected_groups:
        raise ValueError("Plan is missing a product/repeat group")


class Audit:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.inputs, self.errors = {}, []
        self.analysis_helpers = {}

    def error(self, code, message, cluster=None, worker=None):
        self.errors.append({"code": code, "message": str(message), "cluster": cluster, "worker": worker})

    def file(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe collected input path")
        path = self.root / relative
        if any(part.is_symlink() for part in [path, *path.parents]):
            raise ValueError("Symlink evidence is forbidden: " + str(relative))
        if not path.is_file():
            raise ValueError("Missing regular evidence file: " + str(relative))
        return path

    def remember(self, path, sha=None):
        key = path.relative_to(self.root).as_posix()
        stat = path.stat()
        record = {"sha256": sha or digest(path), "bytes": stat.st_size}
        if key in self.inputs and self.inputs[key] != record:
            raise ValueError("Input changed while auditing: " + key)
        self.inputs[key] = record
        return record

    def read_json(self, relative):
        path = self.file(relative)
        data = path.read_bytes()
        self.remember(path, hashlib.sha256(data).hexdigest())
        return json.loads(data)

    def read_flags(self, relative):
        path = self.file(relative)
        data = path.read_bytes()
        record = self.remember(path, hashlib.sha256(data).hexdigest())
        argv = data.decode("utf-8").splitlines()
        if any(not line.startswith("--") or "=" not in line for line in argv):
            raise ValueError("Malformed worker flags: " + str(relative))
        return argv, record

    def read_events(self, relative):
        path = self.file(relative)
        data = path.read_bytes()
        self.remember(path, hashlib.sha256(data).hexdigest())
        return [json.loads(line) for line in data.splitlines() if line.strip()]

    def suite_epochs(self, phase, action, repeats, task_root):
        rows = self.read_json(Path(phase) / "plan.json")
        validate_plan(rows, repeats)
        summary = self.read_json(Path(phase) / "summary.json")
        identities, selected = [], []
        events = self.read_events(Path(phase) / "events.jsonl")
        parameter_path = self.root / phase / "parameters.json"
        parameters = self.read_json(Path(phase) / "parameters.json") if parameter_path.exists() else {}
        continued = parameters.get("continue_readonly_errors") is True
        native_failures = [event for event in events if event.get("event") == "measure_failed"]
        verified_measurements = None
        if action != "gate" and (continued or native_failures or summary.get("measurement_failures")):
            for path in sorted((self.root / phase).rglob("*.json")):
                self.remember(self.file(path.relative_to(self.root)))
            sample_path = self.file(Path(phase) / "resources/samples.jsonl")
            self.remember(sample_path)
            from kvcbench.collect.provenance import analysis_sources
            for name, path in analysis_sources().items():
                record = {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}
                if name in self.analysis_helpers and self.analysis_helpers[name] != record:
                    raise ValueError("Analysis dependency changed during deployment audit: " + name)
                self.analysis_helpers[name] = record
            checked = importlib.import_module("kvcbench.collect.analysis.batch").analyze(self.root / phase)
            verified_measurements = {"evidence_verified": checked.get("evidence_verified"),
                                     "counts": checked.get("counts"), "issues": checked.get("issues"),
                                     "failed_identities": [row["identity"] for row in checked.get("rows", [])
                                                           if row.get("status") == "failed"]}
            if checked.get("evidence_verified") is not True or checked.get("issues"):
                self.error("measurement-evidence", verified_measurements)
            if summary.get("measurement_failures") != verified_measurements["failed_identities"]:
                self.error("measurement-failure-identities", phase)
        for group in rows:
            cell = f"r{group['repeat']}-{group['product']}"
            epochs = [{"id": "gate", "kind": "correctness", "operations": []}] if action == "gate" else group["epochs"]
            for epoch in epochs:
                identity = cell + "/" + epoch["id"]
                identities.append(identity)
                directory = Path(phase) / cell
                if action != "gate":
                    directory /= "epochs/" + epoch["id"]
                ledger = self.read_json(directory / "epoch-summary.json")
                name = action + "-" + cell + ("" if action == "gate" else "-" + epoch["id"])
                try:
                    expected_run = str(task_root / "run" / name)
                    outcomes = [event for event in events if event.get("event") in {"measure_complete", "measure_failed"}
                                and event.get("group") == cell and event.get("epoch") == epoch["id"]]
                    failed = [cell + f"/b{event['batch']}-{event['operation']}" for event in outcomes
                              if event["event"] == "measure_failed"]
                    if (ledger.get("identity") != identity or ledger.get("kind") != epoch["kind"]
                            or ledger.get("operations") != epoch["operations"] or ledger.get("run_dir") != expected_run
                            or ledger.get("workload_protocol") != PROTOCOL or ledger.get("valid") is not (not failed)
                            or continued and (ledger.get("execution_complete") is not True
                                              or ledger.get("measurement_failures") != failed)
                            or failed and not continued):
                        raise ValueError("Epoch summary identity, workload, completion or honest native-failure status differs")
                    before, after = ledger["start_mono_ns"], ledger["end_mono_ns"]
                    if (type(before) is not int or type(after) is not int or after <= before
                            or abs(ledger["duration_s"] - (after - before) / 1e9) > 1e-9):
                        raise ValueError("Epoch monotonic timing is incomplete or inconsistent")
                    drained = ledger.get("drained", {})
                    members = drained.get("members", {})
                    required = {f"/sys/fs/cgroup/kvcfair20261020/{role}/cgroup.procs" for role in ["server", "client"]}
                    if (drained.get("populated") != [] or not required.issubset(members)
                            or any(value != [] for value in members.values())):
                        raise ValueError("Epoch did not record server/client drain")
                    if group["product"] == "datasystem":
                        for measured in [[event for event in events if event.get("event") == "measure_prepare"
                                          and event.get("group") == cell and event.get("epoch") == epoch["id"]], outcomes]:
                            if ([(event.get("batch"), event.get("operation")) for event in measured]
                                    != [(spec["batch"], spec["operation"]) for spec in epoch["operations"]]
                                    or any(not ledger["start_wall_ns"] <= event["wall_ns"] <= ledger["end_wall_ns"] for event in measured)):
                                raise ValueError("Actual measured operations differ from the separate read/fresh-PUT epoch")
                        remote_log = str(task_root / directory / "lifecycle.log")
                        commands = [event for event in events if event.get("event") == "command" and event.get("log") == remote_log]
                        actions = []
                        for event in commands:
                            argv = event.get("argv", [])
                            executable = str(task_root / "scripts/datasystem/runtime.py")
                            if argv.count(executable) != 1 or event.get("returncode") != 0:
                                raise ValueError("Epoch lifecycle command failed or uses another runtime")
                            actions.append(argv[argv.index(executable) + 1])
                            if argv.count("--run-dir") != 1 or argv[argv.index("--run-dir") + 1] != expected_run:
                                raise ValueError("Epoch lifecycle command refers to another run directory")
                            if not ledger["start_wall_ns"] <= event["start_wall_ns"] <= event["end_wall_ns"] <= ledger["end_wall_ns"]:
                                raise ValueError("Lifecycle command falls outside its epoch")
                        if actions != ["stop", "start", "stop"]:
                            raise ValueError("Epoch requires its own pre-stop, start and final-stop sequence")
                except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
                    self.error("epoch-evidence", error, name)
                if group["product"] == "datasystem":
                    selected.append(name)
        completed = [f"r{group['repeat']}-{group['product']}" for group in rows]
        failures = [event.get("identity") for event in native_failures]
        if (summary.get("workload_protocol") != PROTOCOL or summary.get("action") != action
                or summary.get("valid") is not (not failures) or summary.get("failed") != [] or not summary.get("ended")
                or continued and (summary.get("execution_complete") is not True or summary.get("measurement_failures") != failures)
                or failures and not continued
                or summary.get("completed") != completed or summary.get("epochs_expected") != identities
                or summary.get("epochs_completed") != identities):
            self.error("suite-epoch-coverage", phase)
        return selected, {"suite": phase, "action": action, "expected_epochs": identities,
                          "epochs_completed": summary.get("epochs_completed"), "datasystem_clusters": selected,
                          "native_measurement_failures": failures, "measurement_evidence": verified_measurements,
                          "execution_complete": summary.get("execution_complete", summary.get("valid"))}

    def database_files(self, directory):
        path = self.root / directory
        if any(part.is_symlink() for part in [path, *path.parents]) or not path.is_dir():
            raise ValueError("Missing regular collected database directory: " + str(directory))
        files, artifacts = [], []
        for item in sorted(path.rglob("*")):
            if item.is_symlink():
                raise ValueError("Symlink inside database evidence: " + str(item.relative_to(self.root)))
            if item.is_file():
                relative = item.relative_to(self.root)
                files.append({"path": relative.as_posix(), **self.remember(self.file(relative))})
                if ROCKS_ARTIFACT.fullmatch(item.name):
                    artifacts.append(files[-1])
        return {"files": files, "database_artifacts": artifacts,
                "scope": "Collected final directory entries only; ordinary eviction_policy state files are retained, not classified as RocksDB by location."}

    def scan(self, relative):
        path = self.file(relative)
        sha, total, first, last, count = hashlib.sha256(), 0, None, None, 0
        with path.open("rb") as stream:
            for number, line in enumerate(stream, 1):
                sha.update(line)
                total += len(line)
                if STORAGE.search(line) and FILE_ERROR.search(line):
                    count += 1
                    anchor = {"line": number, "line_sha256": hashlib.sha256(line).hexdigest(),
                              "line_bytes": len(line), "text_truncated": len(line) > 4096,
                              "text": line[:4096].decode("utf-8", errors="replace").rstrip("\r\n")}
                    if first is None:
                        first = anchor
                    last = anchor
        record = self.remember(path, sha.hexdigest())
        if record["bytes"] != total:
            raise ValueError("Stdout size changed during streaming scan")
        return {"path": str(relative), **record, "matched_errors": count, "first_match": first, "last_match": last}

    def cluster(self, name, task_root):
        directory = Path("run") / name
        row = {"name": name, "workers": [], "live": {"observed": False, "status": "unobserved",
               "scope": "Only the recorded launch manifest is available; no procfs observation is invented."}}
        try:
            manifest = self.read_json(directory / "processes.json")
            expected_run = task_root / "run" / name
            if path_value(manifest.get("run_dir")) != expected_run:
                raise ValueError("Manifest run_dir differs from this exact cluster")
            if manifest.get("runtime_profile") != "pure-kv":
                raise ValueError("Launch manifest does not declare the pure-kv profile")
            processes = manifest.get("processes", [])
            workers = [process for process in processes if process.get("name") in {"worker0", "worker1"}]
            if len(workers) != 2 or {process["name"] for process in workers} != {"worker0", "worker1"}:
                raise ValueError("Exactly two Worker launch identities are required")
            if len({process.get("pid") for process in workers}) != 2:
                raise ValueError("Worker PID identities are not distinct")
            by_name = {process["name"]: process for process in workers}
            for index in range(2):
                process = by_name[f"worker{index}"]
                argv = process.get("command")
                expected = expected_run / f"w{index}" / "rocksdb"
                flags, flag_record = self.read_flags(directory / f"w{index}" / "worker.flags")
                if type(process.get("pid")) is not int or process["pid"] <= 0 or not str(process.get("start_ticks", "")).isdigit():
                    raise ValueError("Missing Worker PID/start-ticks launch identity")
                value = path_value(one_flag(argv, "rocksdb_store_dir"))
                if value != expected or task_root not in value.parents:
                    raise ValueError("Worker database path is shared, outside, or differs from its own fresh instance")
                if (manifest.get("rocksdb_store_dirs", {}).get(f"worker{index}") != str(expected)
                        or one_flag(flags, "rocksdb_store_dir") != str(expected) or argv[1:] != flags):
                    raise ValueError("Manifest, launch argv and flagfile disagree on Worker configuration")
                for key, expected_value in {**PURE_KV_FLAGS, "log_monitor": "false",
                                            "ipc_through_shared_memory": "false", "enable_rdma": "false",
                                            "enable_urma": "false", "l2_cache_type": "none", "spill_size_limit": "0"}.items():
                    if one_flag(argv, key) != expected_value or one_flag(flags, key) != expected_value:
                        raise ValueError("Pure-KV launch flag is absent, duplicated or enabled: " + key)
                files = self.database_files(directory / f"w{index}/rocksdb")
                if files["database_artifacts"]:
                    self.error("rocksdb-database-artifacts", files["database_artifacts"], name, index)
                row["workers"].append({"worker_index": index, "pid": process["pid"],
                    "start_ticks": process["start_ticks"], "rocksdb_store_dir": str(value),
                    "flagfile": {"path": (directory / f"w{index}/worker.flags").as_posix(), **flag_record},
                    "launch_configuration_verified": True, "runtime_profile": "pure-kv", "rocksdb_directory": files})
            if name == "gate-r0-datasystem":
                proof = self.read_json("batch-gate-001/r0-datasystem/tcp-configuration.json")
                source = proof.get("source", {})
                if source.get("sha") != SOURCE_SHA:
                    raise ValueError("Gate live proof is not bound to pinned rc44")
                if proof.get("processes_sha256") != self.inputs[(directory / "processes.json").as_posix()]["sha256"]:
                    raise ValueError("Gate live process snapshot SHA differs from the launch manifest")
                observations = proof.get("workers", [])
                if len(observations) != 2 or {item.get("worker_index") for item in observations} != {0, 1}:
                    raise ValueError("Gate live proof does not contain both Workers")
                for observation in observations:
                    index = observation["worker_index"]
                    process = by_name[f"worker{index}"]
                    worker = row["workers"][index]
                    if (observation.get("pid") != process["pid"] or observation.get("start_ticks") != process["start_ticks"]
                            or observation.get("live_argv") != process["command"]
                            or observation.get("sha256") != worker["flagfile"]["sha256"]
                            or observation.get("path") != str(expected_run / f"w{index}/worker.flags")):
                        raise ValueError("Gate captured live argv/PID/start-ticks/flags binding differs")
                row["live"] = {"observed": True, "status": "verified",
                    "path": "batch-gate-001/r0-datasystem/tcp-configuration.json",
                    "scope": "Saved gate procfs observation only; not current or continuous process liveness."}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
            self.error("launch-or-live-evidence", error, name)
        for index in range(2):
            try:
                scan = self.scan(directory / f"worker{index}.stdout.log")
                row.setdefault("stdout", []).append({"worker_index": index, **scan})
                if scan["matched_errors"]:
                    self.error("rocksdb-file-errors", f"{scan['matched_errors']} matching error records", name, index)
            except (OSError, ValueError) as error:
                self.error("stdout-evidence", error, name, index)
        row["valid"] = not any(error["cluster"] == name for error in self.errors)
        return row

    def run(self):
        start_wall, start_mono = time.time_ns(), time.monotonic_ns()
        rows, runtime, expected_clusters, suites = [], None, [], []
        task_root = None
        try:
            stage = self.read_json("batch-stage.json")
            task_root = path_value(stage.get("root"))
            if stage.get("feature_profile") != "pure-kv":
                self.error("feature-profile", "The deployment audit requires the explicit pure-kv feature stage")
            expected_runtime = stage["inputs"]["scripts/datasystem/runtime.py"]
            source = self.file("runtime-inputs/scripts/datasystem/runtime.py")
            runtime = {"path": source.relative_to(self.root).as_posix(), **self.remember(source)}
            if any(runtime[key] != expected_runtime[key] for key in ["sha256", "bytes"]):
                raise ValueError("Collected runtime source differs from the frozen stage inventory")
            for phase, action, repeats in [("batch-gate-001", "gate", 1), ("batch-smoke-001", "smoke", 1), ("formal-batch-001", "run", 3)]:
                try:
                    if self.read_json(Path(phase) / "batch-stage.json") != stage:
                        self.error("phase-stage-mismatch", phase)
                    names, evidence = self.suite_epochs(phase, action, repeats, task_root)
                    expected_clusters.extend(names)
                    suites.append(evidence)
                except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
                    self.error("phase-stage-evidence", error)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
            self.error("stage-evidence", error)
        if len(suites) != 3 or not expected_clusters or len(set(expected_clusters)) != len(expected_clusters):
            self.error("epoch-plan-coverage", "Complete gate/smoke/three-repeat epoch plans are required")
        discovered = sorted(path.name for path in (self.root / "run").glob("*")
                            if "datasystem" in path.name and path.is_dir())
        names = list(dict.fromkeys([*expected_clusters, *discovered]))
        if not names:
            names = LEGACY_CLUSTERS
        rows = [self.cluster(name, task_root) for name in names]
        unexpected = set(discovered) - set(expected_clusters)
        if unexpected:
            self.error("unexpected-or-legacy-clusters", sorted(unexpected))
        paths = [worker["rocksdb_store_dir"] for row in rows for worker in row["workers"]]
        if len(paths) != len(expected_clusters) * 2 or len(set(paths)) != len(paths):
            self.error("cluster-path-coverage", "Two distinct database paths per planned DS epoch are required")
        for relative, expected in list(self.inputs.items()):
            try:
                path = self.file(relative)
                if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
                    self.error("input-changed", relative)
            except (OSError, ValueError) as error:
                self.error("input-changed", error)
        for name, expected in self.analysis_helpers.items():
            try:
                path = Path(expected["path"])
                if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
                    self.error("analysis-helper-changed", name)
            except OSError as error:
                self.error("analysis-helper-changed", error)
        end_mono, end_wall = time.monotonic_ns(), time.time_ns()
        return {"schema_version": 4, "kind": "datasystem_collected_deployment_audit", "root": str(self.root),
            "workload_protocol": PROTOCOL,
            "feature_profile": "pure-kv",
            "scope": "Recorded pure-KV launch isolation, final collected database-artifact absence and observed error-log audit for DS clusters derived from actual supported-only epoch plans. Fully verified native read failures are retained as failures without invalidating deployment evidence. Gate live procfs is verified separately; other phases remain live-unobserved. No remote inode/symlink or continuous-liveness proof is inferred.",
            "expected_clusters": expected_clusters, "epoch_suites": suites, "clusters": rows, "runtime_source": runtime, "errors": self.errors, "valid": not self.errors,
            "live_coverage": {"observed_clusters": sum(row["live"]["observed"] for row in rows),
                              "expected_clusters": len(expected_clusters), "required_gate_live_observed": any(row["name"] == "gate-r0-datasystem" and row["live"]["observed"] for row in rows)},
            "stdout_error_patterns": {"storage": STORAGE.pattern.decode(), "failure": FILE_ERROR.pattern.decode(),
                "ordinary_warning_is_failure": False}, "inputs": self.inputs,
            "analysis_helpers": self.analysis_helpers,
            "analysis_provenance_scope": "Actual postprocessing source files loaded for native-failure verification; these may be newer than unused frozen-stage helpers and do not replace frozen runtime inputs.",
            "inputs_rehashed_after_audit": True, "helper": {"sha256": digest(Path(__file__)), "bytes": Path(__file__).stat().st_size},
            "start_wall_ns": start_wall, "end_wall_ns": end_wall, "start_mono_ns": start_mono,
            "end_mono_ns": end_mono, "duration_s": (end_mono - start_mono) / 1e9}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="Fresh JSON output file")
    args = parser.parse_args(argv)
    if args.out.exists() or args.out.is_symlink():
        parser.error("Use a fresh output file; existing audits are never overwritten")
    result = Audit(args.root).run()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"out": str(args.out), "valid": result["valid"], "errors": len(result["errors"]),
                      "live_coverage": result["live_coverage"], "duration_s": result["duration_s"]}))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
