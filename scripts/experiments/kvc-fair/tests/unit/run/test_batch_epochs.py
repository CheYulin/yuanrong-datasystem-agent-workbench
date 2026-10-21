"""Offline lifecycle/namespace tests for separate read and fresh-PUT epochs."""
from collections import Counter
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kvcbench.run import runner as subject
class EpochPlanTests(unittest.TestCase):
    def test_formal_grid_has_180_unique_slots_150_native_and_36_epochs(self):
        rows = subject.plan(3)
        identities = [(row["repeat"], row["product"], spec["batch"], spec["operation"])
                      for row in rows for spec in row["operations"]]
        self.assertEqual(subject.PROTOCOL, "separate-read-and-fresh-put-v1")
        self.assertEqual(len(rows), 9)
        self.assertEqual(len(identities), 180)
        self.assertEqual(len(set(identities)), 180)
        self.assertEqual(sum(spec["supported"] for row in rows for spec in row["operations"]), 150)
        self.assertEqual(sum(len(row["epochs"]) for row in rows), 36)
        self.assertEqual(sum(len(row["epochs"]) for row in subject.plan(1)), 12)

    def test_read_and_put_are_separate_epochs_and_nas_have_no_execution(self):
        for row in subject.plan(3):
            with self.subTest(product=row["product"], repeat=row["repeat"]):
                epochs = row["epochs"]
                self.assertEqual(epochs[0]["id"], "read")
                self.assertEqual(epochs[0]["kind"], "read")
                self.assertTrue(all(spec["operation"] != "put" for spec in epochs[0]["operations"]))
                expected = [spec for spec in row["operations"] if spec["supported"]]
                actual = [spec for epoch in epochs for spec in epoch["operations"]]
                self.assertEqual(actual, expected)
                for epoch in epochs:
                    self.assertTrue(all(spec["supported"] and spec["epoch"] == epoch["id"] for spec in epoch["operations"]))
                for epoch in epochs[1:]:
                    self.assertEqual(epoch["kind"], "fresh_put")
                    self.assertEqual(len(epoch["operations"]), 1)
                    spec = epoch["operations"][0]
                    self.assertEqual(spec["operation"], "put")
                    self.assertEqual(epoch["id"], "put-b" + str(spec["batch"]))
                if row["product"] == "lmcache":
                    self.assertEqual({epoch["id"] for epoch in epochs}, {"read", "put-b1"})


class EpochLifecycleTests(unittest.TestCase):
    def run_case(self, parent, product, *, fail_measure=False, fail_cleanup=False):
        runner = object.__new__(subject.Runner)
        runner.root, runner.out = parent / "runtime", parent / "formal-batch-001"
        runner.root.mkdir(); runner.out.mkdir()
        runner.args = SimpleNamespace(action="run", duration=20, warmup=3, put_keys=262144)
        runner.epochs_completed = []
        runner.products = {name: {"name": name, "python": "fake-python",
                                 "adapter": "/fixture/" + name + ".py", "config": "/fixture/" + name + ".json",
                                 "start": ["fake-start", "{run_dir}"],
                                 "stop": ["fake-stop", "{run_dir}"], "env": {}}
                           for name in ["datasystem", "mooncake", "lmcache"]}
        events, commands, active = [], [], set()
        runner.event = lambda kind, **data: events.append({"event": kind, **data})
        stop_counts = Counter()

        def execute(argv, log, env, timeout=1200):
            commands.append(list(argv))
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            Path(log).touch(exist_ok=True)
            if argv[0] == "fake-start":
                if active:
                    raise AssertionError("Fresh epoch started before the previous service stopped")
                active.add(argv[1])
            elif argv[0] == "fake-stop":
                stop_counts[argv[1]] += 1
                if fail_cleanup and argv[1] in active:
                    raise RuntimeError("synthetic cleanup failure")
                active.discard(argv[1])
            else:
                if len(active) != 1:
                    raise AssertionError("Measured API requires exactly its own active native service epoch")
                path = Path(argv[argv.index("--out") + 1])
                path.write_text(json.dumps({"valid": not fail_measure, "success": 1, "keys_s": 1, "duration_s": 20}))

        runner.execute = execute
        row = next(item for item in subject.plan(1) if item["product"] == product)
        drained = {"checked_utc": "fixture", "members": {}, "populated": []}
        error = None
        with patch.object(subject, "drain", return_value=drained) as drain, patch.object(subject.time, "sleep"):
            try:
                returned = runner.lifecycle(row, 1)
            except Exception as exc:
                error, returned = exc, None
        return runner, row, commands, events, active, error, returned, drain.call_count

    def test_every_supported_put_has_fresh_service_and_no_preload_reuse(self):
        for product in ["datasystem", "mooncake", "lmcache"]:
            with self.subTest(product=product), tempfile.TemporaryDirectory() as directory:
                runner, row, commands, _, active, error, returned, drains = self.run_case(Path(directory), product)
                self.assertIsNone(error)
                self.assertEqual(returned, "r0-" + product)
                self.assertFalse(active)
                starts = [command for command in commands if command[0] == "fake-start"]
                self.assertEqual(len(starts), len(row["epochs"]))
                self.assertEqual(len({command[1] for command in starts}), len(row["epochs"]))
                self.assertGreaterEqual(drains, len(row["epochs"]))
                for epoch, command in zip(row["epochs"], starts, strict=True):
                    self.assertEqual(command[1], str(runner.root / "run" / ("run-r0-" + product + "-" + epoch["id"])))
                    summary = json.loads((runner.out / ("r0-" + product) / "epochs" / epoch["id"] / "epoch-summary.json").read_text())
                    self.assertTrue(summary["valid"])
                    self.assertEqual(summary["identity"], "r0-" + product + "/" + epoch["id"])
                    self.assertEqual(summary["operations"], epoch["operations"])
                    self.assertEqual(summary["run_dir"], command[1])
                    self.assertEqual(summary["workload_protocol"], subject.PROTOCOL)
                    self.assertFalse(summary["drained"]["populated"])
                self.assertEqual(runner.epochs_completed, ["r0-" + product + "/" + epoch["id"] for epoch in row["epochs"]])
                measures = [command for command in commands if "--op" in command]
                put = [command for command in measures if command[command.index("--op") + 1] == "put"]
                self.assertTrue(all("--skip-preload" not in command for command in put))
                read = [command for command in measures if command not in put]
                self.assertNotIn("--skip-preload", read[0])
                self.assertTrue(all("--skip-preload" in command for command in read[1:]))
                self.assertTrue(all(command[command.index("--run-id") + 1] == "formal-batch-001-r0" for command in measures))

    def test_lmcache_unsupported_slots_never_issue_network_work(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, row, commands, _, _, error, _, _ = self.run_case(Path(directory), "lmcache")
            self.assertIsNone(error)
            measures = [command for command in commands if "--op" in command]
            invoked = {(int(command[command.index("--batch") + 1]), command[command.index("--op") + 1]) for command in measures}
            self.assertEqual(len(invoked), 10)
            for spec in row["operations"]:
                path = runner.out / "r0-lmcache" / f"b{spec['batch']}-{spec['operation']}.json"
                self.assertTrue(path.exists())
                if not spec["supported"]:
                    self.assertNotIn((spec["batch"], spec["operation"]), invoked)
                    self.assertEqual(json.loads(path.read_text())["status"], "not_supported")

    def test_native_measure_failure_cleans_own_epoch_and_prevents_later_puts(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, commands, events, active, error, returned, drains = self.run_case(Path(directory), "datasystem", fail_measure=True)
            self.assertIsNotNone(error)
            self.assertIsNone(returned)
            self.assertFalse(active)
            self.assertEqual(len([command for command in commands if command[0] == "fake-start"]), 1)
            self.assertGreaterEqual(drains, 2)
            self.assertFalse(any(event["event"] == "group_complete" for event in events))

    def test_cleanup_failure_is_never_success_or_suppressed(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, _, _, events, _, error, returned, _ = self.run_case(Path(directory), "datasystem", fail_cleanup=True)
            self.assertIsNotNone(error)
            self.assertIsNone(returned)
            self.assertFalse(any(event["event"] == "group_complete" for event in events))
            path = runner.out / "r0-datasystem/epochs/read/epoch-summary.json"
            self.assertTrue(path.exists())
            self.assertIsNot(json.loads(path.read_text()).get("valid"), True)


class FixedWarmResidentTests(unittest.TestCase):
    def test_original_worker_default_warms_same_4096_keys_per_client_for_every_b(self):
        from tests.unit.run.test_batch_harness import WorkerTests
        helper = WorkerTests()
        reference = None
        for batch in [1, 16, 256, 4096]:
            with self.subTest(batch=batch):
                adapter, connection, args = helper.run_worker(batch=batch, op="put", put_keys=32768)
                self.assertNotIn("put_warmup_keys", args, "Exercise the actual scientific default, not a test override")
                self.assertFalse(any("fatal" in record for record in connection.messages))
                warm_calls = [call for call in adapter.calls if call[:2] == ("warm", "put")]
                self.assertEqual(len(warm_calls), 4096 // batch)
                self.assertTrue(all(len(call[2]) == batch for call in warm_calls))
                warm = {key for call in warm_calls for key in call[2]}
                self.assertEqual(len(warm), 4096)
                self.assertTrue(all("-warm-" in key for key in warm))
                if reference is None:
                    reference = warm
                self.assertEqual(warm, reference)
                ready = next(record for record in connection.messages if record.get("warm"))
                self.assertEqual((ready["put_inserted_keys"], ready["put_verified_keys"]), (4096, 4096))
                pool = {key for stage, operation, keys, _ in adapter.calls if stage == "setup" and operation == "put" for key in keys}
                self.assertEqual(len(pool), 8192)
                self.assertTrue(pool.isdisjoint(warm))
                self.assertEqual(len(pool | warm) * 8, 98304)
                measured = {key for stage, operation, keys, _ in adapter.calls if stage == "measure" and operation == "put" for key in keys}
                self.assertTrue(measured.isdisjoint(pool | warm))


if __name__ == "__main__":
    unittest.main()
