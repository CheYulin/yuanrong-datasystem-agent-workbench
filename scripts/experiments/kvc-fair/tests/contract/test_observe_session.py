"""Readiness is a stable snapshot; only stopped streams can be finalized."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kvcbench.observe.session import Session, REQUIRED_TOOLS
from kvcbench.platform.files import digest
from kvcbench.observe import validation


class Child:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None
        self.signals = []

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode


class ObserveSessionTests(unittest.TestCase):
    def fixture(self, out, tool_exit=None):
        tools = {name: {'available': True, 'pid': i + 100} for i, name in enumerate(REQUIRED_TOOLS)}
        (out / 'monitor-manifest.json').write_text(json.dumps({'commands': tools}))
        sample = {'groups': {role: {'cpu.stat': {'usage_usec': 1}, 'memory.current': 42}
                             for role in ('server', 'client')}, 'tool_returncodes': {name: tool_exit for name in tools}}
        (out / 'samples.jsonl').write_text(json.dumps(sample) + '\n')
        (out / 'interference.jsonl').write_text('{}\n')

    def test_ready_snapshot_stays_valid_as_stream_grows_then_final_binds_closed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'observations'
            children = []
            def spawn(*args, **kwargs):
                if not children:
                    self.fixture(out)
                child = Child(1000 + len(children))
                children.append(child)
                return child
            session = Session(out, Path('/fixture/cgroup'))
            with patch('kvcbench.observe.session.subprocess.Popen', side_effect=spawn):
                ready = session.start(timeout=1)
                before = digest(ready)
                with (out / 'samples.jsonl').open('a') as stream:
                    stream.write('{}\n')
                self.assertEqual(before, digest(ready))
                result = session.stop()
            self.assertTrue(result['closed'])
            self.assertTrue(result['normal_exit'])
            self.assertTrue(all(child.signals for child in children))
            samples = next(row for row in result['files'] if row['path'] == 'samples.jsonl')
            self.assertEqual(samples['sha256'], digest(out / 'samples.jsonl'))
            self.assertIsNone(session.stop())
            self.assertTrue(validation.ready(ready)['valid'])
            self.assertTrue(validation.final(out / 'final.json')['valid'])
            with (out / 'samples.jsonl').open('a') as stream:
                stream.write('tamper\n')
            with self.assertRaisesRegex(ValueError, 'changed'):
                validation.final(out / 'final.json')

    def test_second_spawn_failure_stops_first_and_retains_final_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Path(directory) / 'observations', Path('/fixture/cgroup'))
            first = Child(1000)
            with patch('kvcbench.observe.session.subprocess.Popen', side_effect=[first, OSError('second spawn')]):
                with self.assertRaisesRegex(OSError, 'second spawn'):
                    session.start()
            self.assertEqual(first.returncode, 0)
            self.assertTrue((session.out / 'final.json').exists())
            self.assertFalse((session.out / 'ready.json').exists())

    def test_dead_required_tool_blocks_readiness_and_closes_owned_children(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'observations'
            def spawn(*args, **kwargs):
                self.fixture(out, tool_exit=1)
                return Child(1000)
            session = Session(out, Path('/fixture/cgroup'))
            with patch('kvcbench.observe.session.subprocess.Popen', side_effect=spawn):
                with self.assertRaises(TimeoutError):
                    session.start(timeout=.01)
            self.assertFalse((out / 'ready.json').exists())
            self.assertTrue(json.loads((out / 'final.json').read_text())['closed'])

    def test_existing_observation_root_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / 'original').write_text('keep')
            with self.assertRaises(FileExistsError):
                Session(out, Path('/fixture/cgroup')).start()
            self.assertEqual((out / 'original').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
