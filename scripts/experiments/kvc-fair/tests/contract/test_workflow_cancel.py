"""Cancellation refuses unrelated live processes and another boot's receipts."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from kvcbench.pipeline.cancel import cancel
from kvcbench.platform.files import atomic_json


@unittest.skipUnless(hasattr(os, 'pidfd_open'), 'Linux pidfd is required')
class WorkflowCancelTests(unittest.TestCase):
    def fixture(self, root, pid, *, boot=None):
        control, runtime = root / 'control', root / 'runtime'
        runtime.mkdir()
        atomic_json(control / 'spec.json', {'root': str(runtime), 'run_id': 'fixture', 'profile': 'native-batch-tcp'})
        start = {'identity': {'run_id': 'fixture', 'profile': 'native-batch-tcp'},
                 'start_mono_ns': time.monotonic_ns(), 'clock_domain': {'pid': pid, 'host': socket.gethostname(),
                    'boot_id': boot or Path('/proc/sys/kernel/random/boot_id').read_text().strip()}}
        atomic_json(control / 'workflow/steps/W07.smoke/.partial-attempt-000001/start.json', start)
        return control, runtime

    def test_unrelated_command_survives_even_with_matching_receipt_pid_and_cwd(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control, runtime = self.fixture(root, os.getpid())
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], cwd=runtime)
            try:
                start = control / 'workflow/steps/W07.smoke/.partial-attempt-000001/start.json'
                import json
                record = json.loads(start.read_text())
                record['clock_domain']['pid'] = child.pid
                record['start_mono_ns'] = time.monotonic_ns()
                atomic_json(start, record)
                with patch('signal.pidfd_send_signal') as send:
                    with self.assertRaises(ValueError):
                        cancel(control, root / 'cancel', 'synthetic refusal test')
                    send.assert_not_called()
                self.assertIsNone(child.poll())
                self.assertFalse((root / 'cancel').exists())
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_another_boot_is_rejected_before_opening_any_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control, _ = self.fixture(root, os.getpid(), boot='not-this-host-boot')
            with patch('os.pidfd_open') as opened:
                with self.assertRaises(ValueError):
                    cancel(control, root / 'cancel', 'synthetic boot mismatch')
                opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
