"""The relocated independent mechanics entry works outside the source directory."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.paths import TOOL_ROOT


class HarnessCorrectnessTests(unittest.TestCase):
    def test_standalone_fake_entry_validates_calls_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / 'evidence'
            completed = subprocess.run([sys.executable, '-B',
                str(TOOL_ROOT / 'tests/integration/lmcache_harness_correctness.py'),
                '--kind', 'fake', '--output', str(out)], cwd=root,
                capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads((out / 'summary.json').read_text())
            self.assertTrue(result['passed'])
            self.assertFalse(result['rankable'])
            self.assertEqual({case['op'] for case in result['cases']},
                             {'put', 'get', 'exist_hit', 'exist_miss'})
            corrupt = json.loads((out / 'intentional-corruption.json').read_text())
            self.assertFalse(corrupt['valid'])
            self.assertIn('mismatch', corrupt['fatal'])
