"""Build explanations bind the actual recipe and retain original validation failures."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from kvcbench.collect.documents import build_details
from kvcbench.platform.files import atomic_json
from kvcbench.report.documents import build as render_documents


class BuildDocumentTests(unittest.TestCase):
    def fixture(self, root):
        native = root / 'native-build'
        rows = []
        for product in ('datasystem', 'mooncake', 'lmcache'):
            recipe = native / product / 'build.sh'
            recipe.parent.mkdir(parents=True)
            recipe.write_text('#!/bin/sh\ncmake -DUSE_CUDA=OFF -DBUILD_UNIT_TESTS=OFF .\n')
            rows.append({'product': product, 'tag': 'fixture-tag', 'build_recipe': product + '/build.sh',
                         'build_recipe_sha256': hashlib.sha256(recipe.read_bytes()).hexdigest(), 'compiler': 'fixture GCC'})
            commands = native / 'commands' / product
            commands.mkdir(parents=True)
            atomic_json(commands / 'build-command.json', {'cwd': '/original build',
                'argv': ['bash', '/original build/' + product + '/build.sh'], 'returncode': 0, 'duration_s': 1.25})
            atomic_json(commands / 'metadata-command.json', {'returncode': 1 if product == 'datasystem' else 0})
        return rows

    def test_frozen_recipe_is_readable_html_with_real_exit_codes_and_original_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = self.fixture(root)
            document = root / 'build.md'
            document.write_text('# 1. Build\n\n' + '\n'.join(build_details(root, rows)))
            render_documents([document], root / 'html', run_id='synthetic-build-document')
            html = (root / 'html/build.html').read_text()
            self.assertEqual(html.count('<details>'), 3)
            self.assertIn('原始 metadata 验收退出码 <code>1</code>', html)
            self.assertIn('-DUSE_CUDA=OFF', html)
            self.assertIn('原始 cwd: /original build', html)
            for row in rows:
                self.assertIn(row['build_recipe_sha256'], html)
                self.assertIn('native-build/' + row['build_recipe'], html)

    def test_changed_recipe_cannot_be_explained_as_the_accepted_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = self.fixture(root)
            (root / 'native-build/datasystem/build.sh').write_text('different recipe')
            with self.assertRaisesRegex(ValueError, 'differs from accepted'):
                build_details(root, rows)
