"""Delivery acceptance verifies contents rather than trusting generated status flags."""
import gzip
from pathlib import Path
import tempfile
import unittest
import zipfile

from kvcbench.delivery.acceptance import manifest, links, extract
from kvcbench.platform.artifacts import finalize_artifacts


class DeliveryAcceptanceTests(unittest.TestCase):
    def test_actual_manifest_gzip_links_and_missing_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'docs').mkdir()
            (root / 'index.html').write_text('<a href="docs/build.html#compiler">build</a>')
            (root / 'docs/build.html').write_text('<h1 id="compiler">build</h1>')
            finalize_artifacts(root)
            self.assertTrue(manifest(root)['valid'])
            self.assertTrue(links(root)['valid'])
            (root / 'docs/build.html').write_text('<h1>missing anchor</h1>')
            with self.assertRaises(ValueError):
                manifest(root)
            with self.assertRaises(ValueError):
                links(root)

    def test_zip_escape_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(root / 'source.zip', 'w') as archive:
                archive.writestr('kvc-fair/../../outside', 'unsafe')
            with self.assertRaises(ValueError):
                extract(root / 'source.zip', root / 'out')
            self.assertFalse((root / 'out').exists())

    def test_clean_extract_restores_executable_and_empty_directory_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(root / 'source.zip', 'w') as archive:
                for name, mode, content in [('kvc-fair/run.sh', 0o100755, 'exit 0'),
                                            ('kvc-fair/worker/rocksdb/', 0o40750, '')]:
                    entry = zipfile.ZipInfo(name)
                    entry.create_system = 3
                    entry.external_attr = mode << 16
                    archive.writestr(entry, content)
            package = extract(root / 'source.zip', root / 'out')
            self.assertEqual((package / 'run.sh').stat().st_mode & 0o777, 0o755)
            self.assertEqual((package / 'worker/rocksdb').stat().st_mode & 0o777, 0o750)
            self.assertEqual(list((package / 'worker/rocksdb').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
