#!/usr/bin/env python3
"""Local-only guard fixtures. No product services, SSH, or real cgroups."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import tcp_protocol_gate
import tcp_stage
import tcp_workflow


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kvc-tcp-stage-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.base = self.directory / "native"
        self.root = self.directory / "tcp"
        self.bundle = self.directory / "bundle"
        self.cgroup = self.directory / "cg"
        for role in ["", "server", "client", "monitor"]:
            path = self.cgroup / role
            path.mkdir(parents=True, exist_ok=True)
            (path / "cgroup.procs").write_text("")
        native = self.base / "formal-001"
        (native / "builds").mkdir(parents=True)
        (native / "summary.json").write_text(json.dumps({"ended": "2026-10-20T00:01:00Z", "failed": [], "completed": ["fixture"]}))
        (self.base / "suite.lock").write_text("")
        for name in ["datasystem", "mooncake/source", "mooncake/deps", "mooncake/venv", "mooncake/wheels", "mooncake/.evidence", "lmcache/source", "lmcache/venv311", "lmcache/.evidence"]:
            path = self.base / name
            path.mkdir(parents=True, exist_ok=True)
            (path / "original-sentinel").write_text(name + "\n")
        self.artifacts = {}
        for name, relative in [("datasystem", "output/datasystem/service/datasystem_worker"),
                               ("mooncake", "wheels/fixture.whl"),
                               ("lmcache", "source/lmcache_native.cpython-311-x86_64-linux-gnu.so")]:
            path = self.base / name / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((name + "-native-artifact").encode())
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            self.artifacts[str(path)] = checksum
            rows = [{"path": relative, "sha256": checksum}] if name == "mooncake" else {relative: checksum}
            (native / "builds" / (name + ".json")).write_text(json.dumps({"product": name, "artifact_hashes": rows}))
        evidence = native / "build-and-script-evidence"
        evidence.mkdir()
        (evidence / "mooncake.build.log").write_text("fixture original build log; not runtime evidence\n")
        self.bundle.mkdir()
        (self.bundle / "benchmark.py").write_text("# copied fixture; not executable\n")
        products = []
        for name in ["datasystem", "mooncake", "lmcache"]:
            (self.bundle / name).mkdir()
            (self.bundle / name / "adapter.py").write_text("# fixture adapter\n")
            products.append({"name": name, "python": str(self.base / name / "venv/bin/python"),
                             "adapter": str(self.base / name / "adapter.py"),
                             "config": str(self.base / "configs" / (name + ".json")),
                             "start": ["python3", str(self.base / name / "runtime.py"), "start"],
                             "stop": ["python3", str(self.base / name / "runtime.py"), "stop"],
                             "env": {"FIXTURE_ROOT": str(self.base)}})
        (self.bundle / "lmcache/start_server_tcp.sh").write_text("# distinct TCP fixture\n")
        self.manifest = self.bundle / "products.json"
        self.manifest.write_text(json.dumps({"products": products}))
        self.args = argparse.Namespace(base=self.base, root=self.root, bundle=self.bundle, cgroup=self.cgroup)

    def snapshot(self):
        return {str(path.relative_to(self.base)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.base.rglob("*") if path.is_file()}

    def assert_rejected(self):
        before = self.snapshot()
        with self.assertRaises((RuntimeError, ValueError, BlockingIOError)):
            tcp_stage.stage(self.args)
        self.assertFalse(self.root.exists())
        self.assertEqual(before, self.snapshot())

    def test_stage_preserves_native_bytes_and_uses_shared_lock_inode(self):
        before = self.snapshot()
        result = tcp_stage.stage(self.args)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(result["source_builds_reused"])
        self.assertEqual({path: row["sha256"] for path, row in result["native_artifacts_revalidated"].items()}, self.artifacts)
        self.assertEqual((self.root / "tcp-stage.json").read_bytes(), (self.root / "formal-tcp-001/tcp-stage.json").read_bytes())
        self.assertEqual(os.stat(self.base / "suite.lock").st_ino, os.stat(self.root / "suite.lock").st_ino)
        self.assertTrue((self.root / "datasystem").is_symlink())
        self.assertEqual((self.root / "datasystem").resolve(), self.base / "datasystem")
        self.assertEqual((self.root / "lmcache/start_server.sh").read_text(), "# distinct TCP fixture\n")
        products = {item["name"]: item for item in json.loads((self.root / "products.json").read_text())["products"]}
        self.assertEqual(products["datasystem"]["start"][-2:], ["--transport", "tcp"])
        self.assertEqual(products["mooncake"]["env"]["MC_STORE_MEMCPY"], "0")
        self.assertEqual(products["mooncake"]["env"]["MC_STORE_LOCAL_HOT_CACHE_SIZE"], "0")
        self.assertEqual(products["lmcache"]["adapter"], str(self.root / "lmcache/adapter_lmcache_tcp.py"))
        self.assertTrue(all(product["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for product in products.values()))
        self.assertEqual(json.loads((self.root / "configs/datasystem.json").read_text())["endpoints"], ["127.0.0.1:31531", "127.0.0.1:31532"])
        held = (self.base / "suite.lock").open("a")
        self.addCleanup(held.close)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        result = subprocess.run([sys.executable, "-c", code, str(self.root / "suite.lock")], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"BlockingIOError", result.stderr)

    def test_native_lock_blocks_staging_before_writes(self):
        with (self.base / "suite.lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_rejected()

    def test_existing_output_preserved(self):
        self.root.mkdir()
        sentinel = self.root / "existing"
        sentinel.write_text("do not replace")
        with self.assertRaises(RuntimeError):
            tcp_stage.stage(self.args)
        self.assertEqual(sentinel.read_text(), "do not replace")

    def test_direct_group_task_rejected(self):
        (self.cgroup / "client/cgroup.procs").write_text("123\n")
        self.assert_rejected()

    def test_parent_group_task_rejected(self):
        (self.cgroup / "cgroup.procs").write_text("123\n")
        self.assert_rejected()

    def test_descendant_group_task_rejected(self):
        child = self.cgroup / "server/child"
        child.mkdir()
        (child / "cgroup.procs").write_text("123\n")
        self.assert_rejected()

    def test_failed_native_summary_rejected(self):
        (self.base / "formal-001/summary.json").write_text(json.dumps({"ended": "yes", "failed": [{"cell": "failed"}]}))
        self.assert_rejected()

    def test_symlink_alias_cannot_stage_inside_native_root(self):
        alias = self.directory / "native-alias"
        alias.symlink_to(self.base, target_is_directory=True)
        self.args.root = alias / "tcp-child"
        with self.assertRaises((RuntimeError, ValueError)):
            tcp_stage.stage(self.args)
        self.assertFalse((self.base / "tcp-child").exists())

    def test_duplicate_product_rejected(self):
        manifest = json.loads(self.manifest.read_text())
        manifest["products"].append(manifest["products"][0])
        self.manifest.write_text(json.dumps(manifest))
        self.assert_rejected()

    def test_partial_stage_failure_removes_staging_directory(self):
        (self.base / "mooncake/wheels/fixture.whl").unlink()
        (self.base / "mooncake/wheels/original-sentinel").unlink()
        (self.base / "mooncake/wheels").rmdir()
        with self.assertRaises(FileNotFoundError):
            tcp_stage.stage(self.args)
        self.assertFalse(self.root.exists())
        self.assertEqual(list(self.directory.glob(".tcp-*")), [])

    def test_optional_mooncake_evidence_missing_keeps_original_build_log(self):
        evidence = self.base / "mooncake/.evidence"
        (evidence / "original-sentinel").unlink()
        evidence.rmdir()
        before = self.snapshot()
        result = tcp_stage.stage(self.args)
        self.assertNotIn("mooncake/.evidence", result["artifact_links"])
        self.assertFalse((self.root / "mooncake/.evidence").exists())
        original = self.base / "formal-001/build-and-script-evidence/mooncake.build.log"
        copied = self.root / "formal-tcp-001/original-build-evidence/mooncake.build.log"
        self.assertEqual(original.read_bytes(), copied.read_bytes())
        self.assertEqual(before, self.snapshot())

    def test_changed_native_artifact_rejected_and_staging_removed(self):
        artifact = Path(next(iter(self.artifacts)))
        artifact.write_bytes(b"changed-after-build")
        self.assert_rejected()
        self.assertEqual(list(self.directory.glob(".tcp-*")), [])

    def test_root_relative_generic_artifact_metadata_supported(self):
        for metadata in (self.base / "formal-001/builds").glob("*.json"):
            data = json.loads(metadata.read_text())
            rows = data["artifact_hashes"]
            if isinstance(rows, dict):
                data["artifact_hashes"] = {metadata.stem + "/" + name: value for name, value in rows.items()}
            else:
                for row in rows:
                    row["path"] = metadata.stem + "/" + row["path"]
            metadata.write_text(json.dumps(data))
        result = tcp_stage.stage(self.args)
        self.assertEqual({path: row["sha256"] for path, row in result["native_artifacts_revalidated"].items()}, self.artifacts)

    def test_exclusive_releases_lock_after_success_and_failure(self):
        tcp_stage.stage(self.args)
        for fail in [False, True]:
            if fail:
                with self.assertRaises(RuntimeError):
                    tcp_stage.stage(self.args)
            with (self.base / "suite.lock").open("a") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_retire_unstarted_renames_preserving_all_evidence(self):
        tcp_stage.stage(self.args)
        before = self.snapshot()
        staged = {name: (self.root / name).read_bytes() for name in ["tcp-stage.json", "formal-tcp-001/tcp-stage.json", "products.json", "benchmark.py"]}
        result = tcp_stage.retire_unstarted(self.args)
        destination = Path(result["retired_unstarted_stage"])
        self.assertFalse(result["deleted"])
        self.assertFalse(self.root.exists())
        self.assertTrue(destination.is_dir())
        for name, content in staged.items():
            self.assertEqual((destination / name).read_bytes(), content)
        self.assertEqual(before, self.snapshot())

    def test_retire_prepared_or_executed_stage_refused(self):
        tcp_stage.stage(self.args)
        for name in ["run", "runtime", "protocol-gate-001", "orchestration-tcp-smoke", "formal-tcp-001/events.jsonl", "formal-tcp-001/summary.json", "cgroup-settings.json"]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture execution marker")
            with self.assertRaises(RuntimeError):
                tcp_stage.retire_unstarted(self.args)
            self.assertTrue(self.root.is_dir())
            path.unlink()

    def test_retire_modified_unstarted_input_refused(self):
        tcp_stage.stage(self.args)
        (self.root / "benchmark.py").write_text("changed")
        with self.assertRaises(RuntimeError):
            tcp_stage.retire_unstarted(self.args)
        self.assertTrue(self.root.is_dir())

    def test_failed_preparation_is_not_retired_as_never_prepared(self):
        tcp_stage.stage(self.args)
        self.args.prepare_environment = self.directory / "environment"
        def partly_prepare(argv, **kwargs):
            self.args.prepare_environment.mkdir()
            (self.args.prepare_environment / "environment-before.json").write_text("{}")
        with patch.object(tcp_stage.subprocess, "run", side_effect=partly_prepare), patch.object(tcp_stage.subprocess, "check_output", side_effect=RuntimeError("fixture setup failed")):
            with self.assertRaises(RuntimeError):
                tcp_stage.prepare(self.args)
        with self.assertRaises(RuntimeError):
            tcp_stage.retire_unstarted(self.args)
        self.assertTrue(self.root.is_dir())

    def test_prepare_holds_shared_native_lock_during_both_commands(self):
        tcp_stage.stage(self.args)
        self.args.prepare_environment = self.directory / "environment"
        invoked = []
        def assert_lock(argv, **kwargs):
            with (self.base / "suite.lock").open("a") as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            invoked.append(argv)
            return '{"fixture": true}\n'
        with patch.object(tcp_stage.subprocess, "run", side_effect=assert_lock), patch.object(tcp_stage.subprocess, "check_output", side_effect=assert_lock):
            result = tcp_stage.prepare(self.args)
        self.assertTrue(result["shared_lock_held"])
        self.assertEqual(len(invoked), 2)
        self.assertIn("prepare_env.py", invoked[0][1])
        self.assertIn("cgroup.py", invoked[1][1])
        self.assertEqual(json.loads((self.root / "cgroup-settings.json").read_text()), {"fixture": True})

    def test_prepare_snapshot_exists_rejects_before_any_command(self):
        tcp_stage.stage(self.args)
        environment = self.directory / "environment"
        environment.mkdir()
        sentinel = environment / "environment-before.json"
        sentinel.write_text('{"original": true}\n')
        self.args.prepare_environment = environment
        with patch.object(tcp_stage.subprocess, "run") as run, patch.object(tcp_stage.subprocess, "check_output") as output:
            with self.assertRaises(FileExistsError):
                tcp_stage.prepare(self.args)
        run.assert_not_called()
        output.assert_not_called()
        self.assertEqual(sentinel.read_text(), '{"original": true}\n')

    def test_prepare_busy_descendant_rejects_before_any_command(self):
        tcp_stage.stage(self.args)
        self.args.prepare_environment = self.directory / "environment"
        descendant = self.cgroup / "monitor/child"
        descendant.mkdir()
        (descendant / "cgroup.procs").write_text("123\n")
        with patch.object(tcp_stage.subprocess, "run") as run, patch.object(tcp_stage.subprocess, "check_output") as output:
            with self.assertRaises(RuntimeError):
                tcp_stage.prepare(self.args)
        run.assert_not_called()
        output.assert_not_called()


class TraceTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory(prefix="kvc-tcp-trace-fixture-") as directory:
            path = Path(directory) / "trace.123"
            path.write_text(text)
            return tcp_protocol_gate.trace_bytes([path])

    def test_only_positive_tcp_data_syscalls_count(self):
        result = self.parse('write(4<TCP:[127.0.0.1:1->127.0.0.1:2]>, "", 100) = 100\n'
                            'read(4<TCP:[127.0.0.1:1->127.0.0.1:2]>, "", 80) = 80\n'
                            'write(5</tmp/log>, "", 1000000) = 1000000\n'
                            'sendto(6<UDP:[127.0.0.1:3]>, "", 1000000, 0, NULL, 0) = 1000000\n'
                            'write(4<TCP:[127.0.0.1:1->127.0.0.1:2]>, "", 10) = -1 EAGAIN (Resource temporarily unavailable)\n')
        self.assertEqual(result["successful_tcp_syscall_bytes"], 180)
        self.assertEqual(result["successful_tcp_data_calls"], 2)
        self.assertEqual(result["tcp_endpoints"], ["127.0.0.1:1->127.0.0.1:2"])

    def test_unfinished_resumed_bytes_are_counted_once(self):
        result = self.parse('write(4<TCP:[127.0.0.1:1->127.0.0.1:2]>, "", 1048576 <unfinished ...>\n'
                            '--- SIGCHLD {si_signo=SIGCHLD} ---\n'
                            '<... write resumed>) = 1048576\n')
        self.assertEqual(result["successful_tcp_syscall_bytes"], 1048576)
        self.assertEqual(result["successful_tcp_data_calls"], 1)

    def test_unfinished_resumed_retains_initial_endpoint(self):
        result = self.parse('read(4<TCP:[127.0.0.1:1->127.0.0.1:2]>,  <unfinished ...>\n'
                            '<... read resumed>"", 1048576) = 1048576\n')
        self.assertEqual(result["tcp_endpoints"], ["127.0.0.1:1->127.0.0.1:2"])

    def test_unmatched_or_incomplete_calls_are_excluded(self):
        result = self.parse('<... read resumed>"", 1048576) = 1048576\n'
                            'read(4<TCP:[127.0.0.1:1->127.0.0.1:2]>,  <unfinished ...>\n')
        self.assertEqual(result["successful_tcp_syscall_bytes"], 0)

    def test_message_count_is_not_used_as_byte_count(self):
        result = self.parse('sendmmsg(4<TCP:[127.0.0.1:1->127.0.0.1:2]>, [], 3, 0) = 3\n')
        self.assertNotEqual(result["successful_tcp_syscall_bytes"], 3)


class WorkflowTests(unittest.TestCase):
    def invoke(self, action):
        commands = []
        with tempfile.TemporaryDirectory(prefix="kvc-tcp-workflow-fixture-") as directory:
            arguments = ["tcp_workflow.py", action, "--target", "fixture.invalid", "--base", "/fixture/native",
                         "--root", "/fixture/tcp", "--local-output", directory]
            with patch.object(sys, "argv", arguments), patch.object(tcp_workflow.workflow, "run", side_effect=lambda argv: commands.append([str(v) for v in argv])):
                tcp_workflow.main()
        return commands

    def test_formal_run_checks_protocol_and_smoke_before_runner(self):
        commands = self.invoke("run")
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][0:2], ["ssh", "fixture.invalid"])
        self.assertIn("protocol-gate-001/summary.json", commands[0][2])
        self.assertIn("orchestration-tcp-smoke/summary.json", commands[0][2])
        self.assertIn("/fixture/tcp/run_suite.py", commands[1][2])
        self.assertIn("/fixture/tcp/formal-tcp-001", commands[1][2])

    def test_gate_failure_prevents_formal_runner_command(self):
        calls = []
        def fail(argv):
            calls.append(argv)
            raise subprocess.CalledProcessError(1, argv)
        with patch.object(sys, "argv", ["tcp_workflow.py", "run", "--target", "fixture.invalid"]), patch.object(tcp_workflow.workflow, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                tcp_workflow.main()
        self.assertEqual(len(calls), 1)

    def test_probe_only_invokes_separate_root_gate(self):
        commands = self.invoke("probe")
        self.assertEqual(len(commands), 1)
        self.assertIn("/fixture/tcp/tcp_protocol_gate.py", commands[0][2])
        self.assertNotIn("/fixture/native", commands[0][2])

    def test_prepare_uses_locking_stage_helper(self):
        commands = self.invoke("prepare")
        self.assertEqual(len(commands), 1)
        self.assertIn("/fixture/tcp/tcp_stage.py", commands[0][2])
        self.assertIn("--prepare-environment", commands[0][2])


class GateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kvc-tcp-gate-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "root"
        self.root.mkdir()
        self.out = self.directory / "gate"
        self.cgroup = self.directory / "cg"
        (self.cgroup / "client").mkdir(parents=True)
        self.kill = self.cgroup / "client/cgroup.kill"
        self.kill.write_text("")
        self.products = [{"name": name, "python": "fixture-python", "adapter": "fixture-adapter.py",
                          "config": "fixture-config.json", "env": {},
                          "start": ["fixture-start", name], "stop": ["fixture-stop", name]}
                         for name in ["datasystem", "mooncake", "lmcache"]]
        (self.root / "products.json").write_text(json.dumps({"products": self.products}))
        self.commands, self.stopped, self.drains = [], [], []
        self.failure = None

    def process(self, argv, **kwargs):
        case = self
        self.assertTrue(kwargs["start_new_session"])
        self.commands.append(argv)
        class FakeProcess:
            def __init__(self):
                self.returncode = None
                self.pid = 123456789
            def wait(self, timeout):
                if case.failure in {"timeout", "timeout_cleanup"} and "strace" in argv:
                    raise subprocess.TimeoutExpired(argv, timeout)
                self.returncode = 7 if ((case.failure == "start" and argv[0] == "fixture-start") or
                                        (case.failure == "client_return" and "strace" in argv)) else 0
                if "strace" in argv:
                    output = Path(argv[argv.index("--out") + 1])
                    output.write_text('{"valid":true}\n')
                    trace = Path(argv[argv.index("-o") + 1] + ".123")
                    trace.write_text('write(4<TCP:[127.0.0.1:1->127.0.0.1:2]>, "", 4194304) = 4194304\n'
                                     'read(4<TCP:[127.0.0.1:1->127.0.0.1:2]>, "", 4194304) = 4194304\n')
                if any(str(value).endswith("tcp_probe_ds_sizes.py") for value in argv):
                    output = Path(argv[argv.index("--out") + 1])
                    cases = []
                    for worker in [0, 1]:
                        for size in [1024, 65536, 1048576]:
                            key = f"fixture-worker{worker}-size{size}"
                            anchors = {operation: [{"operation": operation, "keys": [key], "actual_medium": "TCP", "code": 0, "data_size": size}]
                                       for operation in ["DS_KV_CLIENT_SET", "DS_KV_CLIENT_GET"]}
                            cases.append({"worker_index": worker, "size": size, "key": key,
                                          "bound_endpoint": f"127.0.0.1:{31531+worker}", "passed": True,
                                          "actual_media": ["TCP"], "actual_medium_anchors": anchors,
                                          "returncode": 0, "timed_out": False, "child": {"correctness": True}})
                    output.write_text(json.dumps({"scope": "local fixture only; no runtime evidence", "passed": case.failure != "medium", "cases": cases}))
                return self.returncode
        return FakeProcess()

    def stop(self, process):
        self.stopped.append(process.pid)
        process.returncode = -15

    def drain(self, group, roles=None):
        self.drains.append({"group": str(group), "roles": roles})
        if self.failure == "timeout_cleanup" and roles == ["client"]:
            raise RuntimeError("fixture failed-client cleanup failed")
        if self.failure == "drain" and len(self.drains) >= 2:
            raise RuntimeError("fixture task remains")
        return {"members": {}, "populated": []}

    def invoke(self):
        def local_path(value, *parts):
            path = Path(value, *parts)
            return self.cgroup if path == Path("/sys/fs/cgroup/kvcfair20261020") else path
        with patch.object(sys, "argv", ["tcp_protocol_gate.py", "--root", str(self.root), "--out", str(self.out)]), \
             patch.object(tcp_protocol_gate.subprocess, "Popen", side_effect=self.process), \
             patch.object(tcp_protocol_gate.subprocess, "check_output", return_value="strace fixture\n"), \
             patch.object(tcp_protocol_gate, "stop_process", side_effect=self.stop), \
             patch.object(tcp_protocol_gate, "drain", side_effect=self.drain), \
             patch.object(tcp_protocol_gate, "Path", side_effect=local_path), patch("builtins.print"):
            tcp_protocol_gate.main()

    def ledger(self):
        return [json.loads(line) for line in (self.out / "operations.jsonl").read_text().splitlines()]

    def test_all_three_products_two_clients_and_lifecycle_records(self):
        self.invoke()
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertTrue(summary["valid"])
        self.assertEqual(len(summary["products"]), 3)
        for product in summary["products"]:
            self.assertEqual([row["client_index"] for row in product["clients"]], [0, 1])
            self.assertTrue(all(row["successful_tcp_syscall_bytes"] == 8 << 20 for row in product["clients"]))
            for row in product["clients"]:
                for name, checksum in row["trace_sha256"].items():
                    trace = self.out / product["product"] / name
                    self.assertEqual(checksum, hashlib.sha256(trace.read_bytes()).hexdigest())
        evidence = summary["products"][0]["actual_medium_evidence"]
        self.assertEqual(evidence["path"], "datasystem-medium.json")
        self.assertEqual(evidence["sha256"], hashlib.sha256((self.out / evidence["path"]).read_bytes()).hexdigest())
        self.assertEqual(len(self.ledger()), 16)
        self.assertEqual(len(self.drains), 5)
        self.assertEqual(self.stopped, [])

    def test_start_failure_still_stops_and_preserves_terminal_summary(self):
        self.failure = "start"
        with self.assertRaises(RuntimeError):
            self.invoke()
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertFalse(summary["valid"])
        self.assertIn("fatal", summary)
        self.assertIn("end_wall_ns", summary)
        self.assertEqual([row["argv"][0] for row in self.ledger()], ["fixture-stop", "fixture-start", "fixture-stop"])
        self.assertEqual(self.ledger()[1]["returncode"], 7)
        self.assertEqual(len(self.drains), 3)

    def test_timeout_stops_own_process_and_records_error_and_cleanup(self):
        self.failure = "timeout"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.invoke()
        self.assertEqual(self.stopped, [123456789])
        ledger = self.ledger()
        self.assertIn("TimeoutExpired", ledger[-2]["error"])
        self.assertEqual(ledger[-2]["returncode"], -15)
        self.assertEqual(ledger[-1]["argv"], ["fixture-stop", "datasystem"])
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertFalse(summary["valid"])
        self.assertIn("TimeoutExpired", summary["fatal"])
        self.assertEqual(self.kill.read_text(), "1")
        self.assertIsNotNone(ledger[-2]["failed_client_cgroup_cleanup"])

    def test_failed_client_return_uses_task_cgroup_kill_and_retains_ledger(self):
        self.failure = "client_return"
        with self.assertRaises(RuntimeError):
            self.invoke()
        ledger = self.ledger()
        self.assertEqual(self.kill.read_text(), "1")
        self.assertEqual(ledger[-2]["returncode"], 7)
        self.assertIsNotNone(ledger[-2]["failed_client_cgroup_cleanup"])
        self.assertEqual(ledger[-1]["argv"], ["fixture-stop", "datasystem"])
        self.assertFalse(json.loads((self.out / "summary.json").read_text())["valid"])

    def test_failed_client_cleanup_error_still_preserves_original_command_ledger(self):
        self.failure = "timeout_cleanup"
        with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
            self.invoke()
        ledger = self.ledger()
        matching = [row for row in ledger if "strace" in row["argv"]]
        self.assertEqual(len(matching), 1, "cleanup errors must not erase the failed client command ledger")
        self.assertIn("TimeoutExpired", matching[0]["error"])
        self.assertIn("fixture failed-client cleanup failed", json.dumps(matching[0]))
        self.assertFalse(json.loads((self.out / "summary.json").read_text())["valid"])

    def test_datasystem_medium_failure_stops_before_other_products(self):
        self.failure = "medium"
        with self.assertRaises(RuntimeError):
            self.invoke()
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertFalse(summary["valid"])
        self.assertEqual(len(summary["products"]), 1)
        self.assertEqual(self.ledger()[-1]["argv"], ["fixture-stop", "datasystem"])

    def test_drain_failure_is_preserved_and_cannot_mark_valid(self):
        self.failure = "drain"
        with self.assertRaises(RuntimeError):
            self.invoke()
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertFalse(summary["valid"])
        self.assertIn("fixture task remains", summary["cgroup_drain_error"])
        self.assertIn("end_wall_ns", summary)

    def test_duplicate_products_fail_before_service_commands(self):
        self.products[2] = self.products[0]
        (self.root / "products.json").write_text(json.dumps({"products": self.products}))
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual(self.commands, [])
        self.assertFalse(json.loads((self.out / "summary.json").read_text())["valid"])


if __name__ == "__main__":
    unittest.main()
