"""Offline tamper fixtures for deployment isolation and bounded log scanning."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import batch_deployment_audit as audit


REMOTE = "/home/worktrees/kvc-batch-tcp-20261020-r5"


def make_plan(repeats):
    rows = []
    for repeat in range(repeats):
        batches = audit.BATCHES[repeat % 4:] + audit.BATCHES[:repeat % 4]
        products = audit.PRODUCTS[repeat % 3:] + audit.PRODUCTS[:repeat % 3]
        for product in products:
            def supported(batch, operation):
                return product != "lmcache" or operation.startswith("exist") or batch == 1 and operation in {"put", "get"}
            operations = [{"batch": batch, "operation": operation, "supported": supported(batch, operation), "epoch": "read"}
                          for batch in batches for operation in ["upsert", "get", "exist_hit", "exist_miss"]]
            operations += [{"batch": batch, "operation": "put", "supported": supported(batch, "put"), "epoch": f"put-b{batch}"}
                           for batch in batches]
            epochs = []
            for name in ["read", *[f"put-b{batch}" for batch in batches]]:
                members = [spec for spec in operations if spec["epoch"] == name and spec["supported"]]
                if members:
                    epochs.append({"id": name, "kind": "read" if name == "read" else "fresh_put", "operations": members})
            rows.append({"repeat": repeat, "product": product, "batches": batches,
                         "workload_protocol": audit.PROTOCOL, "operations": operations, "epochs": epochs})
    return rows


CLUSTERS = ["gate-r0-datasystem"] + [action + f"-r{group['repeat']}-datasystem-" + epoch["id"]
    for action, repeats in [("smoke", 1), ("run", 3)] for group in make_plan(repeats)
    if group["product"] == "datasystem" for epoch in group["epochs"]]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(root):
    source = root / "runtime-inputs/scripts/datasystem/runtime.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fixture-runtime-source")
    stage = {"root": REMOTE, "feature_profile": "pure-kv", "inputs": {"scripts/datasystem/runtime.py":
             {"sha256": audit.digest(source), "bytes": source.stat().st_size}}}
    save(root / "batch-stage.json", stage)
    for phase, action, repeats in [("batch-gate-001", "gate", 1), ("batch-smoke-001", "smoke", 1), ("formal-batch-001", "run", 3)]:
        save(root / phase / "batch-stage.json", stage)
        plan = make_plan(repeats)
        save(root / phase / "plan.json", plan)
        expected, commands = [], []
        for group in plan:
            cell = f"r{group['repeat']}-{group['product']}"
            epochs = [{"id": "gate", "kind": "correctness", "operations": []}] if action == "gate" else group["epochs"]
            for epoch in epochs:
                identity = cell + "/" + epoch["id"]
                expected.append(identity)
                name = action + "-" + cell + ("" if action == "gate" else "-" + epoch["id"])
                remote_dir = REMOTE + "/run/" + name
                directory = Path(phase) / cell
                if action != "gate":
                    directory /= "epochs/" + epoch["id"]
                start = len(expected) * 100_000_000
                save(root / directory / "epoch-summary.json", {"identity": identity, "kind": epoch["kind"],
                    "operations": epoch["operations"], "run_dir": remote_dir, "workload_protocol": audit.PROTOCOL,
                    "valid": True, "start_wall_ns": start, "end_wall_ns": start + 10_000_000,
                    "start_mono_ns": start, "end_mono_ns": start + 10_000_000, "duration_s": .01,
                    "drained": {"members": {f"/sys/fs/cgroup/kvcfair20261020/{role}/cgroup.procs": [] for role in ["server", "client"]}, "populated": []}})
                if group["product"] == "datasystem":
                    for position, spec in enumerate(epoch["operations"]):
                        for event_kind in ["measure_prepare", "measure_complete"]:
                            commands.append({"event": event_kind, "group": cell, "epoch": epoch["id"],
                                "batch": spec["batch"], "operation": spec["operation"],
                                "wall_ns": start + 4_000_000 + position * 1000})
                    for index, command in enumerate(["stop", "start", "stop"]):
                        commands.append({"event": "command", "log": (Path(REMOTE) / directory / "lifecycle.log").as_posix(),
                            "returncode": 0, "start_wall_ns": start + (index + 1) * 1_000_000,
                            "end_wall_ns": start + (index + 1) * 1_000_000 + 100_000,
                            "argv": ["python3", REMOTE + "/scripts/datasystem/runtime.py", command, "--run-dir", remote_dir]})
        save(root / phase / "summary.json", {"action": action, "workload_protocol": audit.PROTOCOL,
            "valid": True, "failed": [], "ended": "completed-fixture", "epochs_expected": expected,
            "epochs_completed": expected, "completed": [f"r{group['repeat']}-{group['product']}" for group in plan]})
        (root / phase / "events.jsonl").write_text("\n".join(json.dumps(event) for event in commands) + "\n")
    for number, name in enumerate(CLUSTERS):
        directory = root / "run" / name
        remote_dir = REMOTE + "/run/" + name
        manifest = {"run_dir": remote_dir, "runtime_profile": "pure-kv", "rocksdb_store_dirs": {}, "processes": []}
        observations = []
        for index in range(2):
            database = remote_dir + f"/w{index}/rocksdb"
            feature_flags = {**audit.PURE_KV_FLAGS, "log_monitor": "false", "ipc_through_shared_memory": "false",
                             "enable_rdma": "false", "enable_urma": "false", "l2_cache_type": "none", "spill_size_limit": "0"}
            flags = ["--worker_address=127.0.0.1:" + str(31531 + index),
                     *[f"--{key}={value}" for key, value in feature_flags.items()], "--rocksdb_store_dir=" + database]
            path = directory / f"w{index}/worker.flags"
            path.parent.mkdir(parents=True)
            (path.parent / "rocksdb/eviction_policy").mkdir(parents=True)
            (path.parent / "rocksdb/eviction_policy/policy-state.json").write_text('{"fixture":true}')
            path.write_text("\n".join(flags) + "\n")
            process = {"name": f"worker{index}", "pid": 1000 + number * 2 + index,
                       "start_ticks": str(9000 + number * 2 + index),
                       "command": [REMOTE + "/datasystem/output/datasystem/service/datasystem_worker", *flags]}
            manifest["processes"].append(process)
            manifest["rocksdb_store_dirs"][process["name"]] = database
            observations.append({"worker_index": index, "pid": process["pid"], "start_ticks": process["start_ticks"],
                                 "live_argv": process["command"], "path": remote_dir + f"/w{index}/worker.flags",
                                 "sha256": audit.digest(path)})
            (directory / f"worker{index}.stdout.log").write_bytes(b"WARNING RocksDB compaction was slow\n")
        save(directory / "processes.json", manifest)
        if number == 0:
            save(root / "batch-gate-001/r0-datasystem/tcp-configuration.json",
                 {"source": {"sha": audit.SOURCE_SHA}, "workers": observations,
                  "processes_sha256": audit.digest(directory / "processes.json")})


class DeploymentAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "collected"
        fixture(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def result(self):
        return audit.Audit(self.root).run()

    def manifest(self, cluster=2):
        return self.root / "run" / CLUSTERS[cluster] / "processes.json"

    def test_dynamic_twenty_one_clusters_with_only_gate_live_is_honest_success(self):
        result = self.result()
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(len(result["clusters"]), 21)
        self.assertEqual(result["live_coverage"]["observed_clusters"], 1)
        self.assertEqual(result["live_coverage"]["expected_clusters"], 21)
        self.assertEqual([row["live"]["status"] for row in result["clusters"]], ["verified", *["unobserved"] * 20])
        paths = [worker["rocksdb_store_dir"] for row in result["clusters"] for worker in row["workers"]]
        self.assertEqual(len(set(paths)), 42)
        self.assertEqual([len(suite["expected_epochs"]) for suite in result["epoch_suites"]], [3, 12, 36])
        self.assertTrue(result["inputs_rehashed_after_audit"])

    def test_missing_repeat_is_not_a_completed_deployment_proof(self):
        self.manifest(20).unlink()
        self.assertFalse(self.result()["valid"])

    def test_pure_kv_profile_and_all_flags_are_required(self):
        path = self.manifest()
        original = json.loads(path.read_text())
        for key, value in audit.PURE_KV_FLAGS.items():
            data = copy.deepcopy(original)
            data["processes"][0]["command"] = [item.replace(f"--{key}={value}", f"--{key}=wrong")
                                               for item in data["processes"][0]["command"]]
            save(path, data)
            (path.parent / "w0/worker.flags").write_text("\n".join(data["processes"][0]["command"][1:]) + "\n")
            self.assertFalse(self.result()["valid"], key)
        data = copy.deepcopy(original)
        data.pop("runtime_profile")
        save(path, data)
        self.assertFalse(self.result()["valid"])

    def test_rocks_database_artifacts_rejected_but_eviction_state_retained(self):
        database = self.manifest().parent / "w0/rocksdb"
        self.assertTrue(self.result()["valid"])
        for name in ["000001.sst", "MANIFEST-000001", "CURRENT", "000001.log", "LOG", "WAL"]:
            path = database / name
            path.write_bytes(b"fixture-database-artifact")
            result = self.result()
            self.assertFalse(result["valid"], name)
            self.assertTrue(any(error["code"] == "rocksdb-database-artifacts" for error in result["errors"]))
            path.unlink()
        result = self.result()
        self.assertTrue(result["valid"], result["errors"])
        files = result["clusters"][2]["workers"][0]["rocksdb_directory"]["files"]
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0]["path"].endswith("eviction_policy/policy-state.json"))

    def readonly_failure(self):
        phase = self.root / "formal-batch-001"
        save(phase / "parameters.json", {"continue_readonly_errors": True, "workload_protocol": audit.PROTOCOL,
                                          "root": REMOTE, "action": "run"})
        (phase / "resources").mkdir()
        (phase / "resources/samples.jsonl").write_text("")
        failure = "r0-datasystem/b16-exist_hit"
        events = [json.loads(line) for line in (phase / "events.jsonl").read_text().splitlines()]
        for event in events:
            if (event.get("event") == "measure_complete" and event.get("group") == "r0-datasystem"
                    and event.get("batch") == 16 and event.get("operation") == "exist_hit"):
                event.update(event="measure_failed", identity=failure, returncode=1)
        (phase / "events.jsonl").write_text("\n".join(json.dumps(row) for row in events) + "\n")
        summary = json.loads((phase / "summary.json").read_text())
        summary.update(valid=False, execution_complete=True, measurement_failures=[failure])
        save(phase / "summary.json", summary)
        for path in phase.glob("r*-*/epochs/*/epoch-summary.json"):
            row = json.loads(path.read_text())
            failed = [failure] if row["identity"] == "r0-datasystem/read" else []
            row.update(valid=not failed, execution_complete=True, measurement_failures=failed)
            save(path, row)
        return phase, failure

    def test_verified_native_failure_kept_distinct_from_successful_deployment_audit(self):
        phase, failure = self.readonly_failure()
        verified = {"evidence_verified": True, "counts": {"valid": 149, "failed": 1, "not_supported": 30},
                    "issues": [], "rows": [{"identity": failure, "status": "failed"}]}
        with patch("batch_analyze.analyze", return_value=verified) as analyze:
            result = self.result()
        analyze.assert_called_once_with(phase)
        self.assertTrue(result["valid"], result["errors"])
        formal = result["epoch_suites"][-1]
        self.assertEqual(formal["native_measurement_failures"], [failure])
        self.assertTrue(formal["execution_complete"])
        self.assertTrue(formal["measurement_evidence"]["evidence_verified"])
        self.assertEqual(formal["measurement_evidence"]["counts"]["failed"], 1)
        self.assertEqual(set(result["analysis_helpers"]), {"batch_analyze", "batch_epoch_verify", "benchmark", "report"})
        for row in result["analysis_helpers"].values():
            path = Path(row["path"])
            self.assertEqual(row["bytes"], path.stat().st_size)
            self.assertEqual(row["sha256"], audit.digest(path))

    def test_real_analyzer_rejects_claimed_failure_without_raw_evidence(self):
        self.readonly_failure()
        result = self.result()
        self.assertFalse(result["valid"])
        self.assertTrue(any(error["code"] == "measurement-evidence" for error in result["errors"]))

    def test_failure_evidence_requires_analyzer_pass_and_exact_failure_identity(self):
        _, failure = self.readonly_failure()
        for verified in [{"evidence_verified": False, "issues": ["fixture-tamper"], "rows": []},
                         {"evidence_verified": True, "issues": [], "rows": [{"identity": "other-case", "status": "failed"}]}]:
            with patch("batch_analyze.analyze", return_value=verified):
                self.assertFalse(self.result()["valid"])
        row = self.root / "formal-batch-001/r0-datasystem/epochs/read/epoch-summary.json"
        data = json.loads(row.read_text())
        data["measurement_failures"] = []
        save(row, data)
        with patch("batch_analyze.analyze", return_value={"evidence_verified": True, "issues": [],
                                                       "rows": [{"identity": failure, "status": "failed"}]}):
            self.assertFalse(self.result()["valid"])

    def test_read_epoch_cannot_include_fresh_put_or_unsupported_padding(self):
        path = self.root / "formal-batch-001/plan.json"
        original = json.loads(path.read_text())
        rows = copy.deepcopy(original)
        rows[0]["epochs"][0]["operations"].append({"batch": 1, "operation": "put", "supported": True, "epoch": "read"})
        save(path, rows)
        self.assertFalse(self.result()["valid"])
        rows = copy.deepcopy(original)
        lm = next(group for group in rows if group["product"] == "lmcache")
        lm["epochs"].append({"id": "put-b16", "kind": "fresh_put", "operations":
            [{"batch": 16, "operation": "put", "supported": True, "epoch": "put-b16"}]})
        save(path, rows)
        self.assertFalse(self.result()["valid"])

    def test_epoch_completed_identity_cannot_be_missing_or_duplicated(self):
        path = self.root / "formal-batch-001/summary.json"
        original = json.loads(path.read_text())
        for completed in [original["epochs_completed"][:-1], [*original["epochs_completed"], original["epochs_completed"][0]]]:
            data = copy.deepcopy(original)
            data["epochs_completed"] = completed
            save(path, data)
            self.assertFalse(self.result()["valid"])

    def test_epoch_requires_own_fresh_run_and_actual_drain(self):
        path = self.root / "formal-batch-001/r0-datasystem/epochs/put-b1/epoch-summary.json"
        original = json.loads(path.read_text())
        for field, value in [("run_dir", REMOTE + "/run/run-r0-datasystem-read"),
                             ("drained", {"members": {}, "populated": []}), ("valid", False)]:
            data = copy.deepcopy(original)
            data[field] = value
            save(path, data)
            self.assertFalse(self.result()["valid"])

    def test_stop_failure_and_operation_in_wrong_epoch_are_rejected(self):
        path = self.root / "batch-smoke-001/events.jsonl"
        original = [json.loads(line) for line in path.read_text().splitlines()]
        for modify in ["failed-stop", "wrong-epoch"]:
            data = copy.deepcopy(original)
            if modify == "failed-stop":
                next(row for row in data if row["event"] == "command")["returncode"] = 1
            else:
                next(row for row in data if row["event"] == "measure_prepare")["epoch"] = "put-b1"
            path.write_text("\n".join(json.dumps(row) for row in data) + "\n")
            self.assertFalse(self.result()["valid"])

    def test_legacy_plan_still_scans_and_preserves_actual_rocks_errors(self):
        for phase in ["batch-gate-001", "batch-smoke-001", "formal-batch-001"]:
            save(self.root / phase / "plan.json", [{"repeat": 0, "product": "datasystem"}])
        log = self.manifest().parent / "worker0.stdout.log"
        log.write_bytes(b"ERROR rocks_store.cpp:324 Async Put failed: Sst file size mismatch: /root/db/000029.sst\n")
        result = self.result()
        self.assertFalse(result["valid"])
        self.assertEqual(sum(item["matched_errors"] for row in result["clusters"] for item in row.get("stdout", [])), 1)
        self.assertTrue(any(error["code"] == "rocksdb-file-errors" for error in result["errors"]))

    def test_malformed_manifest_is_a_saved_invalid_result(self):
        save(self.manifest(), ["not-a-manifest"])
        result = self.result()
        self.assertFalse(result["valid"])
        self.assertTrue(any(row["code"] == "launch-or-live-evidence" for row in result["errors"]))

    def test_runtime_bytes_and_phase_stage_must_match_frozen_inputs(self):
        path = self.root / "runtime-inputs/scripts/datasystem/runtime.py"
        old = path.read_bytes()
        path.write_bytes(b"different-runtime")
        self.assertFalse(self.result()["valid"])
        path.write_bytes(old)
        save(self.root / "formal-batch-001/batch-stage.json", {"root": "/another-root"})
        result = self.result()
        self.assertFalse(result["valid"])
        self.assertTrue(any(error["code"] == "phase-stage-mismatch" for error in result["errors"]))

    def test_shared_path_even_with_consistent_manifest_and_flags_is_rejected(self):
        path = self.manifest()
        data = json.loads(path.read_text())
        shared = data["rocksdb_store_dirs"]["worker0"]
        data["rocksdb_store_dirs"]["worker1"] = shared
        data["processes"][1]["command"][-1] = "--rocksdb_store_dir=" + shared
        save(path, data)
        flags = path.parent / "w1/worker.flags"
        flags.write_text("\n".join(data["processes"][1]["command"][1:]) + "\n")
        self.assertFalse(self.result()["valid"])

    def test_outside_and_non_normalized_database_paths_rejected(self):
        for bad in ["/tmp/old-db", REMOTE + "/run/../old-db"]:
            path = self.manifest()
            data = json.loads(path.read_text())
            data["processes"][0]["command"][-1] = "--rocksdb_store_dir=" + bad
            save(path, data)
            self.assertFalse(self.result()["valid"])

    def test_duplicate_or_missing_explicit_flag_is_rejected(self):
        path = self.manifest()
        original = json.loads(path.read_text())
        for argv in [original["processes"][0]["command"][:-1],
                     [*original["processes"][0]["command"], original["processes"][0]["command"][-1]]]:
            data = copy.deepcopy(original)
            data["processes"][0]["command"] = argv
            save(path, data)
            self.assertFalse(self.result()["valid"])

    def test_flagfile_and_recorded_launch_disagreement_rejected(self):
        path = self.manifest().parent / "w1/worker.flags"
        path.write_text(path.read_text() + "--minloglevel=0\n")
        self.assertFalse(self.result()["valid"])

    def test_gate_live_pid_ticks_and_argv_are_bound(self):
        path = self.root / "batch-gate-001/r0-datasystem/tcp-configuration.json"
        original = json.loads(path.read_text())
        for key, value in [("pid", 900000), ("start_ticks", "wrong"), ("live_argv", ["fake-worker"])]:
            data = copy.deepcopy(original)
            data["workers"][0][key] = value
            save(path, data)
            self.assertFalse(self.result()["valid"])

    def test_gate_live_absence_or_manifest_sha_tamper_cannot_pass(self):
        path = self.root / "batch-gate-001/r0-datasystem/tcp-configuration.json"
        data = json.loads(path.read_text())
        data["processes_sha256"] = "0" * 64
        save(path, data)
        self.assertFalse(self.result()["valid"])
        path.unlink()
        self.assertFalse(self.result()["valid"])

    def test_sst_errors_stream_count_first_last_and_full_binary_sha(self):
        path = self.manifest().parent / "worker0.stdout.log"
        content = (b"ERROR rocks_store.cpp:324 Async Put failed: Sst file size mismatch: /root/db/000029.sst\n"
                   b"WARNING RocksDB slow compaction\x00\xff\n"
                   b"ERROR open /root/db/000029.ldb: No such file or directory\n")
        path.write_bytes(content)
        result = self.result()
        self.assertFalse(result["valid"])
        scan = result["clusters"][2]["stdout"][0]
        self.assertEqual(scan["matched_errors"], 2)
        self.assertEqual((scan["first_match"]["line"], scan["last_match"]["line"]), (1, 3))
        self.assertEqual(scan["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(scan["bytes"], len(content))
        self.assertEqual(path.read_bytes(), content)

    def test_symlink_evidence_refused_and_not_read_as_valid(self):
        path = self.manifest().parent / "worker1.stdout.log"
        external = Path(self.temporary.name) / "external.log"
        external.write_bytes(b"unrelated\n")
        path.unlink()
        path.symlink_to(external)
        self.assertFalse(self.result()["valid"])

    def test_post_scan_input_mutation_is_reported(self):
        original = audit.digest
        calls = {}
        def changed(path):
            key = str(path)
            calls[key] = calls.get(key, 0) + 1
            if path.name == "worker0.stdout.log":
                return "0" * 64
            return original(path)
        with patch.object(audit, "digest", side_effect=changed):
            result = self.result()
        self.assertFalse(result["valid"])
        self.assertTrue(any(row["code"] == "input-changed" for row in result["errors"]))

    def test_cli_outputs_invalid_audit_and_refuses_overwrite(self):
        self.manifest(4).unlink()
        out = Path(self.temporary.name) / "audit.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(audit.main(["--root", str(self.root), "--out", str(out)]), 1)
        original = out.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            audit.main(["--root", str(self.root), "--out", str(out)])
        self.assertEqual(out.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
