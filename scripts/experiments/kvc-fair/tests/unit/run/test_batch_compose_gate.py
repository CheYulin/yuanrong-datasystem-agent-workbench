import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import batch_gate
from tests.unit.run.test_batch_gate import FakeAdapter


from kvcbench.collect.audits import integrity as compose


def fixture(root):
    (root / "suite.lock").touch()
    marker = root / "frozen.txt"
    marker.write_text("frozen fixture input\n")
    native = root / "native.bin"
    native.write_bytes(b"fixture artifact, never a real SDK")
    manifest = [{"name": name, "adapter": str(root / f"batch_{name}.py"), "config": str(root / f"{name}.json")}
                for name in compose.SHAS]
    (root / "products.json").write_text(json.dumps({"products": manifest}))
    stage = {"root": str(root), "inputs": {"frozen.txt": compose.record(marker)},
             "build_artifacts": {str(native): compose.record(native)}}
    (root / "batch-stage.json").write_text(json.dumps(stage))
    source, diagnostic = root / "batch-gate-001", root / "untraced-diagnostic-001"
    source.mkdir(); diagnostic.mkdir()
    (source / "batch-stage.json").write_bytes((root / "batch-stage.json").read_bytes())
    (source / "products.json").write_text(json.dumps(manifest))
    original = {"ended": "fixture-ended", "valid": False, "completed": ["r0-datasystem"],
                "failed": ["retained traced Mooncake GET failure"], "duration_s": 50.8}
    (source / "summary.json").write_text(json.dumps(original))
    commands = []
    for product in manifest:
        name = product["name"]
        directory = source / "r0-datasystem" if name == "datasystem" else diagnostic / name
        directory.mkdir()
        (directory / "lifecycle.log").write_text("offline fixture lifecycle\n")
        if name == "datasystem":
            (directory / "strace.fixture").write_text("offline fixture, not real TCP evidence\n")
        for index in [0, 1]:
            for mode in ["insert"] if name == "lmcache" else ["insert", "upsert"]:
                semantics = "insert_or_update" if name == "datasystem" or mode == "upsert" else "success_without_rewrite"
                adapter = FakeAdapter(semantics=semantics, limited=name == "lmcache")
                metadata = adapter.metadata() if callable(adapter.metadata) else adapter.metadata
                metadata["source_sha"] = compose.SHAS[name]
                result = batch_gate.run_gate(product["adapter"], {}, client_index=index, batch_sizes=compose.BATCHES,
                                             value_size=1024, run_id=f"fixture-{name}-{index}-{mode}",
                                             loader=lambda path, config: adapter)
                assert result["valid"]
                path = directory / f"client-{index}-{mode}.json"
                path.write_text(json.dumps(result))
                if name != "datasystem":
                    commands.append({"argv": ["python3", str(root / "batch_gate.py"), "--adapter", product["adapter"],
                                               "--config", product["config"], "--client-index", str(index),
                                               "--write-mode", mode, "--out", str(path)], "returncode": 0})
    summary = {"kind": "untraced_correctness_diagnostic", "valid": True, "ended": "fixture-ended",
               "products": [{"name": name, "valid": True} for name in ["mooncake", "lmcache"]],
               "failed": [], "commands": commands, "diagnostic_script_sha256": "a" * 64, "duration_s": 73.3}
    (diagnostic / "summary.json").write_text(json.dumps(summary))
    return source, diagnostic


class CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cached = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.cached.cleanup)
        fixture(Path(cls.cached.name))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(self.cached.name, self.root, dirs_exist_ok=True)
        for path in self.root.rglob("*.json"):
            path.write_text(path.read_text().replace(self.cached.name, str(self.root)))
        self.source, self.diagnostic = self.root / "batch-gate-001", self.root / "untraced-diagnostic-001"
        self.out = self.root / "batch-gate-002"

    def execute(self):
        return compose.compose(self.root, self.source, self.diagnostic, self.out)

    def mutate(self, path, function):
        value = json.loads(path.read_text())
        function(value)
        path.write_text(json.dumps(value))

    def test_success_is_explicit_composition_and_preserves_failure_and_source_bytes(self):
        before = compose.inventory(self.source), compose.inventory(self.diagnostic)
        result = self.execute()
        self.assertTrue(result["valid"])
        self.assertEqual(result["action"], "composed_correctness_gate")
        self.assertFalse(result["benchmark_eligible"])
        self.assertEqual(len(result["component_executions"]), 10)
        self.assertEqual(result["sources"]["original_gate"]["summary"]["failed"], ["retained traced Mooncake GET failure"])
        self.assertEqual(result["sources"]["untraced_diagnostic"]["summary"]["duration_s"], 73.3)
        self.assertEqual(before, (compose.inventory(self.source), compose.inventory(self.diagnostic)))
        self.assertEqual(compose.inventory(self.source / "r0-datasystem"), compose.inventory(self.out / "r0-datasystem"))
        self.assertEqual((self.source / "products.json").read_bytes(), (self.out / "products.json").read_bytes())
        self.assertEqual(set(result["completed"]), {"r0-datasystem", "r0-mooncake", "r0-lmcache"})
        snapshot = self.out / "provenance/batch_compose_gate.py"
        self.assertEqual(snapshot.read_bytes(), Path(compose.__file__).read_bytes())
        provenance = json.loads((self.out / "composition-provenance.json").read_text())
        self.assertEqual(provenance["composer_script"]["sha256"], compose.record(snapshot)["sha256"])

    def test_stage_input_or_native_artifact_drift_is_rejected_before_output(self):
        (self.root / "frozen.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError, "Frozen stage input"):
            self.execute()
        self.assertFalse(self.out.exists())

    def test_failed_diagnostic_is_not_accepted(self):
        self.mutate(self.diagnostic / "summary.json", lambda value: value.update(valid=False))
        with self.assertRaisesRegex(ValueError, "diagnostic"):
            self.execute()

    def test_native_artifact_bytes_are_rechecked(self):
        (self.root / "native.bin").write_bytes(b"changed native artifact")
        with self.assertRaisesRegex(ValueError, "native artifact changed"):
            self.execute()

    def test_wrong_pinned_source_and_failed_close_are_rejected(self):
        path = self.diagnostic / "lmcache/client-1-insert.json"
        original = path.read_text()
        self.mutate(path, lambda value: value["metadata"].update(source_sha="0" * 40))
        with self.assertRaisesRegex(ValueError, "Pinned source"):
            self.execute()
        path.write_text(original)
        def corrupt(value):
            next(step for step in value["steps"] if step["name"] == "close_adapter")["status"] = "failed"
        self.mutate(path, corrupt)
        with self.assertRaisesRegex(ValueError, "step outcomes"):
            self.execute()

    def test_traced_or_wrong_config_diagnostic_cannot_be_relabelled_untraced(self):
        def corrupt(value):
            value["commands"][0]["argv"].insert(0, "strace")
        self.mutate(self.diagnostic / "summary.json", corrupt)
        with self.assertRaisesRegex(ValueError, "strace"):
            self.execute()

    def test_duplicate_execution_identity_is_rejected(self):
        self.mutate(self.diagnostic / "summary.json", lambda value: value["commands"].append(value["commands"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.execute()

    def test_component_wrong_adapter_or_source_cannot_be_composed(self):
        path = self.diagnostic / "mooncake/client-0-insert.json"
        self.mutate(path, lambda value: value.update(adapter="different-adapter.py"))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.execute()

    def test_insert_semantics_cannot_be_changed_to_upsert(self):
        path = self.diagnostic / "mooncake/client-0-insert.json"
        self.mutate(path, lambda value: value["metadata"]["native_semantics"].update(write_existing_key="insert_or_update"))
        with self.assertRaisesRegex(ValueError, "semantics"):
            self.execute()

    def test_missing_reverse_or_failed_cleanup_is_rejected(self):
        path = self.source / "r0-datasystem/client-0-upsert.json"
        def corrupt(value):
            value["steps"] = [step for step in value["steps"] if not (step.get("batch_keys") == 16 and step["name"] == "get_reverse_exact")]
        self.mutate(path, corrupt)
        with self.assertRaisesRegex(ValueError, "get_reverse_exact"):
            self.execute()

    def test_existing_output_is_never_replaced(self):
        self.out.mkdir()
        marker = self.out / "original"
        marker.write_text("keep")
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
