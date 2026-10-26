import json
from pathlib import Path
import tempfile
import unittest
from kvclab.artifacts import manifest
from kvclab.remote import artifact
from kvclab.service import LabService
from kvclab.spec import preview_plan


class EvidenceTests(unittest.TestCase):
    def test_resources_and_commands_are_bound_and_downloadable(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / 'jobs/job1'
            job.mkdir(parents=True)
            for name in ['resources.jsonl', 'observe-iostat.log', 'commands.json', 'probe.bt']:
                (job / name).write_text('private evidence')
            (job / 'secret.key').write_text('not indexed')
            (job / 'link.log').symlink_to(job / 'secret.key')
            rows = manifest(job)
            self.assertEqual({r['name'] for r in rows}, {'resources.jsonl', '_evidence/observe-iostat.log', 'commands.json', 'probe.bt'})
            (job / 'complete.json').write_text(json.dumps({'artifacts': rows}))
            result = artifact({'state_root': directory}, 'job1', 'resources.jsonl')
            self.assertEqual(result['sha256'], next(r['sha256'] for r in rows if r['name'] == 'resources.jsonl'))
            names = LabService.remote_artifacts({'completion': {'artifacts': rows}})
            self.assertIn('_evidence/observe-iostat.log', names.values())

    def test_active_service_logs_have_immutable_download_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            original = job / 'coordinator.log'
            original.write_text('original')
            rows = manifest(job)
            original.write_text('later append')
            snapshot = job / rows[0]['name']
            self.assertEqual(snapshot.read_text(), 'original')
            self.assertNotEqual(snapshot, original)

    def test_service_exposes_missing_rows_for_failed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            service = LabService({'state_root': directory, 'csrf_secret': 'fixture' * 8})
            plan = preview_plan({})
            selected = service.store.submit('fixture', 'run', plan['config'], plan, 'fixture-001')
            service.store.finish(selected, 'failed', 'interrupted')
            job = service.get_job('fixture', selected)
            self.assertEqual(job['coverage']['missing_count'], 8)
            self.assertEqual(len(job['results']), 8)
            self.assertTrue(all(r['status'] == 'missing' for r in job['results']))
            self.assertIn('created_at', job)

    def test_source_download_whitelist_and_historical_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            service = LabService({'state_root': directory, 'csrf_secret': 'fixture' * 8, 'source_sha256': 'a' * 64})
            root = Path(directory) / 'sources'
            root.mkdir()
            (root / ('a' * 64 + '.tar.gz')).write_bytes(b'source')
            self.assertEqual(service.get_source('source.tar.gz')[0].read_bytes(), b'source')
            for name in ['../source.tar.gz', 'private.json']:
                with self.assertRaises(FileNotFoundError):
                    service.get_source(name)
