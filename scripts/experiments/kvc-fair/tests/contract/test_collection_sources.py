"""Mixed-origin frozen evidence remains independently verifiable after source removal."""
from pathlib import Path
import shutil
import tempfile
import unittest

from kvcbench.collect.index import freeze_sources, verify, validate_members
from kvcbench.platform.files import read_json


class CollectionSourceTests(unittest.TestCase):
    def test_source_package_module_names_are_not_confused_with_derived_outputs(self):
        for prefix in ('', 'collector-source/', 'native-build/', 'runtime-inputs/', 'source-bundle/payload/'):
            validate_members([{'path': prefix + 'kvcbench/report/render.py', 'role': 'runtime'}])
        validate_members([{'path': 'collector-source/tests/unit/delivery/__init__.py', 'role': 'runtime'}])
        for path in ('report/index.html', 'results/report/index.html', 'results/kvcbench/report/render.py', 'kvcbench/__pycache__/cache.pyc'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_members([{'path': path, 'role': 'runtime'}])

    def test_disabled_storage_empty_directory_is_preserved_and_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            source = parent / 'source'
            (source / 'worker/rocksdb').mkdir(parents=True)
            (source / 'worker/rocksdb').chmod(0o750)
            (source / 'result.json').write_text('{}')
            row = {'source_root': 'runtime', 'source_path': 'result.json', 'path': 'result.json', 'role': 'runtime'}
            directory = {'source_root': 'runtime', 'source_path': 'worker/rocksdb', 'path': 'worker/rocksdb', 'role': 'runtime'}
            out = parent / 'snapshot'
            freeze_sources([row], out, run_id='fixture', profile='native-batch-tcp', roots={'runtime': source}, directories=[directory])
            shutil.rmtree(source)
            self.assertTrue(verify(out)['valid'])
            (out / 'payload/worker/rocksdb/unexpected.log').write_text('unexpected')
            with self.assertRaises(ValueError):
                verify(out)

    def test_original_origins_are_preserved_without_needing_live_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            roots = {name: parent / name for name in ('runtime', 'controller')}
            for root in roots.values():
                root.mkdir()
            (roots['runtime'] / 'case.json').write_text('{"fixture":"closed raw case"}')
            (roots['controller'] / 'workflow.json').write_text('{"fixture":"immutable metadata"}')
            members = [
                {'source_root': 'runtime', 'source_path': 'case.json', 'path': 'results/case.json', 'role': 'measurement'},
                {'source_root': 'controller', 'source_path': 'workflow.json', 'path': 'controller/workflow/workflow.json', 'role': 'runtime'}]
            out = parent / 'snapshot'
            self.assertTrue(freeze_sources(members, out, run_id='fixture', profile='native-batch-tcp', roots=roots)['valid'])
            original = (out / 'evidence-index.json').read_bytes()
            for root in roots.values():
                shutil.rmtree(root)
            self.assertTrue(verify(out)['valid'])
            self.assertEqual((out / 'evidence-index.json').read_bytes(), original)
            self.assertEqual(read_json(out / 'evidence-index.json')['schema_version'], 2)
            (out / 'payload/results/case.json').write_text('tampered')
            with self.assertRaises(ValueError):
                verify(out)

    def test_file_directory_collision_is_rejected_before_copy(self):
        with self.assertRaises(ValueError):
            validate_members([{'path': 'results/a', 'role': 'measurement'},
                              {'path': 'results/a/case.json', 'role': 'measurement'}])

    def test_mutable_workflow_index_cannot_become_frozen_source(self):
        with self.assertRaises(ValueError):
            validate_members([{'path': 'controller/workflow/workflow-status.json', 'role': 'runtime'}])


if __name__ == '__main__':
    unittest.main()
