"""Publication rejects modified summaries and mismatched frozen report files."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from validation import build_study
from test_study_audit import AUDIT, fixture


class StudyPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_directory = tempfile.TemporaryDirectory()
        _, artifacts, evidence = fixture(Path(cls.fixture_directory.name))
        accepted = AUDIT.Auditor('fixture', evidence, artifacts).run()
        if not accepted['passed']:
            raise AssertionError(accepted['issues'])
        runs = []
        for index, name in enumerate(sorted(build_study.REQUIRED)):
            run = copy.deepcopy(accepted)
            run.update(name=name, job_id=format(index + 1, '032x'))
            run['rows'] = [{**copy.deepcopy(accepted['rows'][0]), **expected,
                            'source_sha256': build_study.SOURCE, 'build_sha': build_study.BUILDS[expected['product']]}
                           for expected in build_study.contract(name).values()]
            run['coverage'].update(expected=len(run['rows']), actual_remote_completion_rows=len(run['rows']))
            runs.append(run)
        cls.original = {'status': 'passed', 'scope': {'physical_hosts': 1},
                        'runs': runs, 'summaries': AUDIT.summarize(runs)}

    @classmethod
    def tearDownClass(cls):
        cls.fixture_directory.cleanup()

    def package(self, root, audit=None):
        source = root / 'input.json'
        source.write_text(json.dumps(self.original if audit is None else audit))
        output = root / 'report'
        build_study.package(source, output)
        return output

    def rehash(self, directory, name, value):
        path = directory / name
        path.write_text(json.dumps(value))
        manifest = json.loads((directory / 'study-manifest.json').read_text())
        manifest['files'][name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size}
        (directory / 'study-manifest.json').write_text(json.dumps(manifest))

    def test_accepted_original_is_copied_and_binds_its_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.package(root)
            self.assertEqual(build_study.validate_directory(output), output)
            self.assertEqual((output / 'audit.json').read_bytes(), (root / 'input.json').read_bytes())

    def test_empty_failed_duplicate_or_missing_runs_are_rejected(self):
        for mutation in [lambda value: value.update(runs=[]),
                         lambda value: value['runs'][0].update(passed=False),
                         lambda value: value['runs'][0].update(job_id=value['runs'][1]['job_id']),
                         lambda value: value['runs'].pop()]:
            value = copy.deepcopy(self.original)
            mutation(value)
            with self.assertRaises(ValueError):
                build_study.validate(value)

    def test_unaccepted_original_row_is_rejected(self):
        value = copy.deepcopy(self.original)
        value['runs'][0]['rows'][0]['audit_passed'] = False
        with self.assertRaises(ValueError):
            build_study.validate(value)

    def test_a_passed_smaller_probe_cannot_replace_a_named_study(self):
        value = copy.deepcopy(self.original)
        run = value['runs'][0]
        run['rows'] = run['rows'][:1]
        run['coverage'].update(expected=1, actual_remote_completion_rows=1)
        value['summaries'] = AUDIT.summarize(value['runs'])
        with self.assertRaisesRegex(ValueError, 'study contract'):
            build_study.validate(value)

    def test_short_read_probe_cannot_replace_formal_measurement_windows(self):
        value = copy.deepcopy(self.original)
        row = next(row for row in value['runs'][0]['rows'] if row['operation'] != 'put')
        row['duration_s'] = 5
        value['summaries'] = AUDIT.summarize(value['runs'])
        with self.assertRaisesRegex(ValueError, 'study window'):
            build_study.validate(value)

    def test_bounded_fresh_put_retains_its_actual_short_window(self):
        value = copy.deepcopy(self.original)
        row = next(row for row in value['runs'][0]['rows'] if row['operation'] == 'put')
        row.update(duration_s=.5, short_window=True)
        value['summaries'] = AUDIT.summarize(value['runs'])
        self.assertTrue(build_study.validate(value))

    def test_summary_values_and_repeat_identities_must_match_original_rows(self):
        for mutation in [lambda value: value['summaries'][0]['keys_s'].update(median=123456789),
                         lambda value: value['summaries'][0]['repeat_identities'][0].update(job_id='f' * 32)]:
            value = copy.deepcopy(self.original)
            mutation(value)
            with self.assertRaises(ValueError):
                build_study.validate(value)

    def test_missing_added_symlinked_or_modified_report_files_are_rejected(self):
        for mutation in [lambda output: (output / 'audit.json').unlink(),
                         lambda output: (output / 'extra.json').write_text('{}'),
                         lambda output: (output / 'study.json').write_text('{}'),
                         lambda output: (output / 'extra.json').symlink_to(output / 'audit.json')]:
            with tempfile.TemporaryDirectory() as directory:
                output = self.package(Path(directory))
                mutation(output)
                with self.assertRaises(ValueError):
                    build_study.validate_directory(output)

    def test_rehashed_display_copy_cannot_change_its_bound_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.package(Path(directory))
            study = json.loads((output / 'study.json').read_text())
            study['scope']['physical_hosts'] = 2
            self.rehash(output, 'study.json', study)
            with self.assertRaises(ValueError):
                build_study.validate_directory(output)

    def test_observability_display_and_frozen_file_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, observed = root / 'input.json', root / 'io.json'
            source.write_text(json.dumps(self.original))
            observed.write_text(json.dumps({'text': 'sample-interior delta', 'performance_audit_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'binding_passed': True, 'status': 'passed', 'runs': [dict(name=run['name'], job_id=run['job_id'],
                known_windows=run['coverage']['expected'], unknown_windows=0, read_bytes=0, write_bytes=0) for run in self.original['runs']]}))
            output = root / 'report'
            build_study.package(source, output, observability_path=observed)
            build_study.validate_directory(output)
            self.rehash(output, 'observability.json', {'text': 'changed'})
            with self.assertRaises(ValueError):
                build_study.validate_directory(output)

    def test_stale_io_overview_and_unknown_as_zero_are_rejected_before_output(self):
        for mutation in [lambda observed: observed.update(performance_audit_sha256='f' * 64),
                         lambda observed: observed['runs'][0].update(job_id='f' * 32),
                         lambda observed: observed['runs'][0].update(known_windows=0, unknown_windows=observed['runs'][0]['known_windows'])]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, observed_path = root / 'input.json', root / 'io.json'
                source.write_text(json.dumps(self.original))
                observed = {'performance_audit_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                    'binding_passed': True, 'status': 'passed', 'runs': [dict(name=run['name'], job_id=run['job_id'],
                    known_windows=run['coverage']['expected'], unknown_windows=0, read_bytes=0, write_bytes=0) for run in self.original['runs']]}
                mutation(observed)
                observed_path.write_text(json.dumps(observed))
                with self.assertRaises(ValueError):
                    build_study.package(source, root / 'out', observability_path=observed_path)
                self.assertFalse((root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
