"""Plan-derived synthetic epoch evidence; no product data or process execution."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import batch_analyze
import batch_epoch_verify as subject
import batch_runner
from tests.unit.collect.test_batch_analysis import raw_result


class EpochEvidenceTests(unittest.TestCase):
    def fixture(self, parent):
        suite = parent / "synthetic-formal"
        suite.mkdir()
        plan = batch_runner.plan(3)
        parameters = {"root": "/synthetic/not-a-target", "action": "run", "workload_protocol": subject.PROTOCOL}
        summary = {"workload_protocol": subject.PROTOCOL, "epochs_expected": [], "epochs_completed": []}
        rows, events, files = [], [], []
        sequence = 0
        for group in plan:
            name = f"r{group['repeat']}-{group['product']}"
            for epoch in group["epochs"]:
                identity = name + "/" + epoch["id"]
                start = 1000000000 + sequence * 3000000000
                end = start + 2000000000
                run_dir = str(Path(parameters["root"]) / "run" / ("run-" + name + "-" + epoch["id"]))
                raw = {"identity": identity, "kind": epoch["kind"], "operations": epoch["operations"],
                       "run_dir": run_dir, "workload_protocol": subject.PROTOCOL, "valid": True,
                       "start_mono_ns": start, "end_mono_ns": end, "duration_s": 2,
                       "start_wall_ns": 1700000000000000000 + start, "end_wall_ns": 1700000000000000000 + end,
                       "drained": {"checked_utc": "synthetic-only", "populated": [], "members": {
                           "/sys/fs/cgroup/kvcfair20261020/server/cgroup.procs": [],
                           "/sys/fs/cgroup/kvcfair20261020/client/cgroup.procs": []}}}
                path = suite / name / "epochs" / epoch["id"] / "epoch-summary.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(raw));files.append(path)
                summary["epochs_expected"].append(identity)
                summary["epochs_completed"].append(identity)
                events += [{"event": "epoch_start", "group": name, "epoch": epoch["id"],
                            "run_dir": run_dir, "epoch_kind": epoch["kind"], "mono_ns": start + 10,
                            "wall_ns": raw["start_wall_ns"] + 10},
                           {"event": "epoch_complete", "group": name, "epoch": epoch["id"], "valid": True,
                            "mono_ns": end + 10, "wall_ns": raw["end_wall_ns"] + 10}]
                for index, spec in enumerate(epoch["operations"]):
                    rows.append({"identity": name + f"/b{spec['batch']}-{spec['operation']}", "valid": True,
                                 "start_mono_ns": start + 1000000 * (index + 1),
                                 "end_mono_ns": start + 1000000 * (index + 1) + 1000})
                sequence += 1
        return suite, plan, parameters, summary, rows, events, files

    @staticmethod
    def mutate(path, change):
        raw = json.loads(path.read_text());change(raw);path.write_text(json.dumps(raw))

    @staticmethod
    def verify(fixture):
        return subject.verify(*fixture[:6])

    def test_complete_36_epoch_plan_derived_synthetic_evidence_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            issues, epochs = self.verify(fixture)
            self.assertEqual(issues, [])
            self.assertEqual(len(epochs), 36)
            self.assertEqual(len(fixture[4]), 150)
            self.assertEqual(len({row["run_dir"] for row in epochs}), 36)
            self.assertTrue(all(row.get("epoch_evidence") for row in fixture[4]))

    def test_reused_directory_even_with_matching_start_event_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory));paths = fixture[-1]
            reused = json.loads(paths[0].read_text())["run_dir"]
            self.mutate(paths[1], lambda raw: raw.update(run_dir=reused))
            fixture[5][2]["run_dir"] = reused
            self.assertTrue(self.verify(fixture)[0])

    def test_missing_epoch_summary_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory));fixture[-1][5].unlink()
            self.assertTrue(self.verify(fixture)[0])

    def test_sdk_window_must_be_inside_its_own_native_lifetime(self):
        for field in ["start_mono_ns", "end_mono_ns"]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(Path(directory));raw = json.loads(fixture[-1][0].read_text())
                fixture[4][0][field] = raw[field] + (-1 if field == "start_mono_ns" else 1)
                self.assertTrue(self.verify(fixture)[0])

    def test_unknown_or_nonempty_or_missing_cgroup_members_are_not_empty_evidence(self):
        changes = [lambda raw: raw["drained"]["members"].update({"/sys/fs/cgroup/kvcfair20261020/server/cgroup.procs": None}),
                   lambda raw: raw["drained"]["members"].update({"/sys/fs/cgroup/kvcfair20261020/client/cgroup.procs": [123]}),
                   lambda raw: raw["drained"].update(members={}),
                   lambda raw: raw["drained"].update(populated=["server"])]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(Path(directory));self.mutate(fixture[-1][0], change)
                self.assertTrue(self.verify(fixture)[0])

    def test_missing_actual_monotonic_start_end_and_nonfinite_duration_are_rejected(self):
        changes = [lambda raw: raw.pop("start_mono_ns"), lambda raw: raw.pop("end_mono_ns"),
                   lambda raw: raw.update(duration_s=float("nan")), lambda raw: raw.update(duration_s=float("inf"))]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(Path(directory));self.mutate(fixture[-1][0], change)
                self.assertTrue(self.verify(fixture)[0])

    def test_missing_unique_start_complete_events_or_complete_list_is_rejected(self):
        for mode in ["start", "complete", "duplicate", "list", "expected"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                fixture = self.fixture(Path(directory))
                if mode in {"start", "complete"}:
                    fixture[5].pop(0 if mode == "start" else 1)
                elif mode == "duplicate":
                    fixture[5].append(deepcopy(fixture[5][0]))
                else:
                    fixture[3].pop("epochs_completed" if mode == "list" else "epochs_expected")
                self.assertTrue(self.verify(fixture)[0])

    def test_put_cannot_be_moved_into_read_epoch_even_if_ledger_is_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory));group = fixture[1][0]
            put = next(spec for spec in group["operations"] if spec["operation"] == "put")
            put["epoch"] = "read"
            group["epochs"][0]["operations"].append(put)
            self.mutate(fixture[-1][0], lambda raw: raw.update(operations=group["epochs"][0]["operations"]))
            self.assertTrue(self.verify(fixture)[0])


class IsolatedPreloadTests(unittest.TestCase):
    def test_every_put_requires_its_own_pool_preload_and_fixed_warm_keys(self):
        for batch in [1, 16, 256, 4096]:
            with self.subTest(batch=batch):
                raw = raw_result("put", batch)
                raw["parameters"].update(skip_preload=False, put_warmup_keys=32768)
                for row in raw["setup"]:
                    row["preloaded"] = True
                for row in raw["warmup"]:
                    row.update(put_inserted_keys=4096, put_verified_keys=4096)
                row = {"identity": f"r0-datasystem/b{batch}-put", "product": "datasystem", "repeat": 0,
                       "batch": batch, "operation": "put", "supported": True}
                context = {"isolated_epochs": True, "skip_preload": False}
                correct = batch_analyze.measured_row(deepcopy(raw), deepcopy(row), [], [], context)
                self.assertTrue(correct["valid"], correct["issues"])
                raw["parameters"]["skip_preload"] = True
                for record in raw["setup"]:
                    record["preloaded"] = False
                rejected = batch_analyze.measured_row(raw, row, [], [], context)
                self.assertFalse(rejected["valid"])
                self.assertIn("parameters.skip_preload mismatch", rejected["issues"])


if __name__ == "__main__":
    unittest.main()
