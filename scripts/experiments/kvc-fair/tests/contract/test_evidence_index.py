"""Frozen evidence is independently verifiable and excludes downstream artifacts."""
import json
from pathlib import Path
import tempfile
import unittest

from kvcbench.collect.index import freeze, verify, terminal_suite, validate_members


class EvidenceIndexTests(unittest.TestCase):
    def test_freeze_preserves_source_bytes_modes_and_rejects_later_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, out = root / 'source', root / 'snapshot'
            source.mkdir()
            original = source / 'result.json'
            original.write_text('{"valid":false}\n')
            original.chmod(0o640)
            before = original.read_bytes()
            result = freeze(source, [{'path': 'result.json', 'role': 'measurement'}], out,
                            run_id='case-001', profile='native-batch-tcp')
            self.assertTrue(result['valid'])
            target = out / 'payload/result.json'
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            target.write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                verify(out)
            self.assertEqual(original.read_bytes(), before)

    def test_unsafe_derived_duplicate_and_unknown_roles_rejected_before_mutation(self):
        for members in [[{'path': '../escape', 'role': 'build'}], [{'path': 'derived/a.json', 'role': 'build'}],
                        [{'path': 'a', 'role': 'report'}], [{'path': 'a', 'role': 'build'}] * 2]:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                out = root.parent / (root.name + '-snapshot')
                with self.assertRaises(ValueError):
                    freeze(root, members, out, run_id='case-001', profile='native-batch-tcp')
                self.assertFalse(out.exists())

    def test_selected_upstream_receipts_allowed_but_current_and_downstream_rejected(self):
        source = [{'path': 'workflow/steps/W09/attempt-000001/commit.json', 'role': 'measurement'}]
        self.assertEqual(validate_members(source), source)
        for name in ['workflow/steps/W12/attempt-000001/commit.json', 'workflow/workflow-status.json',
                     'workflow/steps/W15/attempt-000001/commit.json']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_members([{'path': name, 'role': 'measurement'}])

    def test_native_failure_can_be_terminal_but_unknown_cleanup_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = {'ended': 'fixture', 'valid': False, 'execution_complete': True, 'failed': [],
                   'monitor_exit': 0, 'interference_exit': 0,
                   'drained': {'members': {'/fixture/client/cgroup.procs': []}, 'populated': []}}
            path = root / 'summary.json'
            path.write_text(json.dumps(row))
            self.assertIs(terminal_suite(root)['valid'], False)
            row['drained']['members']['/fixture/client/cgroup.procs'] = ['123']
            path.write_text(json.dumps(row))
            with self.assertRaises(ValueError):
                terminal_suite(root)


if __name__ == '__main__':
    unittest.main()
