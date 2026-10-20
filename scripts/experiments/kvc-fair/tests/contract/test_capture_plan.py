"""Closed collection planning retains storage failures and refuses uncommitted steps."""
from pathlib import Path
import tempfile
import unittest

from kvcbench.collect.capture import members, latest_commit
from kvcbench.collect.index import freeze_sources
from kvcbench.platform.files import identity
from kvcbench.platform.receipts import Workflow
from kvcbench.pipeline.steps import perform
from kvcbench.core.steps import Check


class CapturePlanTests(unittest.TestCase):
    def test_selected_sources_keep_rocksdb_artifacts_and_exclude_coordinator_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            roots = {name: parent / name for name in ('runtime', 'control', 'native', 'bundle')}
            for root in roots.values():
                root.mkdir()
            def write(root, path, data=b'fixture'):
                target = roots[root] / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                return target
            write('runtime', 'batch-stage.json')
            frozen = write('runtime', 'code.py')
            for phase in ('batch-gate-001', 'batch-smoke-001', 'formal-batch-001'):
                write('runtime', phase + '/summary.json')
            write('runtime', 'run/gate-r0-datasystem/etcd-data/member/wal/fixture')
            write('runtime', 'run/gate-r0-datasystem/w0/rocksdb/000001.log')
            write('control', 'workflow/workflow.json')
            for prefix in ('inputs', 'configs', 'kvcbench', 'scripts', 'commands', 'compiler-inputs', 'build-metadata', 'revalidation'):
                write('native', prefix + '/fixture.json')
            write('native', 'workflow/workflow.json')
            for name in ('build-result.json', 'build-cleanup.json', 'build-resources.json',
                         'datasystem/build/CMakeCache.txt', 'datasystem/build/_deps/iconv-src/config.log'):
                write('native', name)
            artifact = write('native', 'datasystem/output/sdk.whl')
            for name in ('manifest.json', 'archive.json', 'bundle.zip', 'payload/code.py'):
                write('bundle', name)
            outside = parent / 'outside.log'
            outside.write_text('must not be dereferenced')
            (roots['native'] / 'commands/link.log').symlink_to(outside)
            stage = {'inputs': {'code.py': identity(frozen)}, 'build_artifacts': {str(artifact): identity(artifact)}}
            selected, sources, skipped = members(roots['runtime'], roots['control'], roots['native'], roots['bundle'], stage)
            names = {row['path'] for row in selected}
            self.assertIn('run/gate-r0-datasystem/w0/rocksdb/000001.log', names)
            self.assertFalse(any('etcd-data' in name for name in names))
            self.assertTrue(any(row['path'] == 'commands/link.log' for row in skipped))
            result = freeze_sources(selected, parent / 'snapshot', run_id='fixture', profile='native-batch-tcp', roots=sources)
            self.assertTrue(result['valid'])

    def test_newer_partial_step_is_not_hidden_by_old_accepted_step(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'spec.json').write_text('{}')
            workflow = Workflow.create(root / 'workflow', 'fixture', 'native-batch-tcp', root / 'spec.json')
            perform(workflow, 'W10', action=lambda: ([Check('fixture', True, True, True)], [], {}),
                    scope='fixture receipt only', sources=[Path(__file__)])
            self.assertTrue(latest_commit(workflow.root, 'W10').is_file())
            workflow.begin('W10')
            with self.assertRaisesRegex(ValueError, 'committed terminal'):
                latest_commit(workflow.root, 'W10')


if __name__ == '__main__':
    unittest.main()
