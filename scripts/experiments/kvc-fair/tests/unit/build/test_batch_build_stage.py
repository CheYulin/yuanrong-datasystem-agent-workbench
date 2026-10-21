"""Local Git/build-identity fixtures; no actual product build, service or target access."""
import contextlib
import fcntl
import copy
import hashlib
import io
import json
import os
from pathlib import Path
from tests.paths import TOOL_ROOT
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from kvcbench.compat.entrypoints import batch_build_stage as bootstrap
from kvcbench.compat.entrypoints import batch_stage as batch_stage
from kvcbench.build import metadata as build_metadata
HERE = TOOL_ROOT


def write(path, value, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    path.chmod(mode)


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True,
                                   stderr=subprocess.STDOUT, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).strip()


def snapshot(root):
    return {path.relative_to(root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


class BuildStageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.parent = Path(self.temporary.name)
        self.native, self.bundle, self.target = [self.parent / name for name in ["native", "bundle", "batch"]]
        self.native.mkdir()
        self.bundle.mkdir()
        self.specs = copy.deepcopy(build_metadata.PRODUCTS)
        outputs = {"datasystem": ["datasystem/output/sdk.whl", "datasystem/output/sdk.tar.gz",
                                   "datasystem/output/datasystem/service/datasystem_worker",
                                   "datasystem/output/datasystem/service/datasystem_coordinator"],
                   "mooncake": ["mooncake/wheels/mooncake_transfer_engine-fixture.whl", "mooncake/deps/lib/libxxhash.so.0"],
                   "lmcache": [f"lmcache/source/lmcache/{name}.fixture.so" for name in ["lmcache_native", "lmcache_fs", "lmcache_redis"]]}
        for name, spec in self.specs.items():
            source = self.native / spec["source"]
            source.mkdir(parents=True)
            git(source, "init", "-q")
            git(source, "config", "user.name", "Offline fixture")
            git(source, "config", "user.email", "fixture@example.invalid")
            ignored = {"datasystem": "output/\n.venv/\n.evidence/\n", "mooncake": "build/\n", "lmcache": "lmcache/*.so\n"}[name]
            write(source / ".gitignore", ignored)
            write(source / "source.txt", "fixture source; never compiled\n")
            git(source, "add", ".gitignore", "source.txt")
            git(source, "commit", "-qm", "offline fixture")
            spec["sha"] = git(source, "rev-parse", "HEAD")
            git(source, "tag", spec["tag"])
            write(self.native / spec["script"], "#!/bin/sh\n# fixture build recipe, never executed\n", 0o755)
            write(self.native / spec["python"], "#!/bin/sh\necho Python 3.11.15\n", 0o755)
            for relative in outputs[name]:
                write(self.native / relative, "fixture-native-bytes: " + relative, 0o755 if relative.endswith(("datasystem_worker", "datasystem_coordinator")) else 0o644)
            row = {"schema_version": 1, "product": name, "tag": spec["tag"], "sha": spec["sha"],
                   "pinned_sha": spec["sha"], "tag_sha": spec["sha"], "official_source": spec["official_source"],
                   "source_directory": str(source), "source_status_porcelain": "", "source_tracked_status_porcelain": "",
                   "source_worktree_clean": True, "build_exit": 0, "identity_valid": True, "validation_errors": [],
                   "python_executable": str(self.native / spec["python"]), "python": "Python 3.11.15",
                   "build_started_wall_ns": 100, "build_finished_wall_ns": 200,
                   "build_recipe": spec["script"], "build_recipe_sha256": build_metadata.digest(self.native / spec["script"]),
                   "artifact_hashes": {relative: build_metadata.digest(self.native / relative) for relative in outputs[name]},
                   "artifact_sizes": {relative: (self.native / relative).stat().st_size for relative in outputs[name]},
                   "smoke_exit": None}
            write(self.native / f"build-metadata/{name}.json", json.dumps(row))
        for name in ["mooncake/source", "mooncake/deps", "mooncake/venv", "mooncake/wheels", "lmcache/source", "lmcache/venv311"]:
            (self.native / name).mkdir(parents=True, exist_ok=True)
        write(self.bundle / "products.json", (HERE / "configs/templates/products.json").read_text())
        write(self.bundle / "batch_fixture.py", "# fixture staged input\n")
        for name in ["datasystem", "mooncake", "lmcache"]:
            write(self.bundle / name / "runtime.py", "# fixture runtime, never executed\n")
            write(self.bundle / name / "adapter.py", "# fixture adapter, never loaded\n")
            write(self.bundle / name / "start.sh", "#!/bin/sh\nexit 0\n")
            write(self.bundle / name / "stop.sh", "#!/bin/sh\nexit 0\n")
        write(self.bundle / "datasystem/adapter_datasystem.py", "# fixture scalar adapter\n")
        write(self.bundle / "lmcache/adapter_lmcache_tcp.py", "# fixture TCP adapter\n")
        write(self.bundle / "mooncake/launch_master.sh", "#!/bin/sh\n# fixture pure KV\n")
        write(self.bundle / "lmcache/start_server_tcp.sh", "#!/bin/sh\n# fixture TCP pure KV\n")
        self.patch = mock.patch.object(build_metadata, "PRODUCTS", self.specs)
        self.patch.start()
        self.args = SimpleNamespace(base=self.native, builds_only_native_root=self.native, root=self.target,
                                    bundle=self.bundle, cgroup=self.parent / "fake-cgroup", feature_profile="pure-kv", mooncake_queue=8192)

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def stage(self):
        with mock.patch.object(batch_stage, "drain"):
            return batch_stage.stage(self.args)

    def test_explicit_revalidation_preserves_original_metadata(self):
        from kvcbench.build.catalog import validate_builds, install_provenance
        paths = {name: f"revalidation/attempt-001/{name}.json" for name in self.specs}
        for name, relative in paths.items():
            original = self.native / f"build-metadata/{name}.json"
            write(self.native / relative, original.read_text())
            row = json.loads(original.read_text())
            row["identity_valid"] = False
            write(original, json.dumps(row))
        original_bytes = {name: (self.native / f"build-metadata/{name}.json").read_bytes() for name in paths}
        with self.assertRaises(ValueError):
            validate_builds(self.native)
        records, _, provenance = validate_builds(self.native, record_paths=paths)
        self.target.mkdir()
        install_provenance(self.native, self.target, records, provenance)
        self.assertEqual(provenance["metadata_paths"], paths)
        for name, relative in paths.items():
            self.assertEqual((self.target / "builds-provenance/inputs" / relative).read_bytes(),
                             (self.native / relative).read_bytes())
            self.assertEqual((self.native / f"build-metadata/{name}.json").read_bytes(), original_bytes[name])
        write(self.native / paths["datasystem"], "{}")
        with self.assertRaises(ValueError):
            install_provenance(self.native, self.target, records, provenance)

    def test_explicit_metadata_selection_requires_complete_safe_paths(self):
        from kvcbench.build.catalog import validate_builds
        with self.assertRaises(ValueError):
            validate_builds(self.native, record_paths={"datasystem": "build-metadata/datasystem.json"})
        paths = {name: f"build-metadata/{name}.json" for name in self.specs}
        paths["datasystem"] = "../outside.json"
        with self.assertRaises(ValueError):
            validate_builds(self.native, record_paths=paths)

    def test_revalidation_is_read_only_for_original_build_attempt(self):
        from kvcbench.build.revalidate import revalidate
        from kvcbench.build.catalog import validate_saved_catalog
        write(self.native / "build-result.json", json.dumps({"fatal": None, "cleanup": {
            "removed": True, "drained": {"populated": []}}}))
        for name, spec in self.specs.items():
            write(self.native / f"commands/{name}/build-command.json", json.dumps({
                "returncode": 0, "error": None, "argv": ["bash", str(self.native / spec["script"])],
                "cwd": str(self.native), "start_wall_ns": 100, "end_wall_ns": 200,
                "log": str(self.native / f"commands/{name}/build.log")}))
            write(self.native / f"commands/{name}/build.log", "fixture completed; no real compilation\n")
        before = snapshot(self.native)
        out = self.native / "revalidation/attempt-001"
        with contextlib.redirect_stdout(io.StringIO()):
            result = revalidate(self.native, out)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["compiled_in_this_attempt"])
        after = snapshot(self.native)
        self.assertEqual(before, {path: value for path, value in after.items()
                                 if not path.startswith("revalidation/") and path != "suite.lock"})
        validate_saved_catalog(out / "build-catalog.json", self.native)
        with self.assertRaises(FileExistsError):
            revalidate(self.native, out)
        write(self.native / "datasystem/output/sdk.whl", "changed native bytes")
        with self.assertRaises(ValueError):
            validate_saved_catalog(out / "build-catalog.json", self.native)

    def metadata(self, product="datasystem"):
        return self.native / f"build-metadata/{product}.json"

    def change_metadata(self, key, value, product="datasystem"):
        path = self.metadata(product)
        row = json.loads(path.read_text())
        row[key] = value
        path.write_text(json.dumps(row))

    def test_fresh_builds_need_no_legacy_suite_or_smoke_and_create_only_real_evidence(self):
        before = snapshot(self.native)
        self.assertFalse((self.native / "suite.lock").exists())
        record = self.stage()
        self.assertEqual(record["source_build_mode"], "builds-only-native-root")
        self.assertFalse(record["legacy_suites_required"])
        self.assertFalse(record["baseline_performance_reused"])
        self.assertEqual(len(record["build_artifacts"]), 9)
        self.assertEqual(set(snapshot(self.native)) - set(before), {"suite.lock"})
        for name, expected in before.items():
            self.assertEqual(snapshot(self.native)[name], expected)
        for path in [self.native, self.target]:
            self.assertEqual(list(path.glob("formal*")), [])
            self.assertFalse((path / "tcp-stage.json").exists())
        provenance = record["builds_provenance"]
        self.assertEqual(provenance["schema_version"], 1)
        proof_path = self.target / provenance["path"]
        self.assertEqual(bootstrap.identity(proof_path), {key: provenance[key] for key in ["sha256", "bytes", "mode"]})
        self.assertEqual(len(provenance["inputs"]), 9)
        for relative, expected in provenance["inputs"].items():
            self.assertEqual(bootstrap.identity(self.target / relative), expected)
        proof = json.loads(proof_path.read_text())
        for name, spec in self.specs.items():
            metadata = json.loads((self.target / f"builds/{name}.json").read_text())
            self.assertIsNone(metadata["smoke_exit"])
            self.assertEqual(proof["products"][name]["head"], spec["sha"])
            for relative in [f"build-metadata/{name}.json", spec["script"]]:
                copy_path = self.target / "builds-provenance/inputs" / relative
                self.assertEqual(copy_path.read_bytes(), (self.native / relative).read_bytes())
                self.assertEqual(proof["inputs"][relative], bootstrap.identity(copy_path))
        products = {row["name"]: row for row in json.loads((self.target / "products.json").read_text())["products"]}
        self.assertEqual(products["datasystem"]["start"][-4:], ["--transport", "tcp", "--profile", "pure-kv"])
        ds = json.loads((self.target / "configs/datasystem.json").read_text())
        lm = json.loads((self.target / "configs/lmcache.json").read_text())
        self.assertEqual(ds["endpoints"], ["127.0.0.1:31531", "127.0.0.1:31532"])
        self.assertTrue(lm["scalar_adapter"].endswith("lmcache/adapter_lmcache_tcp.py"))
        self.assertEqual(lm["endpoints"], ["tcp://127.0.0.1:15550", "tcp://127.0.0.1:15551"])
        self.assertEqual(products["mooncake"]["env"]["MC_STORE_MEMCPY"], "0")
        self.assertEqual((self.native / "suite.lock").stat().st_ino, (self.target / "suite.lock").stat().st_ino)
        for relative, expected in record["inputs"].items():
            self.assertEqual(bootstrap.identity(self.target / relative), expected)
        for override in record["runtime_overrides"]:
            path = self.target / override["path"]
            self.assertEqual(override["mode"], stat.S_IMODE(path.stat().st_mode))

    def test_native_artifact_and_recipe_tamper_are_rejected(self):
        artifact = self.native / "datasystem/output/sdk.whl"
        artifact.write_text("tampered")
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.parent.glob(".batch-*")), [])

    def test_recipe_changed_after_build_record_is_rejected(self):
        (self.native / self.specs["mooncake"]["script"]).write_text("changed recipe")
        with self.assertRaises(ValueError):
            self.stage()

    def test_ninth_master_artifact_is_accepted_and_report_binds_complete_inventory(self):
        relative = "mooncake/source/build/release/mooncake_master"
        master = self.native / relative
        write(master, "fixture native master, never executed", 0o755)
        row = json.loads(self.metadata("mooncake").read_text())
        row["artifact_hashes"][relative] = build_metadata.digest(master)
        row["artifact_sizes"][relative] = master.stat().st_size
        self.metadata("mooncake").write_text(json.dumps(row))
        stage = self.stage()
        self.assertEqual(len(stage["build_artifacts"]), 10)
        self.assertIn(str(master), stage["build_artifacts"])
        from kvcbench.compat import reports as batch_report
        suite = self.parent / "consumer-suite"
        write(suite / "batch-stage.json", json.dumps(stage))
        checked_stage, runtime = batch_report.verify_runtime_inputs(suite, self.target)
        builds = [json.loads(path.read_text()) for path in sorted((self.target / "builds").glob("*.json"))]
        accepted = batch_report.verify_source_build_provenance(checked_stage, runtime, builds)
        self.assertTrue(accepted["verified"])
        self.assertEqual(accepted["native_artifacts"], 10)
        self.assertTrue(all(value is None for value in accepted["legacy_smoke_exit"].values()))

    def test_zero_or_noninteger_sizes_cannot_pass_coherently_resealed_metadata(self):
        relative = "datasystem/output/sdk.whl"
        path = self.native / relative
        original = json.loads(self.metadata().read_text())
        for content, size in [(b"", 0), (b"x", True), (b"x", 1.0)]:
            path.write_bytes(content)
            row = copy.deepcopy(original)
            row["artifact_hashes"][relative] = build_metadata.digest(path)
            row["artifact_sizes"][relative] = size
            self.metadata().write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError, "Native build artifact changed"):
                self.stage()
            self.assertFalse(self.target.exists())

    def test_copy_target_corruption_is_rejected_and_transaction_is_rolled_back(self):
        original_copy = bootstrap.shutil.copy2
        for suffix in ["builds/datasystem.json", "builds-provenance/inputs/mooncake/build.sh"]:
            def corrupt(source, target, *args, **kwargs):
                result = original_copy(source, target, *args, **kwargs)
                if Path(target).as_posix().endswith(suffix):
                    Path(target).write_bytes(b"fixture copy-target corruption")
                return result
            with mock.patch.object(bootstrap.shutil, "copy2", side_effect=corrupt), self.assertRaisesRegex(ValueError, "Copied build"):
                self.stage()
            self.assertFalse(self.target.exists())
            self.assertEqual(list(self.parent.glob(".batch-*")), [])

    def test_recorded_success_cannot_replace_current_tracked_cleanliness(self):
        (self.native / "datasystem/source.txt").write_text("modified after recorded build")
        with self.assertRaises(ValueError):
            self.stage()

    def test_unapproved_untracked_source_is_rejected(self):
        (self.native / "mooncake/source/unexpected.txt").write_text("unknown")
        with self.assertRaises(ValueError):
            self.stage()

    def test_actual_tag_and_head_are_both_revalidated(self):
        source = self.native / "lmcache/source"
        original = self.specs["lmcache"]["sha"]
        git(source, "commit", "--allow-empty", "-qm", "another fixture commit")
        with self.assertRaises(ValueError):
            self.stage()
        other = git(source, "rev-parse", "HEAD")
        git(source, "checkout", "--detach", original)
        git(source, "tag", "-f", self.specs["lmcache"]["tag"], other)
        with self.assertRaises(ValueError):
            self.stage()

    def test_incomplete_or_false_metadata_cannot_bootstrap(self):
        original = self.metadata().read_text()
        for field, value in [("identity_valid", False), ("build_exit", 1), ("tag_sha", "0" * 40),
                             ("artifact_sizes", {}), ("validation_errors", ["known failure"]),
                             ("python", "Python 3.12.0"), ("source_directory", str(self.parent / "outside"))]:
            self.metadata().write_text(original)
            self.change_metadata(field, value)
            with self.assertRaises(ValueError, msg=field):
                self.stage()
            self.assertFalse(self.target.exists())

    def test_artifact_symlink_or_outside_metadata_path_cannot_escape_root(self):
        path = self.native / "mooncake/wheels/mooncake_transfer_engine-fixture.whl"
        outside = self.parent / "outside.whl"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.stage()
        path.unlink()
        path.write_bytes(outside.read_bytes())
        row = json.loads(self.metadata("mooncake").read_text())
        for key in ["artifact_hashes", "artifact_sizes"]:
            relative = next(iter(row[key]))
            row[key][str(outside)] = row[key].pop(relative)
        self.metadata("mooncake").write_text(json.dumps(row))
        with self.assertRaises(ValueError):
            self.stage()

    def test_metadata_symlink_is_rejected_before_json_is_read(self):
        path = self.metadata()
        outside = self.parent / "outside-not-json"
        outside.write_text("this must not be read as metadata")
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Regular in-root build evidence"):
            self.stage()

    def xxhash_aliases(self):
        original = self.native / "mooncake/deps/lib/libxxhash.so.0"
        actual = original.with_name("libxxhash.so.0.8.3")
        original.rename(actual)
        row = json.loads(self.metadata("mooncake").read_text())
        for name in ["artifact_hashes", "artifact_sizes"]:
            row[name][str(actual.relative_to(self.native))] = row[name].pop(str(original.relative_to(self.native)))
        self.metadata("mooncake").write_text(json.dumps(row))
        original.symlink_to(actual.name)
        original.with_name("libxxhash.so").symlink_to(original.name)
        return actual

    def test_xxhash_alias_chain_matches_record_regular_outputs_without_fake_artifacts(self):
        actual = self.xxhash_aliases()
        record = self.stage()
        proof = json.loads((self.target / record["builds_provenance"]["path"]).read_text())
        self.assertEqual(len(record["build_artifacts"]), 9)
        self.assertEqual(len(proof["artifact_aliases"]), 2)
        for path, alias in proof["artifact_aliases"].items():
            self.assertEqual(alias["target"], str(actual))
            self.assertEqual(alias["link_text"], os.readlink(path))
            self.assertNotIn(path, proof["native_artifacts"])

    def test_unrecorded_alias_cannot_target_outside_or_unrecorded_library(self):
        self.xxhash_aliases()
        alias = self.native / "mooncake/deps/lib/libxxhash.so.unexpected"
        outside = self.parent / "external-library.so"
        outside.write_text("outside dependency")
        alias.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "alias escaped"):
            self.stage()
        alias.unlink()
        unknown = self.native / "mooncake/deps/unknown-library"
        unknown.write_text("not in recorded outputs")
        alias.symlink_to(unknown)
        with self.assertRaisesRegex(ValueError, "recorded regular output"):
            self.stage()

    def test_lock_symlink_and_fifo_rejected_without_following_or_blocking(self):
        outside = self.parent / "outside.lock"
        outside.write_text("protected")
        lock = self.native / "suite.lock"
        lock.symlink_to(outside)
        with self.assertRaises(OSError):
            self.stage()
        self.assertEqual(outside.read_text(), "protected")
        lock.unlink()
        os.mkfifo(lock)
        with self.assertRaises(ValueError):
            self.stage()

    def test_distinct_runtime_resource_lock_is_shared_and_blocks_staging(self):
        resource = self.parent / "runtime-resource.lock"
        resource.write_text("")
        self.args.resource_lock = resource
        with resource.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.stage()
            self.assertFalse(self.target.exists())
        record = self.stage()
        self.assertEqual(record["resource_lock_path"], str(resource))
        self.assertEqual((self.target / "suite.lock").stat().st_ino, resource.stat().st_ino)
        self.assertNotEqual(resource.stat().st_ino, (self.native / "suite.lock").stat().st_ino)

    def test_fresh_output_replay_and_native_root_symlink_are_refused(self):
        self.stage()
        before = snapshot(self.target)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(snapshot(self.target), before)
        alias = self.parent / "native-alias"
        alias.symlink_to(self.native, target_is_directory=True)
        self.args.builds_only_native_root = alias
        self.args.root = self.parent / "another-batch"
        with self.assertRaises(ValueError):
            self.stage()

    def test_midstage_source_change_rolls_back_without_synthesizing_evidence(self):
        original_copy = bootstrap.shutil.copy2
        changed = False
        def inject(source, target, *args, **kwargs):
            nonlocal changed
            result = original_copy(source, target, *args, **kwargs)
            if not changed and Path(source) == self.bundle / "datasystem/runtime.py":
                changed = True
                (self.native / "datasystem/source.txt").write_text("concurrent fixture mutation")
            return result
        with mock.patch.object(bootstrap.shutil, "copy2", side_effect=inject), self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.parent.glob(".batch-*")), [])

    def test_cli_routes_builds_only_and_keeps_no_legacy_requirement(self):
        with mock.patch.object(batch_stage, "drain"), contextlib.redirect_stdout(io.StringIO()) as stream:
            result = bootstrap.main(["--builds-only-native-root", str(self.native), "--root", str(self.target),
                                     "--bundle", str(self.bundle)])
        self.assertEqual(result, 0)
        self.assertFalse(json.loads(stream.getvalue())["legacy_suites_required"])


if __name__ == "__main__":
    unittest.main()
