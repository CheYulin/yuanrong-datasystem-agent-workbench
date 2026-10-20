"""Offline stateful diagnostics fixtures, no DataSystem process or target access."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("deadline_diag", Path(__file__).with_name("batch_ds_deadline_diagnostic.py"))
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


class Clock:
    def __init__(self):
        self.ns = 0

    def monotonic_ns(self):
        self.ns += 1000
        return self.ns

    def sleep(self, duration):
        self.ns += int(duration * 1e9)


class FakeNative:
    metadata = {"product": "fixture-only", "source_sha": "not-runtime-evidence"}

    def __init__(self, clock, stale=True, malformed=False, close_fails=False):
        self.clock, self.stale = clock, stale
        self.values, self.gets, self.deadline = {}, [], None
        self.closed, self.close_fails, self.malformed = False, close_fails, malformed

    def put_batch(self, keys, values):
        self.values.update(zip(keys, values))

    def get_batch(self, keys):
        self.gets.append(list(keys))
        self.deadline = self.clock.ns + 10 * 10**9
        values = [self.values[key] for key in keys]
        return list(reversed(values)) if self.malformed else values

    def exists_batch(self, keys):
        if self.stale and len(keys) > 1 and self.deadline is not None and self.clock.ns >= self.deadline:
            raise RuntimeError("fixture: API deadline exceeded, remaining -200 us")
        return [key in self.values for key in keys]

    def close(self):
        self.closed = True
        if self.close_fails:
            raise RuntimeError("fixture close failure")


class DiagnosticTests(unittest.TestCase):
    def case(self, name, **native_kwargs):
        clock = Clock()
        adapter = FakeNative(clock, **native_kwargs)
        with mock.patch.object(diagnostic.time, "monotonic_ns", clock.monotonic_ns), \
                mock.patch.object(diagnostic.time, "sleep", clock.sleep), \
                mock.patch.object(diagnostic, "process_snapshot", return_value={"fixture": True}):
            result = diagnostic.run_case("fixture.py", {"timeout_ms": 10000}, name,
                                         [f"fixture-key-{i}" for i in range(16)], 13, 10.1, .05,
                                         factory=lambda path, config: adapter)
        json.dumps(result, allow_nan=False)
        self.assertTrue(adapter.closed)
        self.assertTrue(all(row["duration_s"] >= 0 for row in result["steps"]))
        return result, adapter

    def test_exists_only_never_calls_get_and_runs_beyond_configured_timeout(self):
        result, adapter = self.case("A")
        self.assertEqual(adapter.gets, [])
        self.assertTrue(result["all_sdk_steps_passed"])
        self.assertGreaterEqual(result["exist_elapsed_s"], 13)

    def test_get_then_exists_retains_observed_failure_and_stops_without_retry(self):
        result, adapter = self.case("B")
        failures = [row for row in result["steps"] if row["status"] == "failed"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["name"], "exists")
        self.assertIn("remaining -200", failures[0]["error"]["message"])
        self.assertTrue(result["complete"])
        self.assertFalse(result["all_sdk_steps_passed"])
        self.assertEqual(len(adapter.gets), 1)
        self.assertGreaterEqual(result["exist_elapsed_s"], 10)
        self.assertLess(result["exist_elapsed_s"], 10.1)

    def test_sleep_batch16_failure_and_batch1_control_use_same_initial_get(self):
        c, _ = self.case("C")
        d, adapter = self.case("D")
        self.assertFalse(c["all_sdk_steps_passed"])
        self.assertTrue(d["all_sdk_steps_passed"])
        self.assertEqual(len(adapter.gets[0]), 16)
        self.assertEqual(c["keys"], d["keys"])
        self.assertEqual(c["key_payload_sha256"], d["key_payload_sha256"])
        self.assertEqual(len([row for row in c["steps"] if row["name"] == "exists"]), 1)
        self.assertEqual(len([row for row in d["steps"] if row["name"] == "exists"]), 1)

    def test_healthy_sdk_is_not_forced_to_match_failure_hypothesis(self):
        result, _ = self.case("B", stale=False)
        self.assertTrue(result["all_sdk_steps_passed"])
        self.assertGreaterEqual(result["exist_elapsed_s"], 13)

    def test_get_reordered_bytes_fail_setup_but_still_close(self):
        result, _ = self.case("B", malformed=True)
        self.assertFalse(result["complete"])
        self.assertEqual([row["name"] for row in result["steps"]], ["put-initial", "get-full", "close"])

    def test_close_error_is_recorded_not_erased(self):
        result, _ = self.case("A", close_fails=True)
        self.assertFalse(result["all_sdk_steps_passed"])
        self.assertEqual(result["steps"][-1]["status"], "failed")
        self.assertIn("after_close", result)

    def test_wire_proof_excludes_wrong_window_unix_failed_and_incoming_syscalls(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "strace.123").write_text(
                '1.100000 writev(8<TCP:[127.0.0.1:9999->127.0.0.1:31531]>, [], 1) = 100 <0.001>\n'
                '1.200000 writev(9<TCP:[127.0.0.1:9998->127.0.0.1:31532]>, [], 1) = 90 <0.001>\n'
                '1.250000 write(10<UNIX:[11->12]>, "a", 1) = 1\n'
                '1.260000 readv(8<TCP:[127.0.0.1:9999->127.0.0.1:31533]>, [], 1) = 100\n'
                '1.270000 writev(8<TCP:[127.0.0.1:9999->127.0.0.1:31534]>, [], 1) = -1 EAGAIN\n'
                '2.100000 writev(8<TCP:[127.0.0.1:9999->127.0.0.1:31535]>, [], 1) = 100\n')
            evidence = diagnostic.wire_endpoints(directory, [{"name": "exists", "start_wall_ns": 10**9,
                                                            "end_wall_ns": 1500000000}])
            self.assertEqual(evidence["outgoing_tcp_endpoints"], ["127.0.0.1:31531", "127.0.0.1:31532"])
            self.assertEqual(evidence["successful_send_lines"], 2)

    def test_immutable_json_write_refuses_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            diagnostic.save(path, {"original": True})
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                diagnostic.save(path, {"original": False})
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
