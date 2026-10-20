"""Journaled cgroup preparation and restoration using an isolated filesystem."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from kvcbench.run import environment


class EnvironmentTests(unittest.TestCase):
    def fixture(self, directory):
        root = directory / "cgroups"
        initial = environment.expected_settings()
        initial["server/cpu.max"] = "max 100000"
        for name, value in initial.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)
        lock = directory / "resource.lock"
        lock.touch()
        return root, initial, lock

    def test_failed_service_stop_retains_journal_and_allows_explicit_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root, initial, lock = self.fixture(directory)
            preparation, restoration = directory / "prepare", directory / "restore"
            with patch.object(environment, "drain", return_value={"populated": []}), \
                 patch.object(environment, "unit_state", return_value={"active": True}), \
                 patch.object(environment.shutil, "which", return_value="/fixture/tool"), \
                 patch.object(environment.os, "geteuid", return_value=0), \
                 patch.object(environment, "execute", return_value={"returncode": 1}) as execute:
                with self.assertRaises(ValueError):
                    environment.prepare(root, preparation, resource_lock=lock)
                self.assertFalse(json.loads((preparation / "result.json").read_text())["accepted"])
                self.assertEqual(environment.settings(root), environment.expected_settings())
                execute.return_value = {"returncode": 0}
                environment.restore(root, preparation, restoration, resource_lock=lock)
                self.assertEqual(environment.settings(root), initial)

    def test_missing_required_tool_prevents_changes_and_leaves_failure_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root, initial, lock = self.fixture(directory)
            preparation = directory / "prepare"
            with patch.object(environment, "drain", return_value={"populated": []}), \
                 patch.object(environment, "unit_state", return_value={"active": False}), \
                 patch.object(environment.shutil, "which", return_value=None), \
                 patch.object(environment.os, "geteuid", return_value=0):
                with self.assertRaises(ValueError):
                    environment.prepare(root, preparation, resource_lock=lock)
            self.assertEqual(environment.settings(root), initial)
            self.assertFalse((preparation / "intent.json").exists())
            self.assertFalse(json.loads((preparation / "result.json").read_text())["accepted"])

    def test_changes_restore_original_values_and_preserve_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "cgroups"
            initial = environment.expected_settings()
            initial["server/cpu.max"] = "max 100000"
            for name, value in initial.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value)
            preparation, restoration = Path(temporary) / "prepare", Path(temporary) / "restore"
            lock = Path(temporary) / "resource.lock"
            lock.touch()
            with patch.object(environment, "drain", return_value={"populated": []}), \
                 patch.object(environment, "unit_state", return_value={"active": False}), \
                 patch.object(environment.shutil, "which", return_value="/fixture/tool"), \
                 patch.object(environment.os, "geteuid", return_value=0):
                result = environment.prepare(root, preparation, resource_lock=lock)
                self.assertEqual(result["changed_settings"], 1)
                before = (preparation / "intent.json").read_bytes()
                self.assertEqual(environment.settings(root), environment.expected_settings())
                self.assertTrue(environment.restore(root, preparation, restoration, resource_lock=lock)["accepted"])
                self.assertEqual(environment.settings(root), initial)
                self.assertEqual((preparation / "intent.json").read_bytes(), before)
                with self.assertRaises(FileExistsError):
                    environment.prepare(root, preparation, resource_lock=lock)


if __name__ == "__main__":
    unittest.main()
