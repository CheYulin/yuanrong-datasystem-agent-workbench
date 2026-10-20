"""Complete nested source packaging, stable modes and fresh output contracts."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from kvcbench.build import bundle
from kvcbench.platform.files import atomic_json


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.parent = Path(self.temporary.name)
        self.source = self.parent / "source"
        self.source.mkdir()
        for name, content in {"kvcbench/__init__.py": "", "kvcbench/assets/nested.css": "fixture {}",
                              "run.sh": "#!/bin/sh\nexit 0\n", "README.md": "offline fixture"}.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (self.source / "run.sh").chmod(0o751)
        self.paths = sorted(["kvcbench/__init__.py", "kvcbench/assets/nested.css", "run.sh", "README.md", bundle.INVENTORY])
        self.write_inventory()
        self.out = self.parent / "output"

    def tearDown(self):
        self.temporary.cleanup()

    def write_inventory(self):
        atomic_json(self.source / bundle.INVENTORY, {"schema_version": 1, "paths": self.paths})

    def test_nested_assets_are_in_verified_zip_and_executable_mode_is_preserved(self):
        bundle.create(self.source, self.out)
        self.assertEqual(bundle.verify(self.out)["files"], len(self.paths))
        with zipfile.ZipFile(self.out / "bundle.zip") as archive:
            extracted = self.parent / "extracted"
            archive.extractall(extracted)
            self.assertEqual((extracted / "kvc-fair/kvcbench/assets/nested.css").read_text(), "fixture {}")
            self.assertEqual((archive.getinfo("kvc-fair/run.sh").external_attr >> 16) & 0o7777, 0o751)

    def test_new_package_file_omitted_from_inventory_is_rejected(self):
        (self.source / "kvcbench/new.py").write_text("# missing declared module")
        with self.assertRaisesRegex(ValueError, "omits package"):
            bundle.create(self.source, self.out)

    def test_missing_declared_file_is_rejected(self):
        (self.source / "README.md").unlink()
        with self.assertRaises(FileNotFoundError):
            bundle.create(self.source, self.out)

    def test_symlink_payload_is_rejected(self):
        path = self.source / "README.md"
        path.unlink()
        path.symlink_to(self.source / "run.sh")
        with self.assertRaisesRegex(ValueError, "symlink"):
            bundle.create(self.source, self.out)

    def test_refuses_overwrite_and_source_output_overlap(self):
        bundle.create(self.source, self.out)
        with self.assertRaises(FileExistsError):
            bundle.create(self.source, self.out)
        with self.assertRaises(ValueError):
            bundle.create(self.source, self.source / "output")

    def test_changed_archive_or_mode_cannot_pass(self):
        bundle.create(self.source, self.out)
        (self.out / "payload/run.sh").chmod(0o644)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            bundle.verify(self.out)

    def test_stage_package_installation_is_complete_and_keeps_modes(self):
        installed = self.parent / "installed"
        paths = bundle.install_package(self.source, installed)
        self.assertEqual(set(paths), {"kvcbench/__init__.py", "kvcbench/assets/nested.css"})
        self.assertFalse((installed / "run.sh").exists())
        self.assertEqual((installed / "kvcbench/assets/nested.css").read_bytes(),
                         (self.source / "kvcbench/assets/nested.css").read_bytes())

    def test_archive_extra_member_is_not_accepted(self):
        bundle.create(self.source, self.out)
        with zipfile.ZipFile(self.out / "bundle.zip", "a") as archive:
            archive.writestr("kvc-fair/extra.py", "unexpected")
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            bundle.verify(self.out)

    def test_repository_inventory_covers_tracked_and_new_source_files(self):
        tool = Path(bundle.__file__).resolve().parents[2]
        repo = tool.parents[2]
        if not (repo / ".git").exists():
            self.skipTest("Archive has no Git index; payload/ZIP membership is verified independently")
        names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard',
                                         '--', str(tool.relative_to(repo))], cwd=repo, text=True).splitlines()
        expected = {Path(name).relative_to(tool.relative_to(repo)).as_posix() for name in names if (repo / name).is_file()}
        self.assertEqual(set(bundle.inventory(tool)), expected)

    def test_batch_snapshot_captures_nested_frozen_inputs_and_refuses_mode_tampering(self):
        tool = Path(bundle.__file__).resolve().parents[2]
        runtime, suite, groups = [self.parent / name for name in ['runtime', 'suite', 'cgroups']]
        nested = runtime / 'kvcbench/platform/fixture.py'
        nested.parent.mkdir(parents=True)
        nested.write_text('# frozen runtime fixture\n')
        suite.mkdir()
        atomic_json(runtime / 'products.json', {'products': []})
        atomic_json(runtime / 'batch-stage.json', {'inputs': {'kvcbench/platform/fixture.py': {
            'sha256': hashlib.sha256(nested.read_bytes()).hexdigest(), 'mode': nested.stat().st_mode & 0o777}}})
        atomic_json(suite / 'summary.json', {'ended': True})
        for role in ['server', 'client', 'monitor']:
            path = groups / role / 'cgroup.procs'
            path.parent.mkdir(parents=True)
            path.write_text('')
        command = [sys.executable, str(tool / 'capture_evidence.py'), '--root', str(runtime),
                   '--suite', str(suite), '--cgroup-root', str(groups)]
        subprocess.run(command, check=True, capture_output=True, text=True)
        saved = suite / 'build-and-script-evidence'
        record = json.loads((saved / 'snapshot-manifest.json').read_text())
        row = next(row for row in record['files'] if row['path'] == 'kvcbench/platform/fixture.py')
        self.assertTrue(row['matches_current_session_script_hash'])
        (saved / row['path']).chmod(0o711)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mode mismatch', result.stderr)


if __name__ == "__main__":
    unittest.main()
