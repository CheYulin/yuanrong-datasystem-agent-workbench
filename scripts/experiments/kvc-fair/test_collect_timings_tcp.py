#!/usr/bin/env python3
"""Offline timing fixtures; no SSH, product startup, or measured workload."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from collect_timings import Collector


BASE = 1792400000000000000


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


class TcpTimingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.suite = Path(self.temporary.name) / "formal-tcp-001"
        self.suite.mkdir()
        write_json(self.suite / "tcp-stage.json", {"source_builds_reused": True})
        self.archive = self.suite / "protocol-history/attempt-0001"
        write_json(self.archive / "summary.json", {
            "start_wall_ns": BASE, "end_wall_ns": BASE + 12000000000,
            "start_mono_ns": 2000000000, "end_mono_ns": 11000000000,
            "duration_s": 9, "valid": False, "fatal": "Recorded fixture failure"})
        operations = [
            {"argv": ["fixture", "failed"], "start_wall_ns": BASE + 1,
             "end_wall_ns": BASE + 1000000000, "duration_s": 0.125, "returncode": 1},
            {"argv": ["fixture", "incomplete"], "start_wall_ns": BASE + 2,
             "duration_s": 999, "returncode": None},
            {"argv": ["fixture", "unknown-exit"], "start_wall_ns": BASE + 3,
             "end_wall_ns": BASE + 1000000001, "duration_s": 0.25},
        ]
        self.operations = self.archive / "operations.jsonl"
        self.operations.write_text("".join(json.dumps(row) + "\n" for row in operations) + '{"partial":')
        write_json(self.suite / "protocol-history/attempt-0002/summary.json", {
            "start_wall_ns": BASE + 20000000000, "duration_s": 999, "valid": False})
        self.current = self.suite / "protocol-evidence"
        write_json(self.current / "summary.json", {
            "start_wall_ns": BASE + 30000000000, "end_wall_ns": BASE + 40000000000,
            "duration_s": 8.5, "valid": True})
        write_json(self.current / "operations.jsonl", {
            "argv": ["fixture", "passed"], "start_wall_ns": BASE + 31000000000,
            "end_wall_ns": BASE + 32000000000, "duration_s": 0.5, "returncode": 0})
        write_json(self.suite / "preflight-changes.json", {"revisions": [
            {"number": 1, "start_wall_ns": BASE + 13000000000,
             "end_wall_ns": BASE + 14000000000, "duration_s": 0.2,
             "changes": [], "deployment_permissions": [{"before_mode": 420, "after_mode": 493}],
             "protocol_archive": "protocol-history/attempt-0001"},
            {"number": 2, "start_wall_ns": BASE + 21000000000, "duration_s": 999},
        ]})
        write_json(self.suite / "linked-master-recovery.json", {
            "start_wall_ns": BASE + 25000000000, "end_wall_ns": BASE + 25025000000,
            "operations": [{"pid": 123, "signal": "SIGTERM", "start_ticks": 456}]})
        write_json(self.suite / "failed-preflight-history/attempt-0002-fixture/failure.json", {
            "end_wall_ns": BASE + 22000000000, "error": "fixture", "rolled_back": True})
        write_json(self.suite / "builds/datasystem.json", {
            "product": "datasystem", "build_exit": 0, "build_wall_seconds": 10,
            "build_started_utc": "2026-10-19T00:00:00Z", "build_finished_utc": "2026-10-19T00:00:10Z"})

    def collect(self):
        collector = Collector()
        collector.tcp_preflight(self.suite)
        return collector

    def test_attempt_status_and_clock_domains(self):
        rows = [row for row in self.collect().steps if row.get("process_stage") == "protocol_gate"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["duration_s"], 9)
        self.assertEqual(rows[0]["end_utc"], "2026-10-19T08:53:32.000000000Z")
        self.assertEqual(rows[1]["status"], "interrupted_unknown_end")
        self.assertIsNone(rows[1]["end_utc"])
        self.assertIsNone(rows[1]["duration_s"])
        self.assertEqual(rows[2]["status"], "completed")
        self.assertEqual(rows[2]["duration_s"], 8.5)
        self.assertTrue(all(row["category"] == "local_operation" and not row["additive"] for row in rows))

    def test_operations_partial_lines_unknown_exit_and_deduplication(self):
        collector = self.collect()
        collector.operations([self.operations, self.operations.resolve()])
        collector.tcp_preflight(self.suite / "summary.json")
        rows = [row for row in collector.steps if row.get("process_stage") == "protocol_command"]
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["status"] for row in rows], ["failed", "interrupted_unknown_end", "unknown", "completed"])
        self.assertIsNone(rows[1]["end_utc"])
        self.assertIsNone(rows[1]["duration_s"])
        self.assertIsNone(rows[2]["reported_exit"])
        self.assertEqual(len(collector.issues), 1)
        self.assertIn("#L4", collector.issues[0]["evidence"])
        self.assertEqual(len({row["id"] for row in collector.steps}), len(collector.steps))

    def test_permission_only_revision_and_unknown_elapsed(self):
        rows = [row for row in self.collect().steps if row.get("process_stage") == "preflight_correction"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["duration_s"], 0.2)
        self.assertEqual(rows[0]["helper_changes"], [])
        self.assertEqual(rows[0]["deployment_permissions"][0]["after_mode"], 493)
        self.assertEqual(rows[1]["status"], "unknown")
        self.assertIsNone(rows[1]["duration_s"])

    def test_recovery_wall_only_and_failed_correction_nulls(self):
        rows = self.collect().steps
        recovery = next(row for row in rows if row.get("process_stage") == "guarded_recovery")
        self.assertEqual(recovery["duration_s"], 0.025)
        self.assertEqual(recovery["clock_domain"], "target_wall_clock")
        self.assertIsNone(recovery["reported_exit"])
        self.assertEqual(len(recovery["recovery_observations"]), 1)
        failed = next(row for row in rows if row.get("process_stage") == "preflight_correction_failure")
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["start_utc"])
        self.assertIsNone(failed["duration_s"])

    def test_builds_are_original_provenance_not_tcp_compilation(self):
        collector = self.collect()
        collector.builds(self.suite / "builds")
        build = next(row for row in collector.steps if row["category"] == "reused_build")
        self.assertEqual(build["duration_s"], 10)
        self.assertEqual(build["start_utc"], "2026-10-19T00:00:00.000000000Z")
        self.assertTrue(build["reused_source_build"])
        self.assertIn("no TCP rebuild occurred", build["scope"])
        native = Collector()
        native.builds(self.suite / "builds")
        self.assertEqual(native.steps[0]["category"], "build")
        self.assertNotIn("reused_source_build", native.steps[0])
        self.assertEqual(native.steps[0]["start_utc"], build["start_utc"])

    def test_cli_discovers_evidence_preserves_inputs_and_csv_parity(self):
        write_json(self.suite / "summary.json", {"started": "2026-10-19T07:00:00Z", "ended": "2026-10-19T07:00:01Z", "failed": 0})
        (self.suite / "events.jsonl").write_text("")
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.suite.rglob("*") if path.is_file()}
        out = Path(self.temporary.name) / "output"
        subprocess.run([sys.executable, str(Path(__file__).with_name("collect_timings.py")),
                        "--suite", str(self.suite), "--out", str(out)], check=True, capture_output=True, text=True)
        raw = json.loads((out / "timings.json").read_text())
        with (out / "timings.csv").open() as stream:
            csv_rows = list(csv.DictReader(stream))
        self.assertEqual(len(csv_rows), len(raw["steps"]))
        self.assertTrue(any(row.get("process_stage") == "protocol_gate" for row in raw["steps"]))
        self.assertTrue(any(row["category"] == "reused_build" for row in raw["steps"]))
        self.assertFalse(any(row["category"] == "sdk_window" for row in raw["steps"]))
        self.assertEqual(len({row["id"] for row in raw["steps"]}), len(raw["steps"]))
        self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in self.suite.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
