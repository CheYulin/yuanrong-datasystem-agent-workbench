"""Fresh local staging fixtures for post-overlay executable modes and frozen inputs."""
import hashlib
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import batch_stage


def write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    path.chmod(mode)


def snapshot(root):
    return {path.relative_to(root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


class StageOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.parent = Path(self.temporary.name)
        self.base, self.bundle, self.target = [self.parent / name for name in ["base", "bundle", "fresh"]]
        self.base.mkdir()
        self.bundle.mkdir()
        for name in ["datasystem", "scripts/datasystem", "mooncake/source", "mooncake/deps", "mooncake/venv",
                     "mooncake/wheels", "lmcache/source", "lmcache/venv311"]:
            (self.base / name).mkdir(parents=True, exist_ok=True)
        write(self.base / "suite.lock", "")
        plan = [{"repeat": repeat, "product": product, "size": size, "concurrency": concurrency}
                for repeat in range(3) for product in ["datasystem", "mooncake", "lmcache"]
                for size in [1024, 65536, 1048576] for concurrency in [2, 8, 16]]
        completed = [f"r{row['repeat']}-{row['product']}-s{row['size']}-c{row['concurrency']}" for row in plan]
        write(self.base / "formal-tcp-001/plan.json", json.dumps(plan))
        write(self.base / "formal-tcp-001/summary.json", json.dumps({"ended": "fixture-only", "failed": [], "completed": completed}))
        write(self.base / "formal-tcp-001/builds/fixture.json", '{"scope":"offline-only"}')
        native = self.base / "datasystem/fixture-native"
        write(native, "fixture native, never executed", 0o755)
        write(self.base / "tcp-stage.json", json.dumps({"native_artifacts_revalidated": {
            str(native): {"sha256": hashlib.sha256(native.read_bytes()).hexdigest()}}}))
        products = []
        for product, destination in [("datasystem", "scripts/datasystem"), ("mooncake", "mooncake"), ("lmcache", "lmcache")]:
            write(self.base / destination / "adapter.py", f"# fixture {product}\n")
            write(self.base / destination / "start.sh", "#!/bin/sh\nexit 0\n", 0o755)
            write(self.base / destination / "stop.sh", "#!/bin/sh\nexit 0\n", 0o755)
            write(self.base / "configs" / (product + ".json"), '{}')
            products.append({"name": product, "adapter": str(self.base / destination / "adapter.py"),
                             "config": str(self.base / "configs" / (product + ".json")), "env": {},
                             "start": ["python3", str(self.base / destination / "runtime.py"), "start"] if product == "datasystem"
                                      else ["bash", str(self.base / destination / "start.sh")],
                             "stop": ["bash", str(self.base / destination / "stop.sh")]})
        write(self.base / "products.json", json.dumps({"products": products}))
        write(self.bundle / "batch_fixture.py", "# frozen top-level fixture\n")
        write(self.bundle / "datasystem/runtime.py", "# explicit fixture runtime\n")
        write(self.bundle / "mooncake/launch_master.sh", "#!/bin/sh\n# pure KV fixture\nexit 0\n", 0o644)
        write(self.bundle / "lmcache/start_server_tcp.sh", "#!/bin/sh\n# pure KV fixture\nexit 0\n", 0o644)
        write(self.base / "mooncake/launch_master.sh", "#!/bin/sh\n# old executable\n", 0o755)
        write(self.base / "lmcache/start_server.sh", "#!/bin/sh\n# old executable\n", 0o755)
        self.args = SimpleNamespace(base=self.base, root=self.target, bundle=self.bundle, cgroup=self.parent / "fake-cgroup",
                                    feature_profile="pure-kv", mooncake_queue=8192)

    def tearDown(self):
        self.temporary.cleanup()

    def stage(self):
        with mock.patch.object(batch_stage, "drain") as drain:
            result = batch_stage.stage(self.args)
        drain.assert_called_once_with(self.args.cgroup)
        return result

    def test_copy2_overrides_cannot_remove_shell_execute_bits_and_actual_modes_are_frozen(self):
        before_base, before_bundle = snapshot(self.base), snapshot(self.bundle)
        result = self.stage()
        self.assertEqual(result["feature_profile"], "pure-kv")
        self.assertEqual(snapshot(self.base), before_base)
        self.assertEqual(snapshot(self.bundle), before_bundle)
        for name in ["mooncake/launch_master.sh", "lmcache/start_server.sh"]:
            target = self.target / name
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
            record = next(row for row in result["runtime_overrides"] if row["path"] == name)
            self.assertEqual(record["mode"], stat.S_IMODE(target.stat().st_mode))
            self.assertEqual(record["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(result, json.loads((self.target / "batch-stage.json").read_text()))
        for name, record in result["inputs"].items():
            path = self.target / name
            self.assertEqual(record["mode"], stat.S_IMODE(path.stat().st_mode), name)
            self.assertEqual(record["sha256"], hashlib.sha256(path.read_bytes()).hexdigest(), name)
            self.assertEqual(record["bytes"], path.stat().st_size, name)
        self.assertEqual((self.target / "suite.lock").stat().st_ino, (self.base / "suite.lock").stat().st_ino)
        for name in ["datasystem", "mooncake/source", "lmcache/source"]:
            self.assertTrue((self.target / name).is_symlink())
            self.assertFalse(any(key == name or key.startswith(name + "/") for key in result["inputs"]))

    def test_overlay_preserves_restricted_permissions_while_adding_execute(self):
        (self.bundle / "mooncake/launch_master.sh").chmod(0o640)
        self.stage()
        self.assertEqual(stat.S_IMODE((self.target / "mooncake/launch_master.sh").stat().st_mode), 0o751)
        self.assertEqual(stat.S_IMODE((self.bundle / "mooncake/launch_master.sh").stat().st_mode), 0o640)

    def test_nested_package_is_installed_and_bound_to_actual_frozen_bytes_and_mode(self):
        module = self.bundle / "kvcbench/platform/fixture.py"
        write(module, "# fixture package input\n", 0o640)
        names = sorted([p.relative_to(self.bundle).as_posix() for p in self.bundle.rglob('*') if p.is_file()]
                       + ['bundle-inputs.json'])
        write(self.bundle / 'bundle-inputs.json', json.dumps({'schema_version': 1, 'paths': names}))
        result = self.stage()
        target = self.target / 'kvcbench/platform/fixture.py'
        self.assertEqual(target.read_bytes(), module.read_bytes())
        self.assertEqual(result['inputs']['kvcbench/platform/fixture.py']['mode'], 0o640)
        self.assertEqual(result['inputs']['kvcbench/platform/fixture.py']['sha256'], hashlib.sha256(module.read_bytes()).hexdigest())

    def test_existing_root_and_alias_rejected_without_overwriting_evidence(self):
        self.stage()
        original = snapshot(self.target)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(snapshot(self.target), original)
        alias = self.parent / "alias"
        alias.symlink_to(self.target, target_is_directory=True)
        self.args.root = alias
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(snapshot(self.target), original)

    def test_missing_and_symlink_overlay_roll_back_fresh_stage(self):
        path = self.bundle / "mooncake/launch_master.sh"
        path.unlink()
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.parent.glob(".fresh-*")), [])
        other = self.parent / "external.sh"
        write(other, "external")
        path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.target.exists())
        self.assertEqual(other.read_text(), "external")

    def test_changed_native_build_rejected_before_staging(self):
        write(self.base / "datasystem/fixture-native", "changed build", 0o755)
        with self.assertRaises(RuntimeError):
            self.stage()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.parent.glob(".fresh-*")), [])


if __name__ == "__main__":
    unittest.main()
