"""Offline report admission rejects mixed inputs and preserves original evidence on relocation."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from kvcbench.platform.files import atomic_json, reference
from kvcbench.report.inputs import load
from kvcbench.report.render import render
from kvcbench.report.relocate import materialize


class ReportInputsTests(unittest.TestCase):
    def test_empty_storage_directory_survives_report_and_offline_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            source, path, spec = self.fixture(parent)
            evidence = json.loads((source / 'evidence.json').read_text())
            evidence.append({'destination': 'raw/worker/rocksdb', 'directory_mode': 0o750})
            atomic_json(source / 'evidence.json', evidence)
            spec['inputs']['evidence'] = reference(source, 'evidence.json')
            audit = json.loads((source / 'audit.json').read_text())
            audit['bindings']['evidence'] = spec['inputs']['evidence']['sha256']
            atomic_json(source / 'audit.json', audit)
            spec['inputs']['audit'] = reference(source, 'audit.json')
            atomic_json(path, spec)
            render(path, parent / 'report', diagnostic=True)
            directory = parent / 'report/raw/worker/rocksdb'
            self.assertTrue(directory.is_dir())
            self.assertEqual(directory.stat().st_mode & 0o777, 0o750)
            with zipfile.ZipFile(parent / 'report/reproduce.zip') as archive:
                entry = archive.getinfo('kvc-fair/report/raw/worker/rocksdb/')
                self.assertTrue(entry.is_dir())
                self.assertEqual(entry.external_attr >> 16 & 0o777, 0o750)

    def fixture(self, parent):
        source = parent / 'inputs'
        source.mkdir()
        profile, run_id = 'native-batch-tcp', 'synthetic-report-fixture'
        common = {'run_id': run_id, 'profile': profile}
        analysis = {**common, 'passed': False, 'rows': [], 'samples': [], 'aggregates': [],
                    'counts': {}, 'summary': {}, 'repeats': 3}
        atomic_json(source / 'case.json', {'synthetic': True})
        analysis['rows'] = [{'identity': 'synthetic-case', 'valid': False, 'status': 'invalid', 'evidence': 'results/case.json'}]
        values = {'analysis': analysis, 'series': [],
                  'builds': [{'product': name, 'tag': 'synthetic', 'sha': '0' * 40, 'build_exit': 1}
                             for name in ('datasystem', 'mooncake', 'lmcache')],
                  'workflow': {**common, 'steps': []}, 'evidence': [{'destination': 'results/case.json', 'source': reference(source, 'case.json')}]}
        inputs = {}
        for name, value in values.items():
            path = source / (name + '.json')
            atomic_json(path, value)
            inputs[name] = reference(source, path.name)
        audit = {**common, 'evidence_verified': False, 'complete': False, 'native_passed': False,
                 'issues': ['synthetic layout fixture; not executed product evidence'],
                 'configuration': [], 'bindings': {name: item['sha256'] for name, item in inputs.items()}}
        atomic_json(source / 'audit.json', audit)
        inputs['audit'] = reference(source, 'audit.json')
        document = source / 'build.md'
        fence = chr(96) * 3
        document.write_text('# 1. Synthetic document\n\n' + fence + 'mermaid\nsequenceDiagram\n  participant A\n  participant B\n  A->>B: synthetic\n' + fence + '\n')
        bundle = source / 'source.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('kvc-fair/README.md', '# Synthetic source fixture, not product source\n')
        spec = {'schema_version': 1, **common, 'inputs': inputs, 'documents': [reference(source, 'build.md')],
                'source_bundle': reference(source, 'source.zip')}
        path = source / 'report-inputs.json'
        atomic_json(path, spec)
        return source, path, spec

    def test_unverified_fixture_is_rejected_before_normal_report_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            source, path, spec = self.fixture(Path(directory))
            out = source.parent / 'report'
            with self.assertRaisesRegex(ValueError, 'complete independently'):
                render(path, out)
            self.assertFalse(out.exists())

    def test_mixed_run_or_changed_analysis_binding_is_rejected_even_for_diagnostic(self):
        for kind in ('run', 'binding'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                source, path, spec = self.fixture(Path(directory))
                if kind == 'run':
                    row = json.loads((source / 'analysis.json').read_text())
                    row['run_id'] = 'other-run'
                    atomic_json(source / 'analysis.json', row)
                    spec['inputs']['analysis'] = reference(source, 'analysis.json')
                    atomic_json(path, spec)
                else:
                    row = json.loads((source / 'audit.json').read_text())
                    row['bindings']['analysis'] = 'f' * 64
                    atomic_json(source / 'audit.json', row)
                    spec['inputs']['audit'] = reference(source, 'audit.json')
                    atomic_json(path, spec)
                with self.assertRaises(ValueError):
                    load(path, diagnostic=True)

    def test_diagnostic_has_local_charts_docs_and_exact_portable_audit_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source, path, _ = self.fixture(parent)
            original_audit = (source / 'audit.json').read_bytes()
            result = render(path, parent / 'report', diagnostic=True)
            self.assertFalse(result['publication_eligible'])
            self.assertIn('诊断预览', (parent / 'report/index.html').read_text())
            self.assertIn('reproduce.zip?sha256=', (parent / 'report/index.html').read_text())
            self.assertIn('<figcaption>', (parent / 'report/docs/build.html').read_text())
            extracted = parent / 'extracted'
            with zipfile.ZipFile(parent / 'report/reproduce.zip') as archive:
                archive.extractall(extracted)
                for member in archive.infolist():
                    target = extracted / member.filename
                    if target.is_file():
                        target.chmod(member.external_attr >> 16 & 0o777)
            shutil.rmtree(source)
            root = extracted / 'kvc-fair'
            materialize(root, 'report-inputs.template.json', parent / 'relocated.json')
            loaded = load(parent / 'relocated.json', diagnostic=True)
            self.assertEqual(loaded['paths']['audit'].read_bytes(), original_audit)
            self.assertIn('report/', str(loaded['paths']['analysis']))
            self.assertTrue((root / 'report/assets/echarts.min.js').exists())
            self.assertTrue((root / 'report/docs/_reader/mermaid.min.js').exists())

    def test_complete_native_failure_evidence_can_render_without_becoming_native_success(self):
        from tests.unit.collect.test_batch_failures import CompleteFailureEvidenceTests
        from kvcbench.collect.analysis.batch import analyze
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source, path, spec = self.fixture(parent)
            raw_root = parent / 'raw-fixture'
            raw_root.mkdir()
            suite, _ = CompleteFailureEvidenceTests().fixture(raw_root)
            analysis = analyze(suite)
            analysis.update(run_id=spec['run_id'], profile=spec['profile'])
            samples = analysis.pop('samples')
            evidence = []
            for row in analysis['rows']:
                for key in ('evidence', 'outcome_evidence'):
                    if row.get(key):
                        original = suite / Path(*Path(row[key]).parts[1:])
                        evidence.append({'destination': row[key], 'source': reference(original.parent, original.name)})
            evidence = list({row['destination']: row for row in evidence}.values())
            builds = [{'product': name, 'tag': 'synthetic-unit-fixture', 'sha': '0' * 40,
                       'build_exit': 0, 'identity_valid': True} for name in ('datasystem', 'mooncake', 'lmcache')]
            for name, value in [('analysis', analysis), ('series', samples), ('evidence', evidence), ('builds', builds)]:
                atomic_json(source / (name + '.json'), value)
                spec['inputs'][name] = reference(source, name + '.json')
            audit = {'run_id': spec['run_id'], 'profile': spec['profile'], 'evidence_verified': True,
                     'complete': True, 'native_passed': False, 'issues': [],
                     'bindings': {name: row['sha256'] for name, row in spec['inputs'].items() if name != 'audit'}}
            atomic_json(source / 'audit.json', audit)
            spec['inputs']['audit'] = reference(source, 'audit.json')
            atomic_json(path, spec)
            loaded = load(path)
            self.assertIs(loaded['values']['analysis']['passed'], False)
            self.assertEqual(loaded['values']['analysis']['counts']['failed'], 1)

    def test_legacy_refresh_cannot_mutate_a_canonical_report(self):
        from types import SimpleNamespace
        from kvcbench.report import refresh, refresh_batch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'render-record.json').write_text('{}')
            for module in (refresh, refresh_batch):
                with self.subTest(module=module.__name__), self.assertRaisesRegex(ValueError, 'immutable'):
                    module.refresh(SimpleNamespace(out=root))
            self.assertEqual((root / 'render-record.json').read_text(), '{}')

    def test_archive_tamper_cannot_create_a_relocated_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            _, path, _ = self.fixture(parent)
            render(path, parent / 'report', diagnostic=True)
            extracted = parent / 'extracted'
            with zipfile.ZipFile(parent / 'report/reproduce.zip') as archive:
                archive.extractall(extracted)
                for member in archive.infolist():
                    target = extracted / member.filename
                    if target.is_file():
                        target.chmod(member.external_attr >> 16 & 0o777)
            root = extracted / 'kvc-fair'
            (root / 'report/provenance/analysis.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'bytes/mode differ'):
                materialize(root, 'report-inputs.template.json', parent / 'relocated.json')
            self.assertFalse((parent / 'relocated.json').exists())


if __name__ == '__main__':
    unittest.main()
