"""Mutation tests for the offline auditor, using a generated local SDK fixture."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "validation/audit_study_004.py"
SPEC = importlib.util.spec_from_file_location("study004_auditor", AUDITOR)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
ORIGINAL = ARTIFACTS = EVIDENCE = None


def fixture(root):
    artifact_root = root / "job"
    artifact_root.mkdir()
    job_id = "a" * 32
    config = {"profile": "baseline", "nodes": 1, "workers_per_node": 2, "clients": 8,
              "server_cpus": 8, "client_cpus": 8, "master_cpus": 2, "memory_gib": 16,
              "products": ["mooncake"], "batches": [1], "value_sizes": [1024],
              "operations": ["get", "exist_hit", "exist_miss"], "duration_s": 20, "repeats": 1}
    plan = {"variants": [{"config": config}], "expected_cases": 3}
    directory = artifact_root / "v-v0/001-mooncake"
    directory.mkdir(parents=True)
    rows, samples = [], []
    for index, operation in enumerate(config["operations"]):
        start = 1_000_000_000 + index * 30_000_000_000
        end = start + 20_000_000_000
        relative = f"v-v0/001-mooncake/r0-s1024-b1-{operation}/raw.json"
        raw_path = artifact_root / relative
        raw_path.parent.mkdir()
        raw = {"valid": True, "failures": 0, "success": 100000,
               "clients": [{"success": 12500} for _ in range(8)], "process_exit_codes": [0] * 8,
               "parameters": {"op": operation, "batch": 1, "size": 1024, "concurrency": 8,
                              "run_id": job_id + "-v0-r0-s1024"},
               "duration_s": 20, "keys_s": 5000, "batch_qps": 5000,
               "histogram": {"1152": 100000}, "p99": {"lower_us": 262.144, "upper_us": 266.24},
               "measure_start_mono_ns": start, "measure_end_mono_ns": end,
               "short_window": False, "low_sample_p99": False}
        raw_path.write_text(json.dumps(raw))
        rows.append({"variant_id": "v0", "product": "mooncake", "repeat": 0,
            "profile": "baseline", "nodes": 1, "workers": 2, "workers_per_node": 2,
            "clients": 8, "server_cpus": 8, "client_cpus": 8, "master_cpus": 2, "memory_gib": 16,
            "size": 1024, "value_size": 1024, "batch": 1, "operation": operation, "status": "valid",
            "build_sha": AUDIT.BUILDS["mooncake"], "keys_s": 5000, "batch_qps": 5000,
            "p50_us": 266.24, "p95_us": 266.24, "p99_us": 266.24, "duration_s": 20,
            "server_cpu_cores": 2, "client_cpu_cores": 6, "master_cpu_cores": 1,
            "master_throttled_usec": 0, "peak_charged_memory_gib": 2,
            "sample_count": 401, "interval_s": .05, "window_start_mono_ns": start,
            "window_end_mono_ns": end, "raw_artifact": relative,
            "short_window": False, "low_sample_p99": False})
        for sample in range(401):
            roles = {role: {"cpu.stat": {"usage_usec": sample * usage, "throttled_usec": 0},
                            "memory.current": 2 << 30}
                     for role, usage in [("server", 100000), ("client", 300000), ("server/master", 50000)]}
            samples.append({"mono_ns": start + sample * 50_000_000, "roles": roles})
    with (directory / "resources.jsonl").open("w") as stream:
        for sample in samples:
            stream.write(json.dumps(sample) + "\n")
    build = {"mooncake": {"sha": AUDIT.BUILDS["mooncake"], "artifacts": {
        "mooncake_master": {"valid": True, "sha256": "b" * 64, "expected_sha256": "b" * 64}}}}
    (artifact_root / "build-verification.json").write_text(json.dumps(build))
    (artifact_root / "request.json").write_text(json.dumps({"job_id": job_id, "action": "run", "config": config, "plan": plan}))
    artifacts = [{"name": str(path.relative_to(artifact_root)), "size": path.stat().st_size,
                  "sha256": AUDIT.sha(path), "source": str(path.relative_to(artifact_root))}
                 for path in sorted(artifact_root.rglob("*")) if path.is_file()]
    complete = {"job_id": job_id, "kind": "complete", "status": "succeeded", "results": rows, "artifacts": artifacts}
    (artifact_root / "complete.json").write_text(json.dumps(complete))
    evidence = {"job": {"id": job_id, "status": "succeeded", "config": config, "plan": plan,
                        "completion": complete, "results": copy.deepcopy(rows),
                        "provenance": {"kind": "provenance", "source_sha256": AUDIT.SOURCE}}}
    evidence_path = root / "evidence.json"
    evidence_path.write_text(json.dumps(evidence))
    return evidence, artifact_root, evidence_path


class IndependentAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global ORIGINAL, ARTIFACTS, EVIDENCE
        cls.temporary = tempfile.TemporaryDirectory()
        ORIGINAL, ARTIFACTS, EVIDENCE = fixture(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def audit(self, evidence):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(evidence))
            return AUDIT.Auditor("probe", path, ARTIFACTS).run()

    def issue_codes(self, result):
        return {issue["code"] for issue in result.get("issues", [])}

    def test_closed_native_fixture_passes_histogram_and_resource_recomputations(self):
        result = self.audit(ORIGINAL)
        self.assertTrue(result["passed"], result.get("issues"))
        self.assertEqual(result["coverage"]["actual_remote_completion_rows"], 3)
        self.assertTrue(all(row["audit_passed"] for row in result["rows"]))

    def test_service_rows_are_not_used_as_authoritative_completion(self):
        value = copy.deepcopy(ORIGINAL)
        value["job"]["results"] = [{"status": "missing"}]
        self.assertTrue(self.audit(value)["passed"])

    def test_running_job_is_waiting_without_final_missing_verdict(self):
        value = copy.deepcopy(ORIGINAL)
        value["job"].update(status="running", completion=None, results=[])
        result = self.audit(value)
        self.assertEqual(result["status"], "waiting")
        self.assertIsNone(result["passed"])
        self.assertNotIn("coverage", result)

    def test_missing_actual_completion_row_cannot_be_repaired_by_service_placeholder(self):
        value = copy.deepcopy(ORIGINAL)
        value["job"]["completion"]["results"].pop()
        result = self.audit(value)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["coverage"]["missing"]), 1)

    def test_duplicate_repeat_is_rejected(self):
        value = copy.deepcopy(ORIGINAL)
        value["job"]["completion"]["results"].append(copy.deepcopy(value["job"]["completion"]["results"][0]))
        result = self.audit(value)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["coverage"]["duplicates"]), 1)

    def test_nonfinite_throughput_is_not_a_valid_metric(self):
        value = copy.deepcopy(ORIGINAL)
        value["job"]["completion"]["results"][0]["keys_s"] = float("nan")
        self.assertIn("nonfinite_or_missing_metric", self.issue_codes(self.audit(value)))

    def test_nonfinite_input_still_produces_valid_failed_json_from_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            value = copy.deepcopy(ORIGINAL)
            value["job"]["completion"]["results"][0]["keys_s"] = float("nan")
            path, out = Path(directory) / "nonfinite.json", Path(directory) / "audit.json"
            path.write_text(json.dumps(value))
            process = subprocess.run([sys.executable, str(AUDITOR),
                "--run", "broken=" + str(path), "--artifact-root", "broken=" + str(ARTIFACTS), "--out", str(out)], capture_output=True)
            self.assertEqual(process.returncode, 1)
            result = json.loads(out.read_text(), parse_constant=lambda value: self.fail("Invalid JSON number: " + value))
            self.assertEqual(result["status"], "failed")
            self.assertIn("nonfinite_or_missing_metric", self.issue_codes(result["runs"][0]))

    def test_key_rate_must_equal_native_calls_times_batch(self):
        value = copy.deepcopy(ORIGINAL)
        value["job"]["completion"]["results"][0]["keys_s"] += 1
        self.assertIn("keys_s_batch_qps_inconsistent", self.issue_codes(self.audit(value)))

    def test_p99_must_equal_raw_histogram_upper_bound(self):
        value = copy.deepcopy(ORIGINAL)
        value["job"]["completion"]["results"][0]["p99_us"] -= 1
        self.assertIn("p99_not_numeric_native_bucket_upper_bound", self.issue_codes(self.audit(value)))

    def test_sampling_count_and_window_are_bound_to_sdk_measurement(self):
        value = copy.deepcopy(ORIGINAL)
        row = value["job"]["completion"]["results"][0]
        row["sample_count"] = 1
        row["window_start_mono_ns"] += 1
        codes = self.issue_codes(self.audit(value))
        self.assertTrue({"insufficient_resource_samples", "resource_window_sdk_mismatch", "resource_count_recompute_failed"} <= codes)

    def test_build_and_installed_source_pins_are_fixed(self):
        value = copy.deepcopy(ORIGINAL)
        value["job"]["completion"]["results"][0]["build_sha"] = "0" * 40
        value["job"]["provenance"]["source_sha256"] = "0" * 64
        codes = self.issue_codes(self.audit(value))
        self.assertTrue({"row_build_sha_mismatch", "frozen_source_provenance_mismatch"} <= codes)

    def test_sample_quality_flags_are_recomputed(self):
        value = copy.deepcopy(ORIGINAL)
        row = value["job"]["completion"]["results"][0]
        row["short_window"] = True
        row["low_sample_p99"] = True
        codes = self.issue_codes(self.audit(value))
        self.assertTrue({"short_window_flag_mismatch", "p99_sample_flag_mismatch"} <= codes)

    def test_summaries_do_not_pool_different_role_budgets(self):
        first = self.audit(ORIGINAL)
        second = copy.deepcopy(first)
        second["name"] = "different-budget"
        second["job_id"] = "other-job"
        for row in second["rows"]:
            row["server_cpus"] = 16
        groups = AUDIT.summarize([first, second])
        self.assertEqual(len(groups), 6)
        self.assertEqual({group["server_cpus"] for group in groups}, {8, 16})
        self.assertTrue(all(group["observations"] == 1 for group in groups))

    def test_the_same_physical_run_cannot_be_pooled_twice_under_different_names(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "duplicate-audit.json"
            process = subprocess.run([sys.executable, str(AUDITOR),
                "--run", "first=" + str(EVIDENCE),
                "--run", "second=" + str(EVIDENCE),
                "--artifact-root", "first=" + str(ARTIFACTS), "--artifact-root", "second=" + str(ARTIFACTS), "--out", str(out)],
                capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            result = json.loads(out.read_text())
            self.assertEqual(result["summaries"], [])
            self.assertTrue(all("same_physical_run_supplied_more_than_once" in self.issue_codes(run) for run in result["runs"]))


if __name__ == "__main__":
    unittest.main()
