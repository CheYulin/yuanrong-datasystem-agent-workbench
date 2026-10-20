"""Exercise actual Runner failure branches with synthetic native-command output."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import batch_runner as subject
from tests.unit.collect.test_batch_analysis import raw_result
from tests.unit.collect.test_batch_failures import failed_result


class Clock:
    def __init__(self):
        self.ns = 1000000000

    def monotonic_ns(self):
        self.ns += 1000
        return self.ns

    def time_ns(self):
        return 1700000000000000000 + self.monotonic_ns()

    def sleep(self, seconds):
        self.ns += int(seconds * 1000000000)


class RunnerNativeFailureTests(unittest.TestCase):
    def exercise(self, parent, *, allow=True, failure=(16, "exist_hit"), mutation=None, native_rc=1):
        runner = object.__new__(subject.Runner)
        runner.root, runner.out = parent / "runtime", parent / "formal-batch-001"
        runner.root.mkdir();runner.out.mkdir()
        runner.args = SimpleNamespace(action="run", duration=20, warmup=3, put_keys=262144,
                                      continue_readonly_errors=allow)
        runner.products = {"datasystem": {"name": "datasystem", "python": "synthetic-python",
            "adapter": "/fixture/batch_datasystem.py", "config": "/fixture/configs/datasystem.json",
            "start": ["fake-start", "{run_dir}"], "stop": ["fake-stop", "{run_dir}"], "env": {}}}
        runner.epochs_completed, runner.measurement_failures = [], []
        events, commands, active = [], [], set()
        runner.event = lambda kind, **data: events.append({"event": kind, **data})
        clock = Clock()

        class Process:
            def __init__(self, argv, **kwargs):
                self.argv, self.returncode = list(argv), 0
                commands.append(self.argv)
                if argv[0] == "fake-start":
                    if active:
                        raise AssertionError("overlapping service epochs")
                    active.add(argv[1])
                elif argv[0] == "fake-stop":
                    active.discard(argv[1])
                else:
                    if len(active) != 1:
                        raise AssertionError("SDK command has no owned service")
                    op, batch = argv[argv.index("--op") + 1], int(argv[argv.index("--batch") + 1])
                    failing = (batch, op) == failure
                    raw = failed_result(op, batch) if failing else raw_result(op, batch)
                    output = Path(argv[argv.index("--out") + 1])
                    raw["parameters"].update(adapter=runner.products["datasystem"]["adapter"],
                        config=runner.products["datasystem"]["config"], run_id=argv[argv.index("--run-id") + 1],
                        out=str(output), skip_preload="--skip-preload" in argv, put_warmup_keys=32768)
                    for record in raw["setup"]:
                        record["preloaded"] = "--skip-preload" not in argv
                    for record in raw["warmup"]:
                        record.update(put_inserted_keys=4096 if op == "put" else 0,
                                      put_verified_keys=4096 if op == "put" else 0)
                    start = clock.monotonic_ns();end = start + round(raw["duration_s"] * 1000000000)
                    raw.update(measure_start_mono_ns=start, measure_end_mono_ns=end,
                               measure_start_wall_ns=1700000000000000000 + start)
                    for client in raw["clients"]:
                        client.update(first_ns=start + 1000, last_ns=end)
                    clock.ns = end + 1000
                    if failing and mutation:
                        mutation(raw)
                    output.write_text(json.dumps(raw))
                    self.returncode = native_rc if failing else 0

            def wait(self, timeout=None):
                return self.returncode

        drained = {"checked_utc": "synthetic-only", "populated": [], "members": {}}
        error, returned = None, None
        row = next(row for row in subject.plan(1) if row["product"] == "datasystem")
        with patch.object(subject.subprocess, "Popen", Process), patch.object(subject, "time", clock), \
                patch.object(subject, "drain", return_value=drained) as drain:
            try:
                returned = runner.lifecycle(row, 1)
            except Exception as exc:
                error = exc
        return runner, commands, events, active, drain.call_count, returned, error

    def test_native_read_failure_is_receipted_and_all_later_read_put_epochs_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, commands, events, active, drains, returned, error = self.exercise(Path(directory))
            self.assertIsNone(error)
            self.assertEqual(returned, "r0-datasystem")
            self.assertFalse(active)
            self.assertEqual(len([command for command in commands if "--op" in command]), 20)
            self.assertEqual(len([command for command in commands if command[0] == "fake-start"]), 5)
            self.assertGreaterEqual(drains, 10)
            identity = "r0-datasystem/b16-exist_hit"
            self.assertEqual(runner.measurement_failures, [identity])
            cell = runner.out / "r0-datasystem"
            outcome = json.loads((cell / "case-outcomes/b16-exist_hit.json").read_text())
            result = cell / "b16-exist_hit.json"
            self.assertEqual(outcome["returncode"], 1)
            self.assertEqual(outcome["result_sha256"], hashlib.sha256(result.read_bytes()).hexdigest())
            self.assertEqual(outcome["result"], str(result))
            self.assertEqual(len(outcome["errors"]), 8)
            failed = [event for event in events if event["event"] == "measure_failed"]
            self.assertEqual(len(failed), 1)
            self.assertEqual((failed[0]["identity"], failed[0]["returncode"], failed[0]["failures"]), (identity, 1, 8))
            read = json.loads((cell / "epochs/read/epoch-summary.json").read_text())
            self.assertFalse(read["valid"])
            self.assertTrue(read["execution_complete"])
            self.assertEqual(read["measurement_failures"], [identity])
            for batch in [1, 16, 256, 4096]:
                put = json.loads((cell / f"epochs/put-b{batch}/epoch-summary.json").read_text())
                self.assertTrue(put["valid"])
                self.assertTrue(put["execution_complete"])

    def test_default_flag_remains_failfast_and_stops_own_service(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, commands, _, active, drains, returned, error = self.exercise(Path(directory), allow=False)
            self.assertIsNotNone(error)
            self.assertIsNone(returned)
            self.assertFalse(active)
            self.assertGreaterEqual(drains, 2)
            self.assertEqual(len([command for command in commands if command[0] == "fake-start"]), 1)
            self.assertFalse((runner.out / "r0-datasystem/case-outcomes").exists())

    def test_write_failures_cannot_continue_even_when_enabled(self):
        for operation in ["upsert", "put"]:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                runner, _, _, active, drains, returned, error = self.exercise(Path(directory), failure=(1, operation))
                self.assertIsNotNone(error)
                self.assertIsNone(returned)
                self.assertFalse(active)
                self.assertGreaterEqual(drains, 2)
                self.assertEqual(runner.measurement_failures, [])

    def test_validation_fatal_bad_client_exit_and_non_native_errors_are_failfast(self):
        changes = [lambda raw: raw.update(fatal="synthetic child fatal"),
                   lambda raw: raw["process_exit_codes"].__setitem__(0, 2),
                   lambda raw: raw["clients"][0].update(errors=["AssertionError: corrupt bytes"]),
                   lambda raw: raw.update(success_keys=0),
                   lambda raw: raw["warmup"][0].update(warm=False)]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                runner, _, events, active, drains, returned, error = self.exercise(Path(directory), mutation=change)
                self.assertIsNotNone(error)
                self.assertIsNone(returned)
                self.assertFalse(active)
                self.assertGreaterEqual(drains, 2)
                self.assertEqual(runner.measurement_failures, [])
                self.assertFalse(any(event["event"] == "measure_failed" for event in events))

    def test_unexpected_native_process_exit_or_contradictory_success_is_failfast(self):
        for exit_code, mutation in [(2, None), (1, lambda raw: raw.update(valid=True)), (0, None)]:
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                _, _, _, active, drains, returned, error = self.exercise(Path(directory), native_rc=exit_code, mutation=mutation)
                self.assertIsNotNone(error)
                self.assertIsNone(returned)
                self.assertFalse(active)
                self.assertGreaterEqual(drains, 2)


if __name__ == "__main__":
    unittest.main()
