"""Organized source packages still deploy executable, complete frozen runtime entries."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from kvcbench.build.bundle import install_package
from kvcbench.run import layout
from tests.paths import TOOL_ROOT


class RuntimeLayoutTests(unittest.TestCase):
    def test_modular_source_materializes_executable_runtime_without_archived_controllers(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'runtime'
            selected = layout.materialize(TOOL_ROOT, target)
            install_package(TOOL_ROOT, target)
            for name, source in selected.items():
                self.assertEqual((target / name).read_bytes(), (TOOL_ROOT / source).read_bytes())
            for entry in [('batch_gate.py',), ('scripts/datasystem/runtime.py',),
                          ('-m', 'kvcbench')]:
                result = subprocess.run([sys.executable, '-B', *entry, '--help'], cwd=target,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((target / 'workflow.py').exists())
            self.assertFalse((target / 'tools').exists())

    def test_missing_or_symlinked_resource_refuses_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source'
            resource = source / 'kvcbench/run/entrypoints/batch_benchmark.py'
            resource.parent.mkdir(parents=True)
            with self.assertRaises(FileNotFoundError):
                layout.materialize(source, Path(temporary) / 'missing')
            resource.symlink_to(TOOL_ROOT / 'README.md')
            with self.assertRaises(ValueError):
                layout.materialize(source, Path(temporary) / 'symlink')

    def test_production_owners_do_not_import_archived_controllers_or_root_facades(self):
        owners = ['build', 'run', 'observe', 'collect', 'report', 'delivery', 'core', 'platform', 'pipeline', 'compat', 'diagnostics']
        root_facades = {'batch_analyze', 'batch_runner', 'batch_workflow', 'workflow', 'tcp_workflow',
                        'batch_stage', 'batch_build_stage', 'profile_put', 'tcp_protocol_gate'}
        violations = []
        for owner in owners:
            for path in (TOOL_ROOT / 'kvcbench' / owner).rglob('*.py'):
                if 'entrypoints' in path.parts:
                    continue
                for node in ast.walk(ast.parse(path.read_text())):
                    names = [node.module or ''] if isinstance(node, ast.ImportFrom) else (
                        [item.name for item in node.names] if isinstance(node, ast.Import) else [])
                    for name in names:
                        if name.startswith('tools.migrations') or name.split('.')[0] in root_facades:
                            violations.append((str(path.relative_to(TOOL_ROOT)), node.lineno, name))
        self.assertEqual(violations, [])
