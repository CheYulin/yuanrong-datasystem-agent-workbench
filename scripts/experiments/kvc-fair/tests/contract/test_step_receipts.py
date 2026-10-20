"""Integrity and admission contracts using local immutable fixture artifacts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from kvcbench.core.steps import Check
from kvcbench.platform import receipts
from kvcbench.platform.files import atomic_json, digest, identity, read_json, reference
from kvcbench.platform.receipts import Workflow, verify_commit


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.spec = self.root / "spec.json"
        atomic_json(self.spec, {"fixture": True}, exclusive=True)
        self.workflow = Workflow.create(self.root / "workflow", "fixture-run", "fixture-v1", self.spec)
        self.payload = self.root / "payload.txt"
        self.payload.write_text("frozen fixture\n")

    def tearDown(self):
        self.temporary.cleanup()

    def finish(self, attempt, *, execution="completed", returncode=0, checks=None, artifacts=None, **kwargs):
        return attempt.finish(execution=execution, returncode=returncode, scope="offline fixture integrity",
            checks=[Check("domain_fixture", True, True, True)] if checks is None else checks,
            artifacts=[reference(self.root, "payload.txt")] if artifacts is None else artifacts,
            validator_sources=[Path(receipts.__file__)], validator_id="fixture-v1", **kwargs)

    def test_receipt_verifies_and_has_separate_execution_acceptance_and_native_outcomes(self):
        attempt = self.workflow.begin("W09", allowed_findings=["native_read_failure"])
        commit = self.finish(attempt, checks=[Check("native_read_failure", "all native calls succeeded", "native EXIST error", False, required=False)],
                             native_outcomes={"failed": 1, "valid": 0})
        record = verify_commit(commit)
        self.assertEqual(record["acceptance"], "passed_with_findings")
        self.assertEqual(read_json(commit.parent / "result.json")["native_outcomes"]["failed"], 1)
        self.assertTrue((commit.parent / "summary.md").is_file())

    def test_completed_return_code_alone_cannot_pass_without_domain_checks(self):
        attempt = self.workflow.begin("W03")
        with self.assertRaisesRegex(ValueError, "domain acceptance"):
            self.finish(attempt, checks=[])

    def test_nonzero_command_cannot_pass_by_omitting_exit_code_check(self):
        commit = self.finish(self.workflow.begin("W02"), returncode=7)
        self.assertEqual(read_json(commit)["acceptance"], "failed")
        self.assertEqual(verify_commit(commit)["acceptance"], "failed")

    def test_unapproved_optional_failure_does_not_pass_with_findings(self):
        commit = self.finish(self.workflow.begin("W08"),
            checks=[Check("required_monitor", True, False, False, required=False)])
        self.assertEqual(verify_commit(commit)["acceptance"], "failed")

    def test_required_failure_blocks_normal_downstream_but_recovery_can_consume_record(self):
        failed = self.finish(self.workflow.begin("W07"), execution="failed", returncode=1)
        blocked = self.workflow.begin("W09", upstream=[failed])
        with self.assertRaisesRegex(ValueError, "preconditions"):
            blocked.require_ready()
        recovery = self.workflow.begin("W10", upstream=[failed], purpose="recovery")
        recovery.require_ready()
        self.assertEqual(verify_commit(self.finish(recovery))["acceptance"], "passed")

    def test_upstream_from_another_profile_is_rejected_even_when_it_passed(self):
        other = Workflow.create(self.root / "other", "fixture-run", "other-profile", self.spec)
        parent = self.finish(other.begin("W01"))
        attempt = self.workflow.begin("W02", upstream=[parent])
        with self.assertRaisesRegex(ValueError, "another run/profile"):
            attempt.require_ready()

    def test_changed_payload_invalidates_already_accepted_receipt(self):
        commit = self.finish(self.workflow.begin("W03"))
        self.payload.write_text("changed after acceptance\n")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            verify_commit(commit)

    def test_chmod_alone_invalidates_receipt(self):
        commit = self.finish(self.workflow.begin("W03"))
        self.payload.chmod(0o711)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            verify_commit(commit)

    def test_input_changed_during_execution_never_produces_pass(self):
        source = self.root / "input.txt"
        source.write_text("old")
        attempt = self.workflow.begin("W13", inputs=[source])
        source.write_text("new")
        commit = self.finish(attempt)
        self.assertEqual(read_json(commit)["acceptance"], "failed")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            verify_commit(commit)

    def test_attempt_owned_payload_references_follow_final_atomic_directory(self):
        attempt = self.workflow.begin("W03")
        (attempt.path / "snapshot.txt").write_text("snapshot")
        artifact = reference(attempt.path, "snapshot.txt")
        commit = self.finish(attempt, artifacts=[artifact])
        self.assertEqual(verify_commit(commit)["acceptance"], "passed")

    def test_validator_source_is_a_frozen_snapshot(self):
        commit = self.finish(self.workflow.begin("W03"))
        record = read_json(commit.parent / "acceptance.json")["validator"]["sources"][0]
        self.assertEqual(Path(record["root"]), commit.parent)
        source = Path(record["root"]) / record["path"]
        source.write_text("changed validator snapshot")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            verify_commit(commit)

    def test_symlink_and_parent_traversal_are_rejected(self):
        (self.root / "link.txt").symlink_to(self.payload)
        for relative in ["link.txt", "../payload.txt", str(self.payload), "."]:
            with self.subTest(relative=relative), self.assertRaises((ValueError, OSError)):
                reference(self.root, relative)

    def test_failure_attempt_is_retained_and_new_attempt_does_not_overwrite(self):
        first = self.finish(self.workflow.begin("W03"), execution="failed", returncode=2)
        original = first.read_bytes()
        second = self.finish(self.workflow.begin("W03"))
        self.assertNotEqual(first.parent.name, second.parent.name)
        self.assertEqual(first.read_bytes(), original)

    def test_cannot_finish_a_completed_attempt_twice(self):
        attempt = self.workflow.begin("W03")
        self.finish(attempt)
        with self.assertRaisesRegex(ValueError, "once"):
            self.finish(attempt)

    def test_skipped_step_requires_reason_and_does_not_pass_normal_admission(self):
        attempt = self.workflow.begin("W17")
        with self.assertRaisesRegex(ValueError, "reason"):
            self.finish(attempt, execution="skipped", returncode=None)
        commit = self.finish(attempt, execution="skipped", returncode=None, reason="publication not selected")
        self.assertEqual(verify_commit(commit)["acceptance"], "skipped")

    def test_hard_exit_preserves_uncommitted_attempt_without_inventing_terminal_state(self):
        code = ("import os; from kvcbench.platform.receipts import Workflow; "
                f"w=Workflow({str(self.workflow.root)!r}); w.begin('W09'); os._exit(23)")
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(process.returncode, 23)
        status = self.workflow.status()["steps"][0]
        self.assertEqual(status["execution"], "uncommitted")
        self.assertEqual(status["liveness"], "unknown")
        self.assertEqual(status["acceptance"], "pending")

    def test_changed_summary_and_missing_commit_member_are_detected(self):
        commit = self.finish(self.workflow.begin("W03"))
        (commit.parent / "summary.md").write_text("unbound prose")
        with self.assertRaisesRegex(ValueError, "receipt changed"):
            verify_commit(commit)

    def test_atomic_json_does_not_replace_exclusive_record(self):
        original = self.spec.read_bytes()
        with self.assertRaises(FileExistsError):
            atomic_json(self.spec, {"changed": True}, exclusive=True)
        self.assertEqual(self.spec.read_bytes(), original)

    def test_step_directory_link_cannot_redirect_attempt_writes(self):
        external = self.root / "external"
        external.mkdir()
        (self.workflow.root / "steps").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.workflow.begin("W03")
        self.assertEqual(list(external.iterdir()), [])

    def test_inconsistent_duration_is_rejected_even_with_updated_file_checksums(self):
        commit = self.finish(self.workflow.begin("W03"))
        result_path = commit.parent / "result.json"
        result = read_json(result_path)
        result["duration_s"] += 1
        atomic_json(result_path, result)
        accepted_path = commit.parent / "acceptance.json"
        accepted = read_json(accepted_path)
        accepted["bindings"]["result.json"] = digest(result_path)
        atomic_json(accepted_path, accepted)
        record = read_json(commit)
        record["files"]["result.json"] = identity(result_path)
        record["files"]["acceptance.json"] = identity(accepted_path)
        atomic_json(commit, record)
        with self.assertRaisesRegex(ValueError, "duration/clock"):
            verify_commit(commit)

    def test_collected_artifacts_verify_with_explicit_root_mapping_without_rewriting_receipts(self):
        commit = self.finish(self.workflow.begin("W03"))
        with tempfile.TemporaryDirectory() as target:
            copied = Path(target) / 'evidence'
            shutil.copytree(self.root, copied)
            collected_commit = copied / commit.relative_to(self.root)
            original_bytes = collected_commit.read_bytes()
            self.payload.write_text('changed at original location')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                verify_commit(collected_commit)
            roots = {str(self.root): str(copied)}
            self.assertEqual(verify_commit(collected_commit, roots=roots)['acceptance'], 'passed')
            self.assertEqual(collected_commit.read_bytes(), original_bytes)
            with self.assertRaisesRegex(ValueError, 'read-only'):
                Workflow(copied / 'workflow', roots).begin('W09')
            (copied / 'payload.txt').write_text('changed collected bytes')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                verify_commit(collected_commit, roots=roots)

    def test_readonly_cli_writes_validator_identity_and_does_not_hide_invalid_payload(self):
        commit = self.finish(self.workflow.begin('W03'))
        output = self.root / 'check-001'
        command = [sys.executable, '-m', 'kvcbench', 'verify-step', '--commit', str(commit), '--out', str(output)]
        checked = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        record = read_json(output / 'verification.json')
        self.assertTrue(record['valid'])
        self.assertTrue(record['read_only_verification'])
        self.assertEqual(len(record['validator']['sources']), 5)
        self.payload.write_text('invalid after first CLI verification')
        command[-1] = str(self.root / 'check-002')
        rejected = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 1, rejected.stderr)
        self.assertFalse(json.loads(rejected.stdout)['valid'])


if __name__ == "__main__":
    unittest.main()
