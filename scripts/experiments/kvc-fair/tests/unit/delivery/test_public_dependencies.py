"""Public verification covers linked raw build and workflow files as well as charts."""
from pathlib import Path
import tempfile
import unittest

from kvcbench.delivery.public import dependencies
from kvcbench.platform.files import atomic_json


class PublicDependencyTests(unittest.TestCase):
    def fixture(self, root):
        for name, content in {'index.html': '<a href="docs/build.html">build</a>',
            'docs/build.html': '<a href="../raw/build%20recipe.sh">recipe</a>',
            'raw/build recipe.sh': 'frozen build', 'raw/step/result.json': '{}',
            'raw/not-linked.bin': 'not selected for public transfer'}.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        atomic_json(root / 'data.json', {'workflow': {'steps': [
            {'evidence': {'result.json': 'raw/step/result.json'}}]}})
        return {p.relative_to(root).as_posix(): '0' * 64 for p in root.rglob('*') if p.is_file()}

    def test_all_html_and_dynamic_step_targets_are_selected_for_actual_hash_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self.fixture(root)
            selected = dependencies(root, expected)
            self.assertIn('raw/build recipe.sh', selected)
            self.assertIn('raw/step/result.json', selected)
            self.assertNotIn('raw/not-linked.bin', selected)

    def test_escape_and_unmanifested_links_cannot_be_silently_skipped(self):
        for link in ('../../outside.json', '../raw/missing.json'):
            with self.subTest(link=link), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                expected = self.fixture(root)
                (root / 'docs/build.html').write_text('<a href="' + link + '">invalid</a>')
                with self.assertRaises(ValueError):
                    dependencies(root, expected)
