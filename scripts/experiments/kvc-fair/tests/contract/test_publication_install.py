"""Fresh version installation preserves unrelated navigation and refuses bad uploads."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from kvcbench.delivery import install, publish
from kvcbench.delivery.registration import home_array
from kvcbench.platform.artifacts import finalize_artifacts


class PublicationInstallTests(unittest.TestCase):
    def fixture(self, root):
        source, site = root / 'upload', root / 'site'
        source.mkdir()
        (source / 'index.html').write_text('<html>synthetic publication fixture</html>')
        finalize_artifacts(source)
        (site / 'assets/js').mkdir(parents=True)
        original = {'p': 'existing/index.html', 't': 'unrelated fixture'}
        (site / 'index.html').write_text('<script>var P = ' + json.dumps([original]) + ';</script>')
        (site / 'assets/js/document-index-20261011.json').write_text(json.dumps({'existing/index.html': {'title': 'unrelated fixture'}}))
        return source, site, original

    def test_real_registration_preserves_existing_entries_and_gzip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, site, original = self.fixture(root)
            result = install.install(source, site, 'perf/synthetic-unit-fixture', title='synthetic fixture',
                date='2026-10-21', record=root / 'registration.json', publisher=Path(publish.__file__))
            _, _, pages = home_array((site / 'index.html').read_bytes())
            self.assertEqual(pages[1:], [original])
            self.assertEqual(pages[0]['d'], '2026-10-21')
            self.assertEqual(gzip.decompress((site / 'index.html.gz').read_bytes()), (site / 'index.html').read_bytes())
            self.assertFalse(source.exists())
            self.assertTrue((site / 'perf/synthetic-unit-fixture/index.html').is_file())
            self.assertEqual(result['installed_files'], 2)

    def test_bad_dependency_never_installs_or_registers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, site, _ = self.fixture(root)
            original = (site / 'index.html').read_bytes()
            (source / 'index.html').write_text('tampered')
            with self.assertRaises(ValueError):
                install.install(source, site, 'perf/synthetic-unit-fixture', title='fixture', date='2026-10-21',
                    record=root / 'registration.json', publisher=Path(publish.__file__))
            self.assertTrue(source.exists())
            self.assertFalse((site / 'perf/synthetic-unit-fixture').exists())
            self.assertEqual((site / 'index.html').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
