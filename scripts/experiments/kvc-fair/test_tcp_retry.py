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
            (self.bundle / name).write_text("# corrected diagnostic fixture " + name + "\n")
        stage = {"profile": "tcp", "root": str(self.root), "inputs": inputs}
        encoded = json.dumps(stage, indent=2) + "\n"
        (self.root / "tcp-stage.json").write_text(encoded)
        (self.suite / "tcp-stage.json").write_text(encoded)
        self.make_failed_gate("first")
        self.args = argparse.Namespace(root=self.root, bundle=self.bundle, cgroup=self.cgroup)
        self.original_core = self.snapshot([*self.core, "tcp-stage.json", "formal-tcp-001/tcp-stage.json"])
        self.original_helpers = self.snapshot(retry.ALLOWED)

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
        self.assertTrue((self.protocol / "summary.json").is_file())
        self.assertFalse((self.suite / "preflight-changes.json").exists())

    def assert_audit_consistent(self):
        audit = json.loads((self.suite / "preflight-changes.json").read_text())
        expected = {name: row["sha256"] for name, row in json.loads((self.suite / "tcp-stage.json").read_text())["inputs"].items()}
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
        for name, expected_hash in expected.items():
            self.assertEqual(checksum(self.root / name), expected_hash)
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
