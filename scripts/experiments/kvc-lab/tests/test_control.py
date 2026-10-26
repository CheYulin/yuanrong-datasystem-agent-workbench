import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from kvclab import spec
from kvclab.controller import Controller
from kvclab.service import LabService
from kvclab.store import Conflict, Store

class Plans(unittest.TestCase):
    def test_profiles_have_real_bounded_variants(self):
        for preset in spec.catalog()['presets']:
            plan = spec.preview_plan(preset['config'])
            self.assertGreater(plan['expected_cases'], 0)
            self.assertLessEqual(plan['expected_cases'], 384)
            for variant in plan['variants']:
                self.assertEqual(variant['config']['server_cpus'], 8)
                spec.validate(variant['config'])
        workers = spec.preview_plan({'profile': 'worker_scaling'})
        self.assertEqual([v['config']['workers_per_node'] for v in workers['variants']], [1, 2, 4, 8])
        metadata = spec.preview_plan({'profile': 'metadata_scaling'})
        self.assertEqual(len(metadata['variants']), 12)
        self.assertTrue(all(v['config']['operations'] == ['exist_hit', 'exist_miss'] for v in metadata['variants']))

    def test_invalid_injection_and_unbounded_inputs_rejected(self):
        for request in [{'host': 'arbitrary'}, {'clients': True}, {'clients': 999},
                        {'nodes': 4, 'workers_per_node': 8}, {'master_cpus': 8, 'server_cpus': 4},
                        {'batches': [4096], 'value_sizes': [1048576]},
                        {'products': ['datasystem', 'datasystem']}, {'operations': []}, {'profile': 'arbitrary'}]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                spec.preview_plan(request)

    def test_default_not_mutated_and_large_matrix_rejected(self):
        a = spec.preview_plan({})
        a['config']['products'].clear()
        self.assertEqual(spec.DEFAULTS['products'], ['datasystem', 'mooncake'])
        with self.assertRaises(ValueError):
            spec.preview_plan({'profile': 'metadata_scaling', 'batches': [1, 16, 256, 4096], 'repeats': 3})

class Jobs(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'jobs.sqlite3')
        self.plan = spec.preview_plan({})

    def tearDown(self):
        self.directory.cleanup()

    def submit(self, key='unique-key'):
        return self.store.submit('owner', 'run', self.plan['config'], self.plan, key)

    def test_concurrent_idempotent_submit_has_one_job(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: self.submit(), range(20)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.store.recent('owner')), 1)
        with self.assertRaises(Conflict):
            self.store.submit('owner', 'deploy', self.plan['config'], self.plan, 'unique-key')

    def test_claim_mutex_and_stop_priority_cancels_queued(self):
        first = self.submit('first-key')
        second = self.submit('second-key')
        self.assertEqual(self.store.claim()['id'], first)
        self.assertIsNone(self.store.claim())
        stop = self.store.submit('owner', 'stop', self.plan['config'], self.plan, 'stop-key')
        self.assertTrue(self.store.get(first)['cancel_requested'])
        self.assertEqual(self.store.get(second)['status'], 'cancelled')
        self.store.finish(first, 'cancelled')
        self.assertEqual(self.store.claim()['id'], stop)

    def test_restart_preserves_actual_events_and_owner_boundaries(self):
        job = self.submit()
        self.store.claim()
        self.store.event(job, {'kind': 'result', 'result': {'status': 'failed', 'keys_s': None}})
        restored = Store(self.store.path)
        self.assertEqual(restored.get(job)['status'], 'running')
        self.assertEqual(restored.get(job)['results'], [{'status': 'failed', 'keys_s': None}])
        with self.assertRaises(KeyError):
            restored.get(job, 'another-user')

    def test_service_download_whitelist_and_atomic_exports(self):
        service = LabService({'state_root': self.directory.name, 'csrf_secret': 'x' * 32})
        job = service.create_job('owner', 'run', {}, 'download-key')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(lambda _: service.get_artifact('owner', job['id'], 'job.json')[0], range(8)))
        self.assertTrue(all(json.loads(p.read_text())['id'] == job['id'] for p in paths))
        with self.assertRaises(FileNotFoundError):
            service.get_artifact('owner', job['id'], '../../settings.json')

    def test_controller_command_cannot_take_request_code(self):
        settings = dict(state_root=self.directory.name, csrf_secret='x' * 32,
                        ssh_target='fixed-host', remote_code_root='/opt/kvc-lab',
                        remote_fair_root='/opt/kvc-fair', remote_config='/etc/kvc-lab.json')
        argv = Controller(settings).argv('execute')
        self.assertEqual(argv[-2], 'fixed-host')
        with self.assertRaises(ValueError):
            Controller({**settings, 'ssh_target': 'host; touch /tmp/bad'})

if __name__ == '__main__':
    unittest.main()
