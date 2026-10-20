"""Synthetic R5-shaped native-read failures; no product data or execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import batch_analyze as subject
import batch_runner
from tests.unit.collect.test_batch_analysis import raw_result, refresh_totals


ERROR = "RuntimeError: code: [RPC deadline exceeded], msg: [synthetic fixture only]"


def sync_failure(raw):
    failed = sum(client["failures"] for client in raw["clients"])
    raw.update(failures=failed, failed_keys_upper_bound=failed * raw["parameters"]["batch"],
               partial_success_keys_unknown=bool(failed))
    refresh_totals(raw)
    return raw


def failed_result(operation="exist_hit", batch=16):
    raw = raw_result(operation, batch)
    raw["valid"] = False
    raw["parameters"]["put_warmup_keys"] = 32768
    raw["duration_s"] = 6.4
    raw["measure_end_mono_ns"] = raw["measure_start_mono_ns"] + 6400000000
    for client in raw["clients"]:
        client.update(failures=1, errors=[ERROR], last_ns=raw["measure_end_mono_ns"])
    for warm in raw["warmup"]:
        warm.update(put_inserted_keys=0, put_verified_keys=0)
    return sync_failure(raw)


def zero_success_result(operation="exist_hit", batch=16):
    raw = failed_result(operation, batch)
    for client in raw["clients"]:
        client.update(success=0, histogram={}, latency_sum_ns=0, latency_max_ns=0,
                      per_second={}, validated_keys=0)
    for post in raw["post_validation"]:
        post.update(post_validated_keys=0, restored_verified_keys=0)
    raw.update(mean_us=None, max_us=0.0)
    return sync_failure(raw)


def measured(raw, context=None):
    row = {"identity": f"r0-datasystem/b{raw['parameters']['batch']}-{raw['parameters']['op']}",
           "product": "datasystem", "repeat": 0, "batch": raw["parameters"]["batch"],
           "operation": raw["parameters"]["op"], "supported": True}
    return subject.measured_row(raw, row, [], [], context or {"continue_readonly_errors": True, "isolated_epochs": True})


class ZeroSuccessReadFailureTests(unittest.TestCase):
    def test_eight_native_errors_with_no_success_preserve_complete_failed_evidence(self):
        for operation in ["get", "exist_hit", "exist_miss"]:
            with self.subTest(operation=operation):
                raw = zero_success_result(operation)
                self.assertEqual(raw["histogram"], {})
                self.assertEqual([raw[name] for name in ["p50", "p95", "p99"]], [None] * 3)
                self.assertEqual(raw["process_exit_codes"], [0] * 8)
                self.assertEqual(len(raw["closed"]), 8)
                row = measured(raw)
                self.assertEqual(row["issues"], [])
                self.assertEqual(row["status"], "failed")
                self.assertFalse(row["valid"])
                self.assertTrue(row["evidence_verified"])
                self.assertEqual((row["success"], row["success_keys"], row["failures"]), (0, 0, 8))
                self.assertEqual(len(row["native_errors"]), 8)
                self.assertEqual(row["duration_s"], 6.4)
                self.assertTrue(row["short_window"])
                self.assertEqual([row[name + "_us"] for name in ["p50", "p95", "p99"]], [None] * 3)

    def test_zero_success_is_invalid_without_authorization_or_complete_native_error_evidence(self):
        for mode in ["unauthorized", "no_error", "fatal", "bad_exit", "missing_ack", "bad_warm", "count_mismatch"]:
            with self.subTest(mode=mode):
                raw = zero_success_result()
                context = {"continue_readonly_errors": mode != "unauthorized", "isolated_epochs": True}
                if mode == "no_error":
                    raw["clients"][0]["errors"] = []
                elif mode == "fatal":
                    raw["fatal"] = "synthetic fatal"
                elif mode == "bad_exit":
                    raw["process_exit_codes"][0] = 1
                elif mode == "missing_ack":
                    raw["closed"].pop()
                elif mode == "bad_warm":
                    raw["warmup"][0]["warm"] = False
                elif mode == "count_mismatch":
                    raw["failures"] = 7
                row = measured(raw, context)
                self.assertEqual(row["status"], "invalid")
                self.assertFalse(row["evidence_verified"])
        for operation in ["put", "upsert"]:
            with self.subTest(operation=operation):
                self.assertEqual(measured(zero_success_result(operation))["status"], "invalid")

    def test_zero_success_cannot_invent_a_latency_percentile(self):
        raw = zero_success_result()
        raw["p99"] = {"lower_us": 1, "upper_us": 2}
        row = measured(raw)
        self.assertEqual(row["status"], "invalid")
        self.assertIn("p99 merged bucket mismatch", row["issues"])

    def test_zero_success_failed_repetition_never_becomes_zero_on_main_chart(self):
        rows = []
        for repeat in range(3):
            row = measured(zero_success_result() if repeat == 1 else raw_result("exist_hit", 16),
                           {"continue_readonly_errors": True})
            row["repeat"] = repeat
            rows.append(row)
        result = subject.aggregate(rows, 3)[0]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["valid_repeats"], 2)
        self.assertTrue(all(metric[key] is None for metric in result["metrics"].values()
                            for key in ["median", "min", "max", "cv_percent"]))


class ReadFailureTests(unittest.TestCase):
    def test_native_read_failure_is_verified_failed_not_valid_performance(self):
        for operation in ["get", "exist_hit", "exist_miss"]:
            with self.subTest(operation=operation):
                result = measured(failed_result(operation))
                self.assertEqual(result["issues"], [])
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["valid"])
                self.assertTrue(result["evidence_verified"])
                self.assertEqual(result["failures"], 8)
                self.assertEqual(len(result["native_errors"]), 8)

    def test_default_and_nonboolean_continue_flags_cannot_reclassify_failure(self):
        for flag in [None, False, 1, "true"]:
            with self.subTest(flag=flag):
                result = measured(failed_result(), {"isolated_epochs": True, "continue_readonly_errors": flag})
                self.assertEqual(result["status"], "invalid")
                self.assertFalse(result["evidence_verified"])

    def test_fatal_process_exit_warmup_and_incomplete_shapes_are_invalid(self):
        changes = [lambda raw: raw.update(fatal="synthetic fatal"),
                   lambda raw: raw["process_exit_codes"].__setitem__(0, 1),
                   lambda raw: raw["warmup"][0].update(warm=False),
                   lambda raw: raw["closed"].pop(),
                   lambda raw: raw["setup"][0].update(client=1),
                   lambda raw: raw["post_validation"][0].update(fatal="failed cleanup")]
        for change in changes:
            with self.subTest(change=change):
                raw = failed_result();change(raw)
                self.assertEqual(measured(raw)["status"], "invalid")

    def test_error_shape_and_exact_one_failure_per_client_are_enforced(self):
        changes = [lambda raw: raw["clients"][0].update(errors=[]),
                   lambda raw: raw["clients"][0].update(errors=[ERROR, ERROR]),
                   lambda raw: raw["clients"][0].update(failures=2, errors=[ERROR, ERROR]),
                   lambda raw: raw["clients"][0].update(errors=["AssertionError: bytes mismatch"]),
                   lambda raw: raw["clients"][0].update(errors=["RuntimeError: unknown python failure"]),
                   lambda raw: raw["clients"][0].update(errors=["RuntimeError: code: ["])]
        for change in changes:
            with self.subTest(change=change):
                raw = failed_result();change(raw);sync_failure(raw)
                self.assertEqual(measured(raw)["status"], "invalid")

    def test_nonfailed_clients_must_finish_full_protocol_window(self):
        raw = failed_result()
        raw["clients"][0].update(failures=0, errors=[])
        sync_failure(raw)
        self.assertEqual(measured(raw)["status"], "invalid")
        raw["clients"][0]["last_ns"] = raw["measure_start_mono_ns"] + 20000000000
        raw.update(measure_end_mono_ns=raw["clients"][0]["last_ns"], duration_s=20)
        sync_failure(raw)
        self.assertEqual(measured(raw)["status"], "failed")

    def test_histogram_count_rate_and_provenance_checks_remain_strict(self):
        changes = [lambda raw: raw.update(success_keys=1),
                   lambda raw: raw.update(keys_s=1),
                   lambda raw: raw["clients"][0]["histogram"].clear(),
                   lambda raw: raw["setup"][0]["metadata"].update(source_sha="0" * 40),
                   lambda raw: raw["parameters"].update(pool_keys=1)]
        for change in changes:
            with self.subTest(change=change):
                raw = failed_result();change(raw)
                self.assertEqual(measured(raw)["status"], "invalid")

    def test_write_failures_never_enter_readonly_continuation(self):
        for operation in ["put", "upsert"]:
            with self.subTest(operation=operation):
                self.assertEqual(measured(failed_result(operation))["status"], "invalid")

    def test_failed_repetition_keeps_all_aggregate_metrics_unknown(self):
        rows = []
        for repeat in range(3):
            raw = failed_result() if repeat == 1 else raw_result("exist_hit", 16)
            row = measured(raw, {"continue_readonly_errors": True})
            row["repeat"] = repeat
            rows.append(row)
        result = subject.aggregate(rows, 3)[0]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["valid_repeats"], 2)
        self.assertTrue(all(metric["median"] is None and metric["min"] is None and metric["max"] is None
                            for metric in result["metrics"].values()))


class CompleteFailureEvidenceTests(unittest.TestCase):
    def fixture(self, parent):
        suite = parent / "formal-batch-001";suite.mkdir()
        plan = batch_runner.plan(3)
        root, remote_out = "/synthetic/not-a-target", "/synthetic/not-a-target/formal-batch-001"
        parameters = {"root": root, "out": remote_out, "action": "run", "repeats": 3, "duration": 20,
                      "warmup": 3, "put_keys": 262144, "continue_readonly_errors": True,
                      "workload_protocol": batch_runner.PROTOCOL}
        expected_failure = "r0-datasystem/b16-exist_hit"
        products = [{"name": name, "adapter": root + "/batch_" + name + ".py", "config": root + "/configs/" + name + ".json"}
                    for name in ["datasystem", "mooncake", "lmcache"]]
        summary = {"valid": False, "execution_complete": True, "profile": "native-batch-tcp", "action": "run",
                   "completed": [], "failed": [], "ended": "synthetic-only", "monitor_exit": 0, "interference_exit": 0,
                   "measurement_failures": [expected_failure], "workload_protocol": batch_runner.PROTOCOL,
                   "epochs_expected": [], "epochs_completed": [],
                   "drained": {"checked_utc": "synthetic-only", "populated": [], "members": {}}}
        events, epoch_files = [], []
        cursor = 1000000000
        for group in plan:
            cell = f"r{group['repeat']}-{group['product']}"
            summary["completed"].append(cell)
            product = next(item for item in products if item["name"] == group["product"])
            for spec in group["operations"]:
                if not spec["supported"]:
                    path = suite / cell / f"b{spec['batch']}-{spec['operation']}.json";path.parent.mkdir(exist_ok=True)
                    path.write_text(json.dumps({"product": group["product"], "repeat": group["repeat"],
                        "batch": spec["batch"], "operation": spec["operation"], "status": "not_supported"}))
            for epoch in group["epochs"]:
                epoch_id = cell + "/" + epoch["id"]
                start, end = cursor, cursor + 500000000000
                run_dir = root + "/run/run-" + cell + "-" + epoch["id"]
                failures = [expected_failure] if cell == "r0-datasystem" and epoch["id"] == "read" else []
                ledger = {"identity": epoch_id, "kind": epoch["kind"], "operations": epoch["operations"],
                          "run_dir": run_dir, "workload_protocol": batch_runner.PROTOCOL,
                          "start_mono_ns": start, "end_mono_ns": end, "duration_s": 500,
                          "start_wall_ns": 1700000000000000000 + start, "end_wall_ns": 1700000000000000000 + end,
                          "valid": not failures, "execution_complete": True, "measurement_failures": failures,
                          "drained": {"checked_utc": "synthetic-only", "populated": [], "members": {
                              f"/sys/fs/cgroup/kvcfair20261020/{role}/cgroup.procs": [] for role in ["server", "client"]}}}
                directory = suite / cell / "epochs" / epoch["id"];directory.mkdir(parents=True, exist_ok=True)
                path = directory / "epoch-summary.json";path.write_text(json.dumps(ledger));epoch_files.append(path)
                summary["epochs_expected"].append(epoch_id);summary["epochs_completed"].append(epoch_id)
                events.append({"event": "epoch_start", "group": cell, "epoch": epoch["id"], "run_dir": run_dir, "epoch_kind": epoch["kind"]})
                for index, spec in enumerate(epoch["operations"]):
                    identity = cell + f"/b{spec['batch']}-{spec['operation']}"
                    raw = failed_result(spec["operation"], spec["batch"]) if identity == expected_failure else raw_result(spec["operation"], spec["batch"])
                    window_start = start + 10000000000 + index * 22000000000
                    duration = raw["duration_s"]
                    raw.update(measure_start_mono_ns=window_start, measure_end_mono_ns=window_start + round(duration * 1e9),
                               measure_start_wall_ns=1700000000000000000 + window_start)
                    raw["parameters"].update(adapter=product["adapter"], config=product["config"], put_warmup_keys=32768,
                        run_id=suite.name + "-r" + str(group["repeat"]), skip_preload=index != 0,
                        out=remote_out + "/" + identity + ".json")
                    for record in raw["setup"]:
                        record["preloaded"] = index == 0
                        record["metadata"]["source_sha"] = subject.SOURCE_SHAS[group["product"]]
                    for client in raw["clients"]:
                        client.update(first_ns=window_start + 1000, last_ns=raw["measure_end_mono_ns"])
                    for warm in raw["warmup"]:
                        warm.update(put_inserted_keys=4096 if spec["operation"] == "put" else 0,
                                    put_verified_keys=4096 if spec["operation"] == "put" else 0)
                    path = suite / (identity + ".json");path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(raw))
                    if identity == expected_failure:
                        native_errors = [{"client": client["client"], "errors": client["errors"]} for client in raw["clients"]]
                        outcome = {"identity": identity, "returncode": 1, "status": "failed", "read_only": True,
                                   "evidence_verified": True, "errors": native_errors, "result": raw["parameters"]["out"],
                                   "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        record = suite / cell / "case-outcomes" / (path.stem + ".json");record.parent.mkdir();record.write_text(json.dumps(outcome))
                        events += [{"event": "command", "argv": ["synthetic-only", "--out", raw["parameters"]["out"]], "returncode": 1},
                                   {"event": "measure_failed", "identity": identity, "returncode": 1, "failures": 8,
                                    "success": raw["success"], "duration_s": duration}]
                events.append({"event": "epoch_complete", "group": cell, "epoch": epoch["id"], "valid": not failures,
                               "execution_complete": True, "measurement_failures": failures})
                cursor = end + 1000000000
        for name, value in [("plan.json", plan), ("parameters.json", parameters), ("summary.json", summary), ("products.json", products)]:
            (suite / name).write_text(json.dumps(value))
        (suite / "events.jsonl").write_text("\n".join(json.dumps(row) for row in events) + "\n")
        return suite, epoch_files

    def test_complete_failure_evidence_is_honest_not_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, _ = self.fixture(Path(directory));result = subject.analyze(suite)
            self.assertEqual(result["issues"], [])
            self.assertEqual(result["counts"], {"valid": 149, "failed": 1, "not_supported": 30})
            self.assertFalse(result["passed"])
            self.assertTrue(result["evidence_verified"])

    def test_epoch_suite_failure_lists_and_result_hash_receipt_cannot_diverge(self):
        for mode in ["epoch", "summary", "receipt", "event"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                suite, epochs = self.fixture(Path(directory))
                if mode == "epoch":
                    path = epochs[0];raw = json.loads(path.read_text());raw["measurement_failures"] = [];path.write_text(json.dumps(raw))
                elif mode == "summary":
                    path = suite / "summary.json";raw = json.loads(path.read_text());raw["measurement_failures"] = [];path.write_text(json.dumps(raw))
                elif mode == "receipt":
                    path = suite / "r0-datasystem/case-outcomes/b16-exist_hit.json";raw = json.loads(path.read_text());raw["result_sha256"] = "0" * 64;path.write_text(json.dumps(raw))
                else:
                    path = suite / "events.jsonl";events = [json.loads(line) for line in path.read_text().splitlines()];events = [row for row in events if row["event"] != "measure_failed"];path.write_text("\n".join(map(json.dumps, events)) + "\n")
                result = subject.analyze(suite)
                self.assertTrue(result["issues"])
                self.assertFalse(result["evidence_verified"])


if __name__ == "__main__":
    unittest.main()
