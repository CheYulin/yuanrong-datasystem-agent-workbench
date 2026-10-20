"""Live admission refuses wrong argv, server charging and reused PID identities."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kvcbench.run.configuration_evidence import live_coordinator


class CoordinatorLiveTests(unittest.TestCase):
    def fixture(self, root):
        binary = root / 'output/datasystem/service/datasystem_coordinator'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'fixture coordinator output, never executed')
        argv = [str(binary), '--coordinator_address=127.0.0.1:31511', '--coordinator_raft_initial_peers=',
                '--log_monitor=false', '--json_log_monitor=false', '--request_sample_rate=0', '--access_sample_rate=0']
        manifest = {'source': str(root), 'cgroup': '/sys/fs/cgroup/kvcfair20261020/server',
                    'coordinator_address': '127.0.0.1:31511', 'coordinator_binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
        return {'pid': 999999, 'start_ticks': '1234', 'command': argv}, manifest

    def observe(self, process, manifest, *, argv=None, group='0::/kvcfair20261020/server', reused=False):
        original_text, original_bytes = Path.read_text, Path.read_bytes
        def read_text(path, *args, **kwargs):
            if str(path) == '/proc/999999/stat':
                return '999999 (synthetic) ' + ' '.join(['S'] + ['0'] * 18 + ['5678' if reused else '1234'])
            if str(path) == '/proc/999999/cgroup':
                return group + '\n'
            return original_text(path, *args, **kwargs)
        def read_bytes(path):
            if str(path) == '/proc/999999/cmdline':
                return ('\0'.join(argv or process['command']) + '\0').encode()
            return original_bytes(path)
        with patch.object(Path, 'read_text', read_text), patch.object(Path, 'read_bytes', read_bytes):
            return live_coordinator(process, manifest)

    def test_actual_native_identity_and_server_group_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            process, manifest = self.fixture(Path(temporary))
            self.assertTrue(self.observe(process, manifest)['verified'])
            with self.assertRaises(ValueError):
                self.observe(process, manifest, group='0::/kvcfair20261020/monitor')
            with self.assertRaises(ValueError):
                self.observe(process, manifest, reused=True)
            with self.assertRaises(ValueError):
                self.observe(process, manifest, argv=[process['command'][0], '--log_monitor=true'])


if __name__ == '__main__':
    unittest.main()
