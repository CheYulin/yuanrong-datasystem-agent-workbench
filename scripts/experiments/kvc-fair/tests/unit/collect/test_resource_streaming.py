"""Audit every sample without allocating complete multi-gigabyte JSONL files."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kvcbench.collect.audits import resources as subject
from tests.paths import TOOL_ROOT
from tests.unit.collect.test_batch_resource_audit import sample


class ResourceStreamingTests(unittest.TestCase):
    def test_projection_preserves_all_window_counters_processes_and_unknowns(self):
        for kind in ('normal', 'missing', 'reset', 'pid_reuse', 'host_missing', 'host_null'):
            with self.subTest(kind=kind):
                rows = [sample(1_000_000_000 * at, at * 2_000_000) for at in (1, 2, 3)]
                for at, row in enumerate(rows):
                    row['host']['stat'] += 'cpu0 ignored per-core row\n'
                    row['host']['meminfo'] = 'unused host details'
                    row['tool_returncodes'] = {name: None for name in ('iostat', 'mpstat', 'pidstat', 'dstat')}
                    for role in subject.ROLES:
                        group = subject.group(row, role)
                        group['io.stat'] = '253:2 rbytes=1 wbytes=2'
                        group['processes'] = [{'pid': 4, 'start_ticks': 100, 'comm': 'owned',
                            'utime_ticks': at * 20, 'stime_ticks': at * 3, 'status': {'unused': 'details'}}]
                if kind == 'missing':
                    del rows[1]['groups']['server']['memory.events']['oom']
                    del rows[1]['groups']['client']['memory.stat']['shmem']
                elif kind == 'reset':
                    rows[1]['groups']['client']['cpu.stat']['usage_usec'] = 0
                elif kind == 'pid_reuse':
                    rows[1]['groups']['server']['processes'][0]['start_ticks'] = 200
                if kind == 'host_missing':
                    del rows[1]['host']['stat']
                elif kind == 'host_null':
                    rows[1]['host'] = None
                for row in rows:
                    row['groups']['unconsumed_group'] = 'opaque extra data'
                original = deepcopy(rows)
                compact = [subject.sample_for_audit(row) for row in rows]
                times = [row['mono_ns'] for row in rows]
                self.assertEqual(subject.window_audit(rows, times, [], times[0], times[-1], 100),
                                 subject.window_audit(compact, times, [], times[0], times[-1], 100))
                self.assertEqual(rows, original)
                self.assertEqual(len(compact), len(rows))

    def test_stream_read_and_hash_do_not_use_whole_file_methods(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'samples.jsonl'
            content = '\n'.join(json.dumps(sample(at, at)) for at in (1, 2)) + '\n'
            path.write_text(content)
            with (patch.object(Path, 'read_text', side_effect=AssertionError('whole-file text')),
                  patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file bytes'))):
                records = subject.read_samples(path)
                identity = subject.file_record(path, root)
            self.assertEqual([row['mono_ns'] for row in records], [1, 2])
            self.assertEqual(identity['sha256'], hashlib.sha256(content.encode()).hexdigest())

    def test_duplicate_and_out_of_order_samples_are_still_rejected(self):
        for times in [(1, 1), (2, 1)]:
            with self.subTest(times=times), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'samples.jsonl'
                path.write_text('\n'.join(json.dumps(sample(at, at)) for at in times))
                with self.assertRaisesRegex(ValueError, 'strictly increasing'):
                    subject.read_samples(path)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux address-space limit acceptance')
    def test_input_larger_than_process_memory_limit_is_processed_without_losing_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'samples.jsonl'
            row = sample(1, 1)
            row['unconsumed_detail'] = 'x' * (1 << 20)
            with path.open('w') as stream:
                for at in range(128):
                    row['mono_ns'] = at
                    stream.write(json.dumps(row) + '\n')
            code = '''
import json,resource,sys
from pathlib import Path
from kvcbench.collect.audits.resources import read_samples,file_record
limit=96 << 20
resource.setrlimit(resource.RLIMIT_AS,(limit,limit))
path=Path(sys.argv[1])
rows=read_samples(path)
assert len(rows)==128 and [r['mono_ns'] for r in rows]==list(range(128))
assert file_record(path,path.parent)['bytes']>128 << 20
print(json.dumps({'records':len(rows),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
'''
            completed = subprocess.run([sys.executable, '-B', '-c', code, str(path)], cwd=TOOL_ROOT,
                                       capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout)['records'], 128)
