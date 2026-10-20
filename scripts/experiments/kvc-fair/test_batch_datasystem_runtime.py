"""Launch-boundary regressions for two-worker metadata storage isolation."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


from kvcbench.run.products.datasystem import runtime


class RuntimeStorageTests(unittest.TestCase):
    def test_compatibility_command_enters_the_parser_from_unrelated_cwd(self):
        script = Path(__file__).resolve().parent / "datasystem/runtime.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(script), "--help"], cwd=directory,
                env=dict(os.environ, PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--task-root", result.stdout)
        self.assertIn("{start,stop}", result.stdout)

    def test_main_dispatches_the_requested_lifecycle_action(self):
        for action in ("start", "stop"):
            with self.subTest(action=action), mock.patch.object(runtime, "launch") as launch, mock.patch.object(runtime, "stop") as stop:
                runtime.main([action])
                called = launch if action == "start" else stop
                self.assertEqual(called.call_args.args[0].action, action)

    def launch(self, root, run_dir, transport="tcp", profile="baseline"):
        commands = []
        original_open = Path.open

        def open_file(path, *args, **kwargs):
            if path == Path("/usr/local/bin/etcd"):
                return io.BytesIO(b"fixture-etcd")
            return original_open(path, *args, **kwargs)

        def spawn(command, **kwargs):
            commands.append((command, kwargs))
            return SimpleNamespace(pid=900000 + len(commands), poll=lambda: None)

        args = runtime.parse_args(["start", "--task-root", str(root), "--run-dir", str(run_dir),
                                   "--transport", transport, "--profile", profile])
        with mock.patch.object(Path, "open", open_file), mock.patch.object(runtime.socket, "socket"), \
                mock.patch.object(runtime.subprocess, "Popen", side_effect=spawn), \
                mock.patch.object(runtime.subprocess, "check_output", return_value="fixture-etcd-version"), \
                mock.patch.object(runtime, "start_ticks", return_value="fixture-start"), \
                mock.patch.object(runtime, "wait_port"), mock.patch.object(runtime, "wait_ready"), \
                contextlib.redirect_stdout(io.StringIO()):
            runtime.launch(args)
        return commands, json.loads((run_dir / "processes.json").read_text())

    def assert_rejected_before_process(self, root, run_dir):
        with mock.patch.object(runtime.subprocess, "Popen") as spawn:
            with self.assertRaises(RuntimeError):
                runtime.launch(runtime.parse_args(["start", "--task-root", str(root), "--run-dir", str(run_dir)]))
            spawn.assert_not_called()

    def test_native_and_tcp_actual_argv_have_distinct_absolute_db_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for transport in ["native", "tcp"]:
                run_dir = root / transport
                commands, manifest = self.launch(root, run_dir, transport)
                self.assertEqual(len(commands), 3)
                actual = []
                for index, (command, kwargs) in enumerate(commands[1:]):
                    flags = [flag for flag in command if flag.startswith("--rocksdb_store_dir=")]
                    expected = run_dir / f"w{index}" / "rocksdb"
                    self.assertEqual(flags, [f"--rocksdb_store_dir={expected}"])
                    self.assertEqual(manifest["rocksdb_store_dirs"][f"worker{index}"], str(expected))
                    self.assertTrue(expected.is_dir())
                    self.assertEqual(list(expected.iterdir()), [])
                    self.assertEqual(kwargs["cwd"], run_dir)
                    self.assertEqual((run_dir / f"w{index}/worker.flags").read_text(),
                                     "\n".join(command[1:]) + "\n")
                    self.assertEqual("--ipc_through_shared_memory=false" in command, transport == "tcp")
                    self.assertIn("--minloglevel=2", command)
                    actual.append(expected.resolve())
                self.assertNotEqual(*actual)

    def test_pure_kv_disables_optional_services_at_actual_process_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands, manifest = self.launch(root, root / "pure", profile="pure-kv")
            self.assertEqual(manifest["runtime_profile"], "pure-kv")
            for command, _ in commands[1:]:
                for flag in ["--rocksdb_write_mode=none", "--sc_regular_socket_num=0",
                             "--sc_stream_socket_num=0", "--json_log_monitor=false",
                             "--enable_perf_trace_log=false", "--request_sample_rate=0",
                             "--access_sample_rate=0", "--log_monitor=false",
                             "--enable_urma=false", "--enable_rdma=false",
                             "--ipc_through_shared_memory=false", "--l2_cache_type=none",
                             "--spill_size_limit=0", "--minloglevel=2"]:
                    self.assertEqual(command.count(flag), 1, flag)
                self.assertNotIn("--diagnostic_sample_rate=0", command)
                self.assertFalse(any(flag.startswith("--request_timeout") for flag in command))
                self.assertTrue(any(flag.startswith("--etcd_address=") for flag in command))

    def test_default_profile_retains_original_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands, manifest = self.launch(root, root / "baseline")
            self.assertNotIn("runtime_profile", manifest)
            changed_keys = ["rocksdb_write_mode", "sc_regular_socket_num", "sc_stream_socket_num",
                            "json_log_monitor", "enable_perf_trace_log", "request_sample_rate",
                            "access_sample_rate"]
            for command, _ in commands[1:]:
                self.assertFalse(any(flag.startswith(f"--{key}=") for key in changed_keys for flag in command))

    def test_profile_must_be_explicit_known_choice(self):
        self.assertEqual(runtime.parse_args(["start"]).profile, "baseline")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runtime.parse_args(["start", "--profile", "pur-kv"])

    def test_outside_task_root_and_parent_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "task"
            root.mkdir()
            self.assert_rejected_before_process(root, root.parent / "outside")
            self.assert_rejected_before_process(root, root / "sub/../run")
            self.assert_rejected_before_process(root, root)

    def test_symlink_run_and_ancestor_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "linked"
            link.symlink_to(target, target_is_directory=True)
            self.assert_rejected_before_process(root, link)
            self.assert_rejected_before_process(root, link / "child")
            self.assertEqual(list(target.iterdir()), [])

    def test_symlink_worker_and_shared_worker_directory_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            (run_dir / "w0").mkdir(parents=True)
            (run_dir / "w1").symlink_to(run_dir / "w0", target_is_directory=True)
            self.assert_rejected_before_process(root, run_dir)
            self.assertFalse((run_dir / "w0/rocksdb").exists())

    def test_dangling_database_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            (run_dir / "w0").mkdir(parents=True)
            (run_dir / "w0/rocksdb").symlink_to(root / "missing", target_is_directory=True)
            self.assert_rejected_before_process(root, run_dir)
            self.assertFalse((root / "missing").exists())

    def test_run_replay_refused_even_after_manifest_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            self.launch(root, run_dir)
            before = (run_dir / "processes.json").read_bytes()
            self.assert_rejected_before_process(root, run_dir)
            self.assertEqual((run_dir / "processes.json").read_bytes(), before)
            (run_dir / "processes.json").unlink()
            self.assert_rejected_before_process(root, run_dir)

    def test_existing_process_evidence_rejected_without_worker_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            run_dir.mkdir()
            evidence = run_dir / "processes.json"
            evidence.write_text(json.dumps({"processes": []}))
            original = evidence.read_bytes()
            self.assert_rejected_before_process(root, run_dir)
            self.assertEqual(evidence.read_bytes(), original)

    def test_symlink_task_root_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root = directory / "real"
            root.mkdir()
            link = directory / "task"
            link.symlink_to(root, target_is_directory=True)
            self.assert_rejected_before_process(link, link / "run")
            self.assertEqual(list(root.iterdir()), [])

    def test_old_etcd_state_without_worker_or_manifest_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            (run_dir / "etcd-data").mkdir(parents=True)
            self.assert_rejected_before_process(root, run_dir)

    def test_dangling_manifest_cannot_redirect_evidence_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            run_dir.mkdir()
            target = root / "must-not-create"
            (run_dir / "processes.json").symlink_to(target)
            self.assert_rejected_before_process(root, run_dir)
            self.assertFalse(target.exists())

    def test_stop_retains_pid_identity_protection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owned = subprocess.Popen(["sleep", "30"])
            protected = subprocess.Popen(["sleep", "30"])
            try:
                (root / "processes.json").write_text(json.dumps({"processes": [
                    {"name": "protected", "pid": protected.pid, "start_ticks": "deliberate-mismatch"},
                    {"name": "owned", "pid": owned.pid, "start_ticks": runtime.start_ticks(owned.pid)}]}))
                with contextlib.redirect_stdout(io.StringIO()):
                    runtime.stop(SimpleNamespace(task_root=str(root.parent), run_dir=str(root), stop_timeout=.5))
                owned.wait(timeout=5)
                self.assertLess(owned.returncode, 0)
                self.assertIsNone(protected.poll())
                rows = json.loads((root / "stop-results.json").read_text())
                self.assertEqual(next(row for row in rows if row["pid"] == protected.pid)["result"],
                                 "already_stopped_or_reused")
            finally:
                for process in [owned, protected]:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
