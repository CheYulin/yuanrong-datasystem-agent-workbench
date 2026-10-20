"""Workflow admission fixtures; no SDK, host resource changes or native performance evidence."""
import copy
from pathlib import Path
import tempfile
from unittest import mock
import unittest

from kvcbench.core.spec import validate_runtime
from kvcbench.core.steps import Check
from kvcbench.pipeline import runtime
from kvcbench.pipeline.steps import perform
from kvcbench.platform.files import atomic_json, read_json
from kvcbench.platform.receipts import Workflow, verify_commit
from kvcbench.run.plan import plan


class RuntimeSpecTests(unittest.TestCase):
    def test_explicit_measurement_policy_and_finite_intervals(self):
        spec = {"schema_version": 1, "run_id": "fixture", "profile": "native-batch-tcp",
                **{key: "/fixture/" + key for key in ["root", "native_root", "build_catalog", "bundle", "resource_lock"]},
                "cgroup": "/sys/fs/cgroup/kvcfair20261020", "measurement": {"repeats": 3, "duration_s": 20,
                    "warmup_s": 3, "put_keys": 262144, "continue_readonly_errors": True}}
        self.assertEqual(validate_runtime(spec), spec)
        for key, value in [("repeats", 1), ("duration_s", float("nan")), ("warmup_s", True),
                           ("put_keys", 4097), ("continue_readonly_errors", False)]:
            bad = copy.deepcopy(spec)
            bad["measurement"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_runtime(bad)


class RuntimeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        atomic_json(self.root / "spec.json", {"fixture": True}, exclusive=True)
        self.workflow = Workflow.create(self.root / "workflow", "fixture", "native-batch-tcp", self.root / "spec.json")
        for name in ["W06", "W07.smoke", "W08"]:
            perform(self.workflow, name, action=lambda: ([Check("fixture", True, True, True)], [], {}),
                    scope="fixture only", sources=[Path(__file__)])
        self.suite = self.root / "formal-batch-001"
        self.suite.mkdir()
        (self.root / "payload").mkdir()
        atomic_json(self.suite / "parameters.json", {"fixture": True}, exclusive=True)
        atomic_json(self.suite / "products.json", [{"name": name} for name in ["datasystem", "mooncake", "lmcache"]], exclusive=True)
        self.rows = plan(3)
        atomic_json(self.suite / "plan.json", self.rows, exclusive=True)
        for row in self.rows:
            cell = self.suite / f"r{row['repeat']}-{row['product']}"
            cell.mkdir()
            for case in row["operations"]:
                atomic_json(cell / f"b{case['batch']}-{case['operation']}.json", {"fixture": True}, exclusive=True)
        self.epochs = [f"r{row['repeat']}-{row['product']}/" + epoch["id"] for row in self.rows for epoch in row["epochs"]]
        self.summary = {"completed": [f"r{row['repeat']}-{row['product']}" for row in self.rows],
                        "epochs_completed": self.epochs, "failed": [], "measurement_failures": [],
                        "start_wall_ns": 0, "start_mono_ns": 0}
        self.checked = {"evidence_verified": True, "issues": [], "native_failures": [], "counts": {"invalid": 0}}
        self.controller = runtime.FormalObserver(self.root, self.workflow, {"cgroup": "/fixture/cgroup"}, self.suite)

    def tearDown(self):
        self.temporary.cleanup()

    def test_native_failure_is_finding_without_becoming_native_success(self):
        failure = "r0-datasystem/b4096-exist_hit"
        self.summary["measurement_failures"] = [failure]
        self.checked["native_failures"] = [failure]
        self.controller.measurement_started(self.root / "fixture-ready.json")
        with mock.patch.object(runtime.admission, "verify_matrix", return_value=self.checked):
            self.controller.measurement_finished(self.summary, self.rows, self.epochs)
        commit = verify_commit(self.controller.measurement_commit)
        self.assertEqual(commit["acceptance"], "passed_with_findings")
        result = read_json(self.controller.measurement_commit.parent / "result.json")
        self.assertFalse(result["native_outcomes"]["passed"])
        first = self.suite / "r0-datasystem/b1-put.json"
        first.write_text("changed case")
        with self.assertRaises(ValueError):
            verify_commit(self.controller.measurement_commit)

    def test_missing_case_cannot_pass_even_when_controller_reports_complete(self):
        (self.suite / "r0-datasystem/b1-put.json").unlink()
        self.controller.measurement_started(self.root / "fixture-ready.json")
        with mock.patch.object(runtime.admission, "verify_matrix", return_value=self.checked):
            self.controller.measurement_finished(self.summary, self.rows, self.epochs)
        self.assertEqual(verify_commit(self.controller.measurement_commit)["acceptance"], "failed")

    def test_unknown_case_evidence_blocks_acceptance(self):
        self.checked.update(evidence_verified=False, issues=["invalid histogram"])
        self.controller.measurement_started(self.root / "fixture-ready.json")
        with mock.patch.object(runtime.admission, "verify_matrix", return_value=self.checked):
            self.controller.measurement_finished(self.summary, self.rows, self.epochs)
        self.assertEqual(verify_commit(self.controller.measurement_commit)["acceptance"], "failed")

    def test_new_uncommitted_attempt_blocks_old_pass_reuse(self):
        self.workflow.begin("W08")
        with self.assertRaisesRegex(ValueError, "uncommitted"):
            runtime.accepted(self.workflow, "W08")


if __name__ == "__main__":
    unittest.main()
