#!/usr/bin/env python3
"""Offline transaction fixtures for TCP diagnostic preflight corrections."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_tcp_gate_retry as retry


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kvc-tcp-retry-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "root"
        self.bundle = self.directory / "bundle"
        self.suite = self.root / "formal-tcp-001"
        self.protocol = self.root / "protocol-gate-001"
        self.cgroup = self.directory / "cg"
        self.bundle.mkdir()
        self.suite.mkdir(parents=True)
        (self.root / "suite.lock").write_text("")
        for role in ["", "server", "client", "monitor"]:
            group = self.cgroup / role
            group.mkdir(parents=True, exist_ok=True)
            (group / "cgroup.procs").write_text("")
        self.core = ["benchmark.py", "cgroup.py", "products.json", "configs/datasystem.json",
                     "configs/mooncake.json", "configs/lmcache.json", "scripts/datasystem/adapter_datasystem.py",
                     "mooncake/adapter.py", "lmcache/adapter_lmcache_tcp.py"]
        inputs = {}
        for name in sorted(set(self.core) | retry.ALLOWED):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# original fixture " + name + "\n")
            inputs[name] = {"sha256": checksum(path), "bytes": path.stat().st_size}
        for name in retry.ALLOWED:
            source = self.bundle / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("# corrected diagnostic fixture " + name + "\n")
            source.chmod(0o644)
        (self.root / "mooncake/launch_master.sh").chmod(0o755)
        stage = {"profile": "tcp", "root": str(self.root), "inputs": inputs}
        encoded = json.dumps(stage, indent=2) + "\n"
        (self.root / "tcp-stage.json").write_text(encoded)
        (self.suite / "tcp-stage.json").write_text(encoded)
        self.make_failed_gate("first")
        self.args = argparse.Namespace(root=self.root, bundle=self.bundle, cgroup=self.cgroup)
        self.original_core = self.snapshot([*self.core, "tcp-stage.json", "formal-tcp-001/tcp-stage.json"])
        self.original_helpers = self.snapshot(retry.ALLOWED)
        self.original_modes = {name: (self.root / name).stat().st_mode & 0o777 for name in retry.ALLOWED}

    def snapshot(self, names):
        return {name: (self.root / name).read_bytes() for name in names}

    def make_failed_gate(self, label):
        self.protocol.mkdir()
        (self.protocol / "datasystem").mkdir()
        (self.protocol / "summary.json").write_text(json.dumps({"end_wall_ns": 123, "valid": False, "fatal": label}))
        (self.protocol / "datasystem/probe.log").write_bytes(b"fixture raw data: " + label.encode() + b"\x00\xff\n")
        (self.protocol / "operations.jsonl").write_text('{"error":"fixture failed gate"}\n')

    def assert_core_unchanged(self):
        self.assertEqual(self.snapshot(self.original_core), self.original_core)

    def assert_old_state(self):
        self.assert_core_unchanged()
        self.assertEqual(self.snapshot(retry.ALLOWED), self.original_helpers)
        self.assertEqual({name: (self.root / name).stat().st_mode & 0o777 for name in retry.ALLOWED}, self.original_modes)
        self.assertTrue((self.protocol / "summary.json").is_file())
        self.assertFalse((self.suite / "preflight-changes.json").exists())

    def assert_audit_consistent(self):
        audit = json.loads((self.suite / "preflight-changes.json").read_text())
        expected = {name: row["sha256"] for name, row in json.loads((self.suite / "tcp-stage.json").read_text())["inputs"].items()}
        final_modes = {}
        for revision in audit["revisions"]:
            archived = self.suite / revision["protocol_archive"]
            self.assertTrue(archived.is_dir())
            actual_names = {str(path.relative_to(archived)) for path in archived.rglob("*") if path.is_file()}
            self.assertEqual(actual_names, set(revision["protocol_files"]))
            for name, row in revision["protocol_files"].items():
                self.assertEqual(checksum(archived / name), row["sha256"])
                self.assertEqual((archived / name).stat().st_size, row["bytes"])
            for change in revision["changes"]:
                name = change["relative_path"]
                self.assertIn(name, retry.ALLOWED)
                self.assertEqual(expected[name], change["before_sha256"])
                for kind in ["before", "after"]:
                    self.assertEqual(checksum(self.suite / change[kind + "_file"]), change[kind + "_sha256"])
                expected[name] = change["after_sha256"]
            for permission in revision.get("deployment_permissions", []):
                name = permission["relative_path"]
                self.assertEqual(name, "mooncake/launch_master.sh")
                self.assertEqual(permission["sha256"], expected[name])
                self.assertEqual(permission["after_mode"], permission["before_mode"] | 0o111)
                final_modes[name] = permission["after_mode"]
        for name, expected_hash in expected.items():
            self.assertEqual(checksum(self.root / name), expected_hash)
        for name, mode in final_modes.items():
            self.assertEqual((self.root / name).stat().st_mode & 0o777, mode)
        self.assert_core_unchanged()
        return audit

    def test_complete_archive_sources_hash_chain_and_core_preserved(self):
        original = {str(path.relative_to(self.protocol)): path.read_bytes() for path in self.protocol.rglob("*") if path.is_file()}
        result = retry.run(self.args)
        self.assertTrue(result["stage_unchanged"])
        self.assertEqual({row["relative_path"] for row in result["updated_helpers"]}, retry.ALLOWED)
        self.assertFalse(self.protocol.exists())
        archived = Path(result["protocol_preserved"])
        self.assertEqual({str(path.relative_to(archived)): path.read_bytes() for path in archived.rglob("*") if path.is_file()}, original)
        self.assertEqual(len(self.assert_audit_consistent()["revisions"]), 1)

    def test_second_failed_gate_retains_both_independent_revisions(self):
        retry.run(self.args)
        first = (self.suite / "protocol-history/attempt-0001/datasystem/probe.log").read_bytes()
        self.make_failed_gate("second")
        (self.bundle / "tcp_probe_ds_sizes.py").write_text("# revised diagnostic fixture 2\n")
        retry.run(self.args)
        audit = self.assert_audit_consistent()
        self.assertEqual(len(audit["revisions"]), 2)
        self.assertEqual((self.suite / "protocol-history/attempt-0001/datasystem/probe.log").read_bytes(), first)
        self.assertNotEqual(audit["revisions"][0]["protocol_archive"], audit["revisions"][1]["protocol_archive"])

    def test_shared_lock_blocks_retry_before_writes(self):
        with (self.root / "suite.lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                retry.run(self.args)
        self.assert_old_state()

    def test_formal_preparation_or_execution_markers_refuse_retry(self):
        for name in ["events.jsonl", "summary.json", "resume-provenance.json", "cells"]:
            path = self.suite / name
            path.write_text("fixture formal marker")
            with self.assertRaises(RuntimeError):
                retry.run(self.args)
            path.unlink()
            self.assert_old_state()

    def test_parent_and_descendant_cgroup_tasks_refuse_retry(self):
        drain = retry.drain
        child = self.cgroup / "client/child"
        child.mkdir()
        (child / "cgroup.procs").write_text("")
        for path in [self.cgroup / "cgroup.procs", child / "cgroup.procs"]:
            path.write_text("123\n")
            with patch.object(retry, "drain", side_effect=lambda root: drain(root, timeout=0)):
                with self.assertRaises(RuntimeError):
                    retry.run(self.args)
            path.write_text("")
            self.assert_old_state()

    def test_successful_or_nonterminal_gate_refused(self):
        for summary in [{"end_wall_ns": 123, "valid": True}, {"valid": False}]:
            (self.protocol / "summary.json").write_text(json.dumps(summary))
            with self.assertRaises(RuntimeError):
                retry.run(self.args)
            self.assert_old_state()

    def test_core_adapter_change_is_refused_before_any_updates(self):
        path = self.root / "lmcache/adapter_lmcache_tcp.py"
        path.write_text("# unapproved adapter change\n")
        before = self.snapshot([*self.core, *retry.ALLOWED])
        with self.assertRaises(RuntimeError):
            retry.run(self.args)
        self.assertEqual(self.snapshot(before), before)
        self.assertFalse((self.suite / "preflight-history").exists())
        self.assertTrue(self.protocol.is_dir())

    def test_protocol_symlink_is_refused(self):
        (self.protocol / "symlink").symlink_to(self.root / "benchmark.py")
        with self.assertRaises(ValueError):
            retry.run(self.args)
        self.assert_old_state()

    def test_missing_corrected_helper_keeps_original_state_and_can_retry(self):
        missing = self.bundle / sorted(retry.ALLOWED)[-1]
        content = missing.read_bytes()
        missing.unlink()
        with self.assertRaises(FileNotFoundError):
            retry.run(self.args)
        self.assert_old_state()
        missing.write_bytes(content)
        retry.run(self.args)
        self.assert_audit_consistent()

    def test_symlink_corrected_helper_is_refused(self):
        target = self.bundle / sorted(retry.ALLOWED)[0]
        target.unlink()
        target.symlink_to(self.root / "benchmark.py")
        with self.assertRaises(FileNotFoundError):
            retry.run(self.args)
        self.assert_old_state()

    def test_no_helper_changes_refused_without_touching_failed_gate(self):
        for name in retry.ALLOWED:
            (self.bundle / name).write_bytes((self.root / name).read_bytes())
        with self.assertRaises(RuntimeError):
            retry.run(self.args)
        self.assert_old_state()

    def identical_bundle(self):
        for name in retry.ALLOWED:
            (self.bundle / name).write_bytes((self.root / name).read_bytes())

    def test_changed_launcher_source_644_deploys_755_and_records_final_sha(self):
        self.identical_bundle()
        name = "mooncake/launch_master.sh"
        launcher = self.root / name
        source = self.bundle / name
        source.write_text("#!/bin/sh\n# corrected direct ELF launcher fixture\n")
        source.chmod(0o644)
        result = retry.run(self.args)
        self.assertEqual(launcher.read_bytes(), source.read_bytes())
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
        self.assertEqual({change["relative_path"] for change in result["updated_helpers"]}, {name})
        audit = self.assert_audit_consistent()
        permission = audit["revisions"][0]["deployment_permissions"][0]
        self.assertEqual(permission["before_mode"], 0o755)
        self.assertEqual(permission["after_mode"], 0o755)
        self.assertEqual(permission["sha256"], checksum(source))
        snapshot = self.suite / audit["revisions"][0]["changes"][0]["after_file"]
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o644)

    def permission_only(self, original_mode):
        self.identical_bundle()
        name = "mooncake/launch_master.sh"
        launcher = self.root / name
        launcher.chmod(original_mode)
        original_sha = checksum(launcher)
        result = retry.run(self.args)
        self.assertEqual(result["updated_helpers"], [])
        self.assertEqual(checksum(launcher), original_sha)
        self.assertEqual(launcher.stat().st_mode & 0o777, original_mode | 0o111)
        audit = self.assert_audit_consistent()
        revision = audit["revisions"][0]
        self.assertEqual(revision["changes"], [])
        self.assertEqual(revision["deployment_permissions"][0]["before_mode"], original_mode)
        self.assertEqual(revision["deployment_permissions"][0]["after_mode"], original_mode | 0o111)
        self.assertEqual(revision["deployment_permissions"][0]["sha256"], original_sha)

    def test_permissions_only_644_same_sha_allowed_and_deploys_755(self):
        self.permission_only(0o644)

    def test_permissions_only_750_same_sha_allowed_and_deploys_755(self):
        self.permission_only(0o750)

    def test_noop_mode_777_refused_without_touching_failed_gate(self):
        self.identical_bundle()
        name = "mooncake/launch_master.sh"
        (self.root / name).chmod(0o777)
        self.original_modes[name] = 0o777
        with self.assertRaises(RuntimeError):
            retry.run(self.args)
        self.assert_old_state()

    def test_permissions_only_failure_restores_original_mode_and_bytes(self):
        self.identical_bundle()
        name = "mooncake/launch_master.sh"
        (self.root / name).chmod(0o644)
        self.original_modes[name] = 0o644
        with patch.object(retry, "write_json", side_effect=OSError("fixture audit failure after chmod")):
            with self.assertRaises(OSError):
                retry.run(self.args)
        self.assert_old_state()
        retry.run(self.args)
        self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o755)
        self.assert_audit_consistent()

    def test_launcher_content_and_permissions_rollback_preserves_original_mode(self):
        name = "mooncake/launch_master.sh"
        launcher = self.root / name
        launcher.chmod(0o750)
        self.original_modes[name] = 0o750
        with patch.object(retry, "write_json", side_effect=OSError("fixture audit failure after install and chmod")):
            with self.assertRaises(OSError):
                retry.run(self.args)
        self.assert_old_state()
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o750)

    def test_existing_snapshot_tamper_refuses_next_revision(self):
        retry.run(self.args)
        self.make_failed_gate("second")
        (self.suite / "preflight-history/attempt-0001/before/tcp_stage.py").write_text("changed snapshot")
        before = self.snapshot(retry.ALLOWED)
        with self.assertRaises(RuntimeError):
            retry.run(self.args)
        self.assertEqual(before, self.snapshot(retry.ALLOWED))
        self.assertTrue(self.protocol.exists())

    def test_existing_protocol_archive_tamper_refuses_next_revision(self):
        retry.run(self.args)
        self.make_failed_gate("second")
        (self.bundle / "tcp_stage.py").write_text("# revised diagnostic fixture 2\n")
        (self.suite / "protocol-history/attempt-0001/datasystem/probe.log").write_text("changed archived gate log")
        before = self.snapshot(retry.ALLOWED)
        with self.assertRaises(RuntimeError):
            retry.run(self.args)
        self.assertEqual(before, self.snapshot(retry.ALLOWED))
        self.assertTrue(self.protocol.exists())

    def test_install_error_rolls_back_and_a_fresh_retry_can_succeed(self):
        replace = retry.os.replace
        failure_name = sorted(retry.ALLOWED)[1]
        def fail_replace(source, destination):
            if str(source).endswith(".preflight-pending") and Path(destination).name == failure_name:
                raise OSError("fixture install failure")
            return replace(source, destination)
        with patch.object(retry.os, "replace", side_effect=fail_replace):
            with self.assertRaises(OSError):
                retry.run(self.args)
        self.assert_old_state()
        retry.run(self.args)
        self.assert_audit_consistent()

    def test_audit_failure_before_commit_rolls_back_and_can_retry(self):
        with patch.object(retry, "write_json", side_effect=OSError("fixture audit write failure")):
            with self.assertRaises(OSError):
                retry.run(self.args)
        self.assert_old_state()
        retry.run(self.args)
        self.assert_audit_consistent()

    def test_interrupt_immediately_after_helper_replace_restores_that_helper(self):
        replace = retry.os.replace
        interrupted = False
        def replace_then_interrupt(source, destination):
            nonlocal interrupted
            result = replace(source, destination)
            if str(source).endswith(".preflight-pending") and not interrupted:
                interrupted = True
                raise KeyboardInterrupt("fixture interrupt after helper replacement")
            return result
        with patch.object(retry.os, "replace", side_effect=replace_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                retry.run(self.args)
        self.assert_old_state()
        retry.run(self.args)
        self.assert_audit_consistent()

    def test_exception_after_audit_commit_cannot_create_split_state(self):
        write_json = retry.write_json
        def commit_then_interrupt(path, value):
            write_json(path, value)
            raise KeyboardInterrupt("fixture interrupt just after commit")
        with patch.object(retry, "write_json", side_effect=commit_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                retry.run(self.args)
        if (self.suite / "preflight-changes.json").exists():
            self.assert_audit_consistent()
            self.assertFalse(self.protocol.exists())
        else:
            self.assert_old_state()


if __name__ == "__main__":
    unittest.main()
