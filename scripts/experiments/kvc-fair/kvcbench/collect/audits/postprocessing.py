"""Independently revalidate collected postprocessing inputs."""
import hashlib
import json
from pathlib import Path
import re

from kvcbench.platform.files import digest
from kvcbench.collect.analysis.batch import analyze
from kvcbench.collect.audits.runtime import verify_runtime_inputs


def verify_deployment_evidence(source, suite, runtime_inputs, scripts):
    from kvcbench.collect.audits.deployment import PROTOCOL, PURE_KV_FLAGS, ROCKS_ARTIFACT, one_flag, validate_plan
    source, suite = Path(source).absolute(), Path(suite).resolve(strict=True)
    root = suite.parent
    if any(path.is_symlink() for path in [source, *source.parents]) or not source.is_file():
        raise ValueError("Deployment audit must be a regular file without symlink traversal")
    raw = json.loads(source.read_bytes())
    helper = Path(scripts) / ("kvcbench/collect/audits/deployment.py" if raw.get("schema_version") == 4 else "batch_deployment_audit.py")
    if (raw.get("schema_version") not in {2, 3, 4} or raw.get("kind") != "datasystem_collected_deployment_audit"
            or raw.get("root") != str(root) or raw.get("workload_protocol") != PROTOCOL
            or raw.get("valid") is not True or raw.get("errors") != []
            or raw.get("inputs_rehashed_after_audit") is not True
            or helper.is_symlink() or not helper.is_file()
            or raw.get("helper") != {"sha256": digest(helper), "bytes": helper.stat().st_size}):
        raise ValueError("Deployment audit root/protocol/success/executed-source identity differs")
    clocks = [raw.get(key) for key in ["start_wall_ns", "end_wall_ns", "start_mono_ns", "end_mono_ns"]]
    if (any(type(value) is not int or value <= 0 for value in clocks) or clocks[3] <= clocks[2]
            or type(raw.get("duration_s")) not in {int, float}
            or not abs(raw["duration_s"] - (clocks[3] - clocks[2]) / 1e9) <= 1e-9):
        raise ValueError("Deployment audit monotonic duration or original clock boundaries are incomplete")
    stage, runtime = verify_runtime_inputs(suite, runtime_inputs)
    pure_kv = raw["schema_version"] >= 3
    if pure_kv and (raw.get("feature_profile") != "pure-kv" or stage.get("feature_profile") != "pure-kv"):
        raise ValueError("The pure-KV deployment audit requires the matching frozen feature profile")
    if stage.get("feature_profile") == "pure-kv" and not pure_kv:
        raise ValueError("A pure-KV stage requires schema3 launch and database-artifact evidence")
    if json.loads((root / "batch-stage.json").read_bytes()) != stage:
        raise ValueError("Deployment audit and current suite refer to different frozen stages")
    expected = {"batch-stage.json", "runtime-inputs/scripts/datasystem/runtime.py",
                "batch-gate-001/r0-datasystem/tcp-configuration.json"}
    clusters, epoch_suites = [], []
    for phase, action, repeats in [("batch-gate-001", "gate", 1), ("batch-smoke-001", "smoke", 1), ("formal-batch-001", "run", 3)]:
        directory = root / phase
        for name in ["batch-stage.json", "plan.json", "summary.json", "events.jsonl"]:
            expected.add(phase + "/" + name)
        if json.loads((directory / "batch-stage.json").read_bytes()) != stage:
            raise ValueError("Deployment audit phase is not from the current frozen stage")
        plan = json.loads((directory / "plan.json").read_bytes())
        validate_plan(plan, repeats)
        summary = json.loads((directory / "summary.json").read_bytes())
        parameter_file = directory / "parameters.json"
        parameters = json.loads(parameter_file.read_bytes()) if parameter_file.exists() else {}
        if parameter_file.exists():
            expected.add(phase + "/parameters.json")
        continued = parameters.get("continue_readonly_errors") is True
        events = [json.loads(line) for line in (directory / "events.jsonl").read_bytes().splitlines() if line.strip()]
        failures = [event.get("identity") for event in events if event.get("event") == "measure_failed"]
        measured = None
        if pure_kv and action != "gate" and (continued or failures or summary.get("measurement_failures")):
            expected |= {path.relative_to(root).as_posix() for path in directory.rglob("*.json")}
            expected.add(phase + "/resources/samples.jsonl")
            checked = analyze(directory)
            measured = {"evidence_verified": checked.get("evidence_verified"), "counts": checked.get("counts"),
                        "issues": checked.get("issues"),
                        "failed_identities": [row["identity"] for row in checked["rows"] if row["status"] == "failed"]}
            if measured["evidence_verified"] is not True or measured["issues"] or failures != measured["failed_identities"]:
                raise ValueError("Deployment audit native failure evidence differs from independent result analysis")
        identities, selected = [], []
        for group in plan:
            cell = f"r{group['repeat']}-{group['product']}"
            epochs = [{"id": "gate"}] if action == "gate" else group["epochs"]
            for epoch in epochs:
                identities.append(cell + "/" + epoch["id"])
                ledger = phase + "/" + cell + ("" if action == "gate" else "/epochs/" + epoch["id"]) + "/epoch-summary.json"
                expected.add(ledger)
                if group["product"] == "datasystem":
                    selected.append(action + "-" + cell + ("" if action == "gate" else "-" + epoch["id"]))
        if (summary.get("action") != action or summary.get("workload_protocol") != PROTOCOL
                or summary.get("valid") is not (not failures) or summary.get("failed") != [] or summary.get("fatal")
                or not summary.get("ended") or summary.get("epochs_expected") != identities
                or summary.get("epochs_completed") != identities
                or failures and (not continued or summary.get("execution_complete") is not True)
                or continued and (summary.get("execution_complete") is not True or summary.get("measurement_failures") != failures)
                or summary.get("completed") != [f"r{group['repeat']}-{group['product']}" for group in plan]):
            raise ValueError("Deployment audit requires complete same-stage gate/smoke/formal epochs")
        epoch_evidence = {"suite": phase, "action": action, "expected_epochs": identities,
                          "epochs_completed": identities, "datasystem_clusters": selected}
        if pure_kv:
            epoch_evidence.update(native_measurement_failures=failures, measurement_evidence=measured,
                                  execution_complete=summary.get("execution_complete", summary.get("valid")))
        epoch_suites.append(epoch_evidence)
        clusters.extend(selected)
    if (len(clusters) != 21 or len(set(clusters)) != 21 or raw.get("expected_clusters") != clusters
            or raw.get("epoch_suites") != epoch_suites):
        raise ValueError("Deployment audit does not cover the actual 21 DataSystem service epochs")
    directories = []
    observed = []
    rows = raw.get("clusters", [])
    if len(rows) != len(clusters) or [row.get("name") for row in rows] != clusters:
        raise ValueError("Deployment audit cluster identities are missing, duplicated or unexpected")
    for row in rows:
        name, live = row["name"], row.get("live", {})
        prefix = "run/" + name
        expected.add(prefix + "/processes.json")
        workers, stdout = row.get("workers", []), row.get("stdout", [])
        if (row.get("valid") is not True or len(workers) != 2 or len(stdout) != 2
                or [item.get("worker_index") for item in workers] != [0, 1]
                or [item.get("worker_index") for item in stdout] != [0, 1]):
            raise ValueError("Deployment audit requires two verified Workers and stdout scans per epoch")
        manifest = json.loads((root / prefix / "processes.json").read_bytes())
        processes = [item for item in manifest.get("processes", []) if item.get("name") in {"worker0", "worker1"}]
        if (manifest.get("run_dir") != stage["root"] + "/" + prefix or len(processes) != 2
                or {item.get("name") for item in processes} != {"worker0", "worker1"}
                or len({item.get("pid") for item in processes}) != 2):
            raise ValueError("Deployment audit launch manifest cluster/Worker identities differ")
        if pure_kv and manifest.get("runtime_profile") != "pure-kv":
            raise ValueError("Deployment audit launch manifest is not the pure-KV runtime")
        for worker, scan in zip(workers, stdout):
            index = worker["worker_index"]
            expected_path = stage["root"] + "/" + prefix + f"/w{index}/rocksdb"
            flag_path, log_path = prefix + f"/w{index}/worker.flags", prefix + f"/worker{index}.stdout.log"
            expected.update([flag_path, log_path])
            if (worker.get("launch_configuration_verified") is not True or worker.get("rocksdb_store_dir") != expected_path
                    or type(worker.get("pid")) is not int or worker["pid"] <= 0
                    or not str(worker.get("start_ticks", "")).isdigit()
                    or worker.get("flagfile", {}).get("path") != flag_path
                    or scan.get("path") != log_path or type(scan.get("matched_errors")) is not int
                    or scan["matched_errors"] != 0 or scan.get("first_match") is not None or scan.get("last_match") is not None):
                raise ValueError("Deployment audit Worker directory/launch identity/error scan differs")
            process = next(item for item in processes if item["name"] == f"worker{index}")
            flags = (root / flag_path).read_text().splitlines()
            if (process.get("pid") != worker["pid"] or process.get("start_ticks") != worker["start_ticks"]
                    or process.get("command", [])[1:] != flags
                    or one_flag(process.get("command"), "rocksdb_store_dir") != expected_path
                    or one_flag(flags, "rocksdb_store_dir") != expected_path
                    or manifest.get("rocksdb_store_dirs", {}).get(f"worker{index}") != expected_path):
                raise ValueError("Deployment audit launch argv/flags do not bind its Worker isolation record")
            if pure_kv:
                switches = {**PURE_KV_FLAGS, "log_monitor": "false", "ipc_through_shared_memory": "false",
                            "enable_rdma": "false", "enable_urma": "false", "l2_cache_type": "none", "spill_size_limit": "0"}
                if worker.get("runtime_profile") != "pure-kv" or any(one_flag(flags, key) != value for key, value in switches.items()):
                    raise ValueError("Deployment audit pure-KV Worker switches are absent or enabled")
                database = root / prefix / f"w{index}/rocksdb"
                if database.is_symlink() or not database.is_dir():
                    raise ValueError("Missing regular collected database directory")
                files = sorted(path for path in database.rglob("*") if path.is_file())
                if any(path.is_symlink() for path in database.rglob("*")) or any(ROCKS_ARTIFACT.fullmatch(path.name) for path in files):
                    raise ValueError("Collected pure-KV directory contains a symlink or RocksDB artifact")
                inventory = worker.get("rocksdb_directory", {})
                if (inventory.get("database_artifacts") != [] or len(inventory.get("files", [])) != len(files)
                        or [record.get("path") for record in inventory["files"]] != [path.relative_to(root).as_posix() for path in files]):
                    raise ValueError("Deployment audit final directory inventory does not match collected entries")
                for record in inventory["files"]:
                    expected.add(record["path"])
                    if any(record.get(key) != raw.get("inputs", {}).get(record["path"], {}).get(key) for key in ["sha256", "bytes"]):
                        raise ValueError("Deployment database directory entry does not bind its input bytes")
            directories.append(Path(expected_path))
        if name == "gate-r0-datasystem":
            if live.get("observed") is not True or live.get("status") != "verified" or live.get("path") != "batch-gate-001/r0-datasystem/tcp-configuration.json":
                raise ValueError("Deployment audit is missing the saved gate live observation")
            observed.append(name)
        elif live.get("observed") is not False or live.get("status") != "unobserved":
            raise ValueError("Deployment audit must not invent live coverage for other epochs")
    if len(set(directories)) != 42 or any(left in right.parents or right in left.parents for index, left in enumerate(directories) for right in directories[index + 1:]):
        raise ValueError("DataSystem RocksDB directories must be 42 distinct nonoverlapping paths")
    if raw.get("live_coverage") != {"observed_clusters": 1, "expected_clusters": 21, "required_gate_live_observed": True}:
        raise ValueError("Deployment audit live coverage must remain one saved gate out of 21 epochs")
    inputs = raw.get("inputs", {})
    if not isinstance(inputs, dict) or set(inputs) != expected:
        raise ValueError("Deployment audit input inventory does not match every actual epoch/launch/log source")
    for name, record in inputs.items():
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or name in {"", "."}
                or not isinstance(record, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))
                or type(record.get("bytes")) is not int or record["bytes"] < 0):
            raise ValueError("Unsafe deployment audit input path or SHA256/bytes record")
        path = root / relative
        if (any(part.is_symlink() for part in [path, *path.parents]) or not path.is_file()
                or path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]):
            raise ValueError("Deployment audit input SHA256/bytes mismatch: " + name)
    runtime_name = "runtime-inputs/scripts/datasystem/runtime.py"
    expected_runtime = {"path": runtime_name, **inputs[runtime_name]}
    matched = [record for record in runtime if record["relative"] == "scripts/datasystem/runtime.py"]
    if (raw.get("runtime_source") != expected_runtime or len(matched) != 1
            or any(matched[0][key] != inputs[runtime_name][key] for key in ["sha256", "bytes"])):
        raise ValueError("Deployment audit DataSystem launcher is not the measured frozen runtime")
    for row in rows:
        for worker, scan in zip(row["workers"], row["stdout"]):
            if any(worker["flagfile"].get(key) != inputs[worker["flagfile"]["path"]][key] or scan.get(key) != inputs[scan["path"]][key] for key in ["sha256", "bytes"]):
                raise ValueError("Deployment audit Worker flag/stdout records differ from bound inputs")
    analysis_helpers = raw.get("analysis_helpers", {})
    from kvcbench.collect.provenance import analysis_sources
    helper_sources = analysis_sources(scripts, legacy=raw["schema_version"] < 4)
    expected_helpers = set(helper_sources) if any(row.get("measurement_evidence") is not None for row in epoch_suites) else set()
    if set(analysis_helpers) != expected_helpers:
        raise ValueError("Deployment audit executed analysis source inventory is incomplete")
    executed_sources = []
    for name, expected_helper in analysis_helpers.items():
        path = helper_sources[name]
        if (Path(expected_helper.get("path", "")).resolve() != path.resolve() or path.is_symlink() or not path.is_file()
                or expected_helper.get("sha256") != digest(path) or expected_helper.get("bytes") != path.stat().st_size):
            raise ValueError("Deployment audit analysis dependency differs from its executed source: " + name)
        executed_sources.append({"source": str(path), "relative": "deployment-audit/source/" + name,
                                 "sha256": digest(path), "bytes": path.stat().st_size})
    records = [{"source": str(path), "relative": "deployment-audit/" + name,
                "sha256": digest(path), "bytes": path.stat().st_size}
               for path, name in [(source, "deployment-audit.json"), (helper, "source/batch_deployment_audit.py")]]
    records.extend(executed_sources)
    frozen_name = next((name for name in ["batch_deployment_audit.py", "scripts/batch_deployment_audit.py"] if name in stage["inputs"]), None)
    frozen_helper = stage["inputs"].get(frozen_name)
    return {"verified": True, "path": records[0]["relative"], "sha256": records[0]["sha256"], "bytes": records[0]["bytes"],
            "clusters": len(clusters), "workers": len(directories), "matched_errors": 0,
            "feature_profile": raw.get("feature_profile", "legacy-isolated"), "database_artifacts_absent": pure_kv,
            "live_coverage": raw["live_coverage"], "input_files_revalidated": len(inputs),
            "duration_s": raw["duration_s"], "executed_helper": {"path": records[1]["relative"], **raw["helper"]},
            "executed_analysis_helpers": [{key: record[key] for key in ["relative", "sha256", "bytes"]} for record in executed_sources],
            "frozen_helper": frozen_helper, "frozen_helper_path": frozen_name,
            "executed_helper_matches_frozen": frozen_helper == raw["helper"],
            "scope": "Rebound saved launch flags/manifests, final collected directory entries and full stdout scans for 21 DS epochs / 42 distinct RocksDB paths. Only the gate has a recorded live procfs observation (1/21); other 20 epochs remain live-unobserved. No remote inode/symlink or continuous-liveness proof is inferred. Native read failures retain their separate result validity. Executed postprocessor/dependency sources are separate from frozen stage copies."}, records



def verify_resource_evidence(directory, suite, analysis, scripts):
    from kvcbench.collect.audits.resources import markdown
    directory, suite = Path(directory).resolve(strict=True), Path(suite).resolve(strict=True)
    source, narrative = directory / "resource-audit.json", directory / "resource-audit.md"
    if any(path.is_symlink() or not path.is_file() for path in [source, narrative]):
        raise ValueError("The independent resource audit requires regular JSON and Markdown files")
    raw = json.loads(source.read_bytes())
    helper = Path(scripts) / ("kvcbench/collect/audits/resources.py" if raw.get("schema_version") == 2 else "batch_resource_audit.py")
    if (raw.get("schema_version") not in {1, 2} or raw.get("kind") != "native_batch_collected_resource_audit"
            or Path(raw.get("suite", "")).resolve() != suite or raw.get("helper_sha256") != digest(helper)
            or raw.get("suite_valid_recorded") is not analysis["summary"].get("valid")):
        raise ValueError("Resource audit suite/source identity differs from the current evidence")
    if narrative.read_bytes() != markdown(raw).encode("utf-8"):
        raise ValueError("Resource audit Markdown does not match its original JSON")
    expected = {"resources/samples.jsonl", "summary.json"}
    expected |= {name for name in ["resources/interference.jsonl", "resources/monitor-manifest.json"] if (suite / name).exists()}
    result_paths = {path.relative_to(suite).as_posix() for path in suite.glob("r*-*/b*-*.json")}
    expected |= result_paths
    seen = set()
    for record in raw.get("inputs", []):
        name = record.get("path", "")
        relative = Path(name)
        if (name in seen or name not in expected or relative.is_absolute() or ".." in relative.parts
                or relative.as_posix() != name or name in {"", "."}):
            raise ValueError("Resource audit input inventory is unsafe or duplicated")
        seen.add(name)
        path = suite / relative
        if (path.is_symlink() or any(parent.is_symlink() for parent in path.parents if suite in parent.parents)
                or not path.is_file() or type(record.get("bytes")) is not int or path.stat().st_size != record.get("bytes") or digest(path) != record.get("sha256")):
            raise ValueError("Resource audit input SHA256/bytes mismatch: " + name)
    if seen != expected:
        raise ValueError("Resource audit input set does not match every actual sample/result source")
    windows, unsupported, unmeasured = {}, [], []
    for name in sorted(result_paths):
        result = json.loads((suite / name).read_bytes())
        if result.get("status") == "not_supported":
            unsupported.append(name)
            continue
        start, end = result.get("measure_start_mono_ns"), result.get("measure_end_mono_ns")
        if type(start) is int and type(end) is int and end > start:
            windows[name[:-5]] = result
        else:
            unmeasured.append(name)
    audited = raw.get("windows", [])
    if (len(audited) != len(windows) or {row.get("identity") for row in audited} != set(windows)
            or raw.get("sdk_window_count") != len(windows) or sorted(raw.get("unsupported", [])) != unsupported
            or raw.get("unsupported_count") != len(unsupported) or sorted(raw.get("unmeasured_results", [])) != unmeasured
            or raw.get("valid_sdk_window_count") != sum(result.get("valid") is True for result in windows.values())
            or raw.get("invalid_sdk_window_count") != sum(result.get("valid") is not True for result in windows.values())
            or raw.get("suite_monitor_exit") != analysis["summary"].get("monitor_exit")
            or raw.get("suite_interference_exit") != analysis["summary"].get("interference_exit")):
        raise ValueError("Resource audit SDK identities or N/A/unmeasured sets differ")
    for row in audited:
        result = windows[row["identity"]]
        if (row.get("start_mono_ns") != result["measure_start_mono_ns"] or row.get("end_mono_ns") != result["measure_end_mono_ns"]
                or any(row.get(target) != result.get(original) for target, original in
                       [("result_valid_recorded", "valid"), ("result_success_recorded", "success"),
                        ("result_failures_recorded", "failures"), ("result_fatal_recorded", "fatal")])):
            raise ValueError("Resource audit SDK boundary/result identity differs")
    records = [{"source": str(path), "relative": "resource-audit/" + name,
                "sha256": digest(path), "bytes": path.stat().st_size}
               for path, name in [(source, "resource-audit.json"), (narrative, "resource-audit.md"), (helper, "source/batch_resource_audit.py")]]
    return {"verified": True, "path": records[0]["relative"], "markdown": records[1]["relative"],
            "sha256": records[0]["sha256"], "bytes": records[0]["bytes"], "sdk_windows": len(windows),
            "input_files_revalidated": len(expected), "scope": raw["scope"],
            "summary_scope": "Independent read-only postprocessing of the original sample stream; no new samples, synthetic intervals or performance repetitions. Parent/monitor and host/affinity estimates retain their separate scopes."}, records
