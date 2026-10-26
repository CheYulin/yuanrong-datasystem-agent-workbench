import concurrent.futures
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
import hashlib
import base64
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

class NativeResultShape(unittest.TestCase):
    def test_real_histogram_bound_contract_is_numeric_and_missing_stays_missing(self):
        from kvclab.remote_runtime import latency_us, workload_sizes
        self.assertEqual(latency_us({'p99': {'lower_us': 122.88, 'upper_us': 124.928}}, 'p99'), 124.928)
        self.assertIsNone(latency_us({'p99': None}, 'p99'))
        self.assertIsNone(latency_us({'p99': {'upper_us': float('nan')}}, 'p99'))
        pool, puts = workload_sizes(spec.validate({}), 1024)
        self.assertGreaterEqual(pool, 32768)
        self.assertGreaterEqual(puts[1], 262144)


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

    def test_stop_remains_available_with_full_queue(self):
        for i in range(12):
            self.submit('full-queue-' + str(i))
        with self.assertRaises(Conflict):
            self.submit('overflow-key')
        stopped = self.store.submit('owner', 'stop', self.plan['config'], self.plan, 'stop-full-queue')
        self.assertEqual(self.store.claim()['id'], stopped)
        self.assertEqual(sum(j['status'] == 'cancelled' for j in self.store.recent('owner')), 12)

    def test_cleanup_gate_blocks_runs_until_owned_stop_accepted(self):
        job = self.submit()
        self.store.cleanup_required(True)
        self.assertIsNone(self.store.claim())
        cleanup = self.store.submit('owner', 'stop', self.plan['config'], self.plan, 'recover-owner')
        self.assertEqual(self.store.claim()['id'], cleanup)
        self.store.finish(cleanup, 'failed')
        self.assertTrue(self.store.cleanup_required())
        self.assertIsNone(self.store.claim())

    def test_deployment_wrapper_normalized_and_remote_artifact_is_verified(self):
        service = LabService({'state_root': self.directory.name, 'csrf_secret': 'x' * 32})
        job = service.create_job('owner', 'run', {}, 'artifact-key')
        service.store.event(job['id'], {'kind': 'deployment', 'deployment': {'active': True, 'product': 'datasystem'}})
        self.assertTrue(service.store.deployment()['active'])
        service.store.event(job['id'], {'kind': 'step', 'name': 'qualify', 'artifact': 'stage/result.json'})
        rendered = service.get_job('owner', job['id'])
        name = rendered['steps'][0]['artifact_url'].split('/')[-1]
        payload = b'{"valid":true}'
        response = {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), 'data': base64.b64encode(payload).decode()}
        with patch('kvclab.transport.rpc', return_value=response) as rpc:
            path, _ = service.get_artifact('owner', job['id'], name)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(rpc.call_args.args[2]['name'], 'stage/result.json')
        with patch('kvclab.transport.rpc', return_value={**response, 'sha256': '0' * 64}), self.assertRaises(RuntimeError):
            service.get_artifact('owner', job['id'], name)

    def test_controller_command_cannot_take_request_code(self):
        settings = dict(state_root=self.directory.name, csrf_secret='x' * 32,
                        ssh_target='fixed-host', remote_code_root='/opt/kvc-lab',
                        remote_fair_root='/opt/kvc-fair', remote_config='/etc/kvc-lab.json')
        argv = Controller(settings).argv('execute')
        self.assertEqual(argv[-2], 'fixed-host')
        with self.assertRaises(ValueError):
            Controller({**settings, 'ssh_target': 'host; touch /tmp/bad'})


class Recovery(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = dict(state_root=str(self.root), csrf_secret='x' * 32, ssh_target='fixed-host',
                             remote_code_root='/opt/kvc-lab', remote_fair_root='/opt/kvc-fair',
                             remote_config='/etc/kvc-lab.json')
        self.controller = Controller(self.settings)
        self.store = self.controller.store
        self.plan = spec.preview_plan({})
        self.job = self.store.submit('owner', 'run', self.plan['config'], self.plan, 'recovery-key')
        self.store.claim()

    def event(self, index):
        return dict(kind='step', job_id=self.job, wall_ns=index, mono_ns=index,
                    name='stage-' + str(index), duration_s=1, artifact=f'stage-{index}.json', status='passed')

    @staticmethod
    def snapshot(events, tail=b''):
        return b''.join(json.dumps(event, ensure_ascii=False).encode() + b'\n' for event in events) + tail

    @staticmethod
    def artifact(data):
        return dict(data=base64.b64encode(data).decode(), size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def test_live_events_deduplicate_against_recovery_snapshot_and_persist_cursor(self):
        first, second = self.event(1), self.event(2)
        self.store.event(self.job, first)
        data = self.snapshot([first, second])
        receipt = self.store.ingest_remote_log(self.job, data)
        self.assertEqual((receipt['added'], receipt['duplicates']), (1, 1))
        restored = Store(self.store.path)
        self.assertEqual(restored.remote_cursor(self.job)['byte_offset'], len(data))
        self.assertEqual(restored.ingest_remote_log(self.job, data)['added'], 0)
        self.assertEqual([first, second], restored.get(self.job)['steps'])
        self.assertFalse(restored.event(self.job, second, remote=True))

    def test_partial_final_line_is_only_consumed_when_complete(self):
        first, second = self.event(1), self.event(2)
        first_data = self.snapshot([first])
        partial = self.snapshot([second])[:-4]
        receipt = self.store.ingest_remote_log(self.job, first_data + partial)
        self.assertEqual(receipt['byte_offset'], len(first_data))
        self.assertEqual(receipt['partial_bytes'], len(partial))
        receipt = self.store.ingest_remote_log(self.job, self.snapshot([first, second]))
        self.assertEqual(receipt['added'], 1)
        self.assertEqual(receipt['partial_bytes'], 0)

    def test_truncated_or_rewritten_prefix_cannot_silently_reset_cursor(self):
        data = self.snapshot([self.event(1)])
        self.store.ingest_remote_log(self.job, data)
        for replacement in (b'', self.snapshot([self.event(2)])):
            with self.assertRaisesRegex(ValueError, '截断或改写'):
                self.store.ingest_remote_log(self.job, replacement)
        self.assertEqual(len(self.store.get(self.job)['steps']), 1)

    def test_malformed_or_cross_job_events_roll_back_the_entire_batch(self):
        for tail in (b'invalid-json\n', self.snapshot([{**self.event(2), 'job_id': 'another-job'}])):
            with self.assertRaises(ValueError):
                self.store.ingest_remote_log(self.job, self.snapshot([self.event(1)]) + tail)
            self.assertEqual(self.store.get(self.job)['steps'], [])
            self.assertEqual(self.store.remote_cursor(self.job)['byte_offset'], 0)

    def test_legacy_events_are_indexed_without_duplicate_steps(self):
        first = self.event(1)
        with self.store.connect() as db:
            db.execute('INSERT INTO events(job_id,recorded,payload) VALUES(?,?,?)',
                       (self.job, 1, json.dumps(first)))
        receipt = self.store.ingest_remote_log(self.job, self.snapshot([first]))
        self.assertEqual(receipt['added'], 0)
        self.assertEqual(receipt['duplicates'], 1)
        self.assertEqual(len(self.store.get(self.job)['steps']), 1)

    def test_controller_recovers_every_step_before_accepting_complete_without_replay(self):
        first, second, third = self.event(1), self.event(2), self.event(3)
        self.controller.record(self.job, first, remote=True)
        final = dict(kind='complete', job_id=self.job, status='succeeded', results=[{'status': 'valid'}],
                     deployment={'active': True})
        states = [dict(running=True, complete=None), dict(running=False, complete=final)]
        snapshots = [self.snapshot([first, second]), self.snapshot([first, second, third])]
        calls = []
        def rpc(action, request):
            calls.append((action, request))
            return states.pop(0) if action == 'state' else self.artifact(snapshots.pop(0))
        self.controller.rpc = rpc
        with patch('kvclab.controller.time.sleep'):
            self.controller.recover()
        job = self.store.get(self.job)
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['steps'], [first, second, third])
        mirror = [json.loads(line) for line in (self.root / 'jobs' / self.job / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(row['kind'] == 'complete' for row in mirror), 1)
        self.assertEqual(sum(row['kind'] == 'step' for row in mirror), 3)
        self.assertEqual([action for action, _ in calls], ['state', 'artifact', 'state', 'artifact'])

    def test_corrupt_transfer_never_finishes_or_replays_the_running_job(self):
        complete = dict(kind='complete', job_id=self.job, status='succeeded', results=[])
        response = {**self.artifact(self.snapshot([self.event(1)])), 'sha256': '0' * 64}
        calls = []
        def rpc(action, request):
            calls.append(action)
            return dict(running=False, complete=complete) if action == 'state' else response
        self.controller.rpc = rpc
        def stop_wait(_duration):
            self.controller.stopping = True
        with patch('kvclab.controller.time.sleep', side_effect=stop_wait):
            self.controller.recover()
        self.assertEqual(self.store.get(self.job)['status'], 'running')
        self.assertEqual(calls, ['state', 'artifact'])
        self.assertEqual(self.store.get(self.job)['steps'], [])

    def test_cleanup_failure_blocks_new_measurements_in_live_and_recovered_completion(self):
        for evidence in ({'cleanup_error': 'still populated'}, {'cleanup_errors': ['not drained']},
                         {'error': 'failed\nCleanup error:\nnot drained'}, {'deployment': {'active': True}}):
            self.store.cleanup_required(False)
            complete = dict(kind='complete', job_id=self.job, status='failed', results=[], **evidence)
            self.controller.accept_complete(self.job, complete)
            self.assertTrue(self.store.cleanup_required())
        self.store.submit('owner', 'run', self.plan['config'], self.plan, 'after-cleanup-failure')
        self.assertIsNone(self.store.claim())

    def test_recovered_successful_stop_clears_gate_and_preserves_failure_reason(self):
        self.store.finish(self.job, 'failed')
        self.store.cleanup_required(True)
        stopped = self.store.submit('owner', 'stop', self.plan['config'], self.plan, 'cleanup-recovered')
        self.store.claim()
        self.controller.rpc = lambda action, request: (
            dict(running=False, complete=dict(kind='complete', job_id=stopped, status='succeeded',
                                              deployment={'active': False}))
            if action == 'state' else self.artifact(b'')
        )
        self.controller.recover()
        self.assertEqual(self.store.get(stopped)['status'], 'succeeded')
        self.assertFalse(self.store.cleanup_required())
        self.controller.accept_complete(self.job, dict(kind='complete', status='failed', error='original failure'))
        self.assertEqual(self.store.get(self.job)['error'], 'original failure')

    def test_reproduction_defaults_to_new_identity_and_original_is_read_only(self):
        capture = self.root / 'capture.py'
        output = self.root / 'captured.json'
        capture.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps({"action":sys.argv[2],"request":json.load(sys.stdin)}))\n')
        self.controller.argv = lambda action: [sys.executable, str(capture), str(output), action]
        request = dict(job_id=self.job, action='run', config=self.plan['config'], plan=self.plan)
        script = self.root / 'reproduce.sh'
        script.write_text(self.controller.reproduce_script(request))
        ids = []
        for _ in range(2):
            subprocess.run(['bash', str(script)], check=True, capture_output=True, timeout=5)
            received = json.loads(output.read_text())
            self.assertEqual(received['action'], 'execute')
            self.assertEqual(received['request']['config'], request['config'])
            ids.append(received['request']['job_id'])
        self.assertEqual(len(set(ids + [self.job])), 3)
        subprocess.run(['bash', str(script), '--original'], check=True, capture_output=True, timeout=5)
        self.assertEqual(json.loads(output.read_text()), {'action': 'state', 'request': {'job_id': self.job}})
        self.assertNotIn(self.settings['csrf_secret'], script.read_text())

    def test_job_binds_only_existing_hash_verified_source_archive(self):
        self.controller.source_provenance(self.job)
        self.assertIsNone(self.store.get(self.job)['provenance'])
        payload = b'fixture source archive'
        identity = hashlib.sha256(payload).hexdigest()
        self.controller.settings['source_sha256'] = identity
        self.controller.source_provenance(self.job)
        self.assertIsNone(self.store.get(self.job)['provenance'])
        sources = self.root / 'sources'
        sources.mkdir()
        source = sources / (identity + '.tar.gz')
        source.write_bytes(payload)
        self.controller.source_provenance(self.job)
        self.assertEqual(self.store.get(self.job)['provenance']['source_sha256'], identity)
        source.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, '身份不匹配'):
            self.controller.source_provenance(self.job)

if __name__ == '__main__':
    unittest.main()
