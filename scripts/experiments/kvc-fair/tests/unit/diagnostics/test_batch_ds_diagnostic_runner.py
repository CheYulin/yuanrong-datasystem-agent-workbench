"""Owned-process cleanup regressions with fake Popen; no product execution."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kvcbench.diagnostics import datasystem_runner as subject
class DiagnosticCleanupTests(unittest.TestCase):
    def fixture(self, parent):
        root = parent / "root";root.mkdir()
        products = {"products": [{"name": "datasystem", "python": "fixture-python",
                                  "start": ["fixture-start", "{run_dir}"],
                                  "stop": ["fixture-stop", "{run_dir}"], "env": {"KVC_FEATURE_PROFILE": "pure-kv"}}]}
        content = json.dumps(products).encode();(root / "products.json").write_bytes(content)
        stage = {"feature_profile": "pure-kv", "inputs": {"products.json": {
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}}}
        (root / "batch-stage.json").write_text(json.dumps(stage))
        return SimpleNamespace(root=root, out=root / "diagnostic-case"), root / "run/diagnostic-purekv"

    def exercise(self, args, *, drain_failure=False, monitor_fault=None, start_failure=False, sdk_failure=False):
        commands, monitors, drains = [], [], []
        sentinel = {"checked_utc": "fixture", "members": {}, "populated": []}

        def drain(*positional, **kwargs):
            drains.append((positional, kwargs))
            if drain_failure and len(drains) == 1:
                raise RuntimeError("synthetic populated precondition")
            return sentinel

        class Process:
            pid = 999999999

            def __init__(self, argv, **kwargs):
                self.argv, self.returncode, self.monitor = list(argv), None, any(str(arg).endswith('/monitor.py') for arg in argv)
                self.signals = []
                commands.append(self.argv)
                if self.monitor:
                    monitors.append(self)
                if self.argv[0] == "fixture-start":
                    Path(self.argv[1]).mkdir(parents=True, exist_ok=True)
                if any(str(arg).endswith('/batch_ds_deadline_diagnostic.py') for arg in argv):
                    output = Path(argv[argv.index('--out') + 1]);output.mkdir(parents=True)
                    (output / "summary.json").write_text(json.dumps({"diagnostic_complete": True, "all_sdk_steps_passed": True}))

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                if self.monitor and monitor_fault == "wait":
                    raise OSError("synthetic monitor wait failure")
                self.returncode = 1 if start_failure and self.argv[0] == "fixture-start" or sdk_failure and any(str(arg).endswith('/batch_ds_deadline_diagnostic.py') for arg in self.argv) else 0
                return self.returncode

            def send_signal(self, signum):
                self.signals.append(signum)
                if self.monitor and monitor_fault == "signal":
                    self.returncode = 0
                    raise ProcessLookupError("synthetic exit race")

        exception, code = None, None
        with patch.object(subject, "drain", side_effect=drain), patch.object(subject.subprocess, "Popen", Process), \
                patch.object(subject, "stop_process"), patch.object(subject.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                code = subject.run(args)
            except Exception as exc:
                exception = exc
        summary = json.loads((args.out / "summary.json").read_text()) if (args.out / "summary.json").exists() else None
        return commands, monitors, drains, code, exception, summary

    def test_existing_run_directory_is_refused_without_stopping_its_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            args, run = self.fixture(Path(directory));run.mkdir(parents=True)
            (run / "owner-sentinel").write_bytes(b"another invocation owns this directory\n")
            commands, _, _, code, error, _ = self.exercise(args)
            self.assertFalse(commands)
            self.assertTrue(error is not None or code != 0)
            self.assertEqual((run / "owner-sentinel").read_bytes(), b"another invocation owns this directory\n")

    def test_initial_drain_refusal_cannot_stop_any_existing_service(self):
        with tempfile.TemporaryDirectory() as directory:
            args, _ = self.fixture(Path(directory))
            commands, _, _, code, error, _ = self.exercise(args, drain_failure=True)
            self.assertFalse(commands)
            self.assertTrue(error is not None or code != 0)

    def test_monitor_signal_exit_race_still_drains_and_persists_terminal_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            args, _ = self.fixture(Path(directory))
            commands, monitors, drains, _, error, summary = self.exercise(args, monitor_fault="signal")
            self.assertIsNone(error)
            self.assertEqual(len(monitors), 1)
            self.assertTrue(monitors[0].signals)
            self.assertGreaterEqual(len(drains), 2)
            self.assertIsNotNone(summary)
            self.assertIn("after", summary)
            self.assertIn("end_mono_ns", summary)
            self.assertTrue(any(command[0] == "fixture-stop" for command in commands))

    def test_monitor_wait_error_does_not_skip_final_drain_or_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            args, _ = self.fixture(Path(directory))
            _, _, drains, code, error, summary = self.exercise(args, monitor_fault="wait")
            self.assertIsNone(error)
            self.assertEqual(code, 1)
            self.assertGreaterEqual(len(drains), 2)
            self.assertIn("after", summary)
            self.assertTrue(summary["errors"])

    def test_partially_started_own_service_is_stopped_after_start_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            args, _ = self.fixture(Path(directory))
            commands, _, drains, code, error, summary = self.exercise(args, start_failure=True)
            self.assertIsNone(error)
            self.assertEqual(code, 1)
            start = next(index for index, command in enumerate(commands) if command[0] == "fixture-start")
            self.assertTrue(any(command[0] == "fixture-stop" for command in commands[start + 1:]))
            self.assertGreaterEqual(len(drains), 2)
            self.assertFalse(summary["diagnostic_complete"])

    def test_sdk_command_failure_remains_failed_after_normal_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            args, _ = self.fixture(Path(directory))
            commands, _, drains, code, error, summary = self.exercise(args, sdk_failure=True)
            self.assertIsNone(error)
            self.assertEqual(code, 1)
            self.assertGreaterEqual(len(drains), 2)
            self.assertTrue(summary["errors"])
            self.assertFalse(summary["diagnostic_complete"])
            self.assertEqual(commands[-1][0], "fixture-stop")


if __name__ == "__main__":
    unittest.main()
