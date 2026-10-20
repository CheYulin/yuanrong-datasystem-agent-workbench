#!/usr/bin/env python3
"""Negative cleanup guard: mismatched process identity must survive teardown."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kvcbench.run.products.datasystem import runtime


parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)


def verify_transport_flags():
    defaults = runtime.parse_args(["start"])
    assert defaults.transport == "native" and defaults.min_log_level == 2
    for options in [["--transport", "invalid"], ["--min-log-level", "4"]]:
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                runtime.parse_args(["start", *options])
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError("Invalid runtime option was accepted")
    original_open = Path.open

    def open_fixture(path, *positional, **keyword):
        if path == Path("/usr/local/bin/etcd"):
            return io.BytesIO(b"fixture-etcd-binary")
        return original_open(path, *positional, **keyword)

    with tempfile.TemporaryDirectory(dir=args.out) as temporary:
        root = Path(temporary)
        captures = 0

        def capture(options):
            nonlocal captures
            captures += 1
            run_dir = root / f"run-{captures}"
            processes = []

            def spawn(command, **keyword):
                processes.append(command)
                return SimpleNamespace(pid=900000 + len(processes), poll=lambda: None)

            parsed = runtime.parse_args(["start", "--task-root", str(root),
                                         "--run-dir", str(run_dir), *options])
            with mock.patch.object(Path, "open", open_fixture), \
                    mock.patch.object(runtime.socket, "socket"), \
                    mock.patch.object(runtime.subprocess, "Popen", side_effect=spawn), \
                    mock.patch.object(runtime.subprocess, "check_output", return_value="fixture-etcd-version"), \
                    mock.patch.object(runtime, "start_ticks", return_value="fixture-start-ticks"), \
                    mock.patch.object(runtime, "wait_port"), mock.patch.object(runtime, "wait_ready"), \
                    contextlib.redirect_stdout(io.StringIO()):
                runtime.launch(parsed)
            manifest = json.loads((run_dir / "processes.json").read_text())
            flags = [(run_dir / f"w{i}/worker.flags").read_bytes() for i in range(2)]
            assert [p["command"] for p in manifest["processes"]] == processes
            assert len(processes) == 3
            for index, content in enumerate(flags):
                assert content == ("\n".join(processes[index + 1][1:]) + "\n").encode()
                assert f"--rocksdb_store_dir={run_dir}/w{index}/rocksdb\n".encode() in content
            assert len(set(manifest["rocksdb_store_dirs"].values())) == 2
            def normalized(value):
                value = value.replace(str(run_dir), "<fresh-run>")
                return re.sub(re.escape(str(root / "u")) + r"/ds[0-9a-f]{10}", "<instance-uds>", value)
            return [[normalized(value) for value in command] for command in processes], [normalized(content.decode()).encode() for content in flags]

        default_commands, default_flags = capture([])
        native_commands, native_flags = capture(["--transport", "native"])
        assert native_commands == default_commands and native_flags == default_flags
        tcp_commands, tcp_flags = capture(["--transport", "tcp"])
        disabled = "--ipc_through_shared_memory=false"
        assert tcp_commands[0] == native_commands[0]
        for index in range(1, 3):
            assert tcp_commands[index].count(disabled) == 1
            assert [option for option in tcp_commands[index] if option != disabled] == native_commands[index]
            assert disabled not in native_commands[index]
            assert "--enable_urma=false" in tcp_commands[index]
            assert "--enable_rdma=false" in tcp_commands[index]
            assert "--minloglevel=2" in tcp_commands[index]
            assert tcp_flags[index - 1] == native_flags[index - 1] + (disabled + "\n").encode()
        smoke_commands, _ = capture(["--transport", "tcp", "--min-log-level", "0"])
        for index in range(1, 3):
            assert [option.replace("--minloglevel=0", "--minloglevel=2")
                    for option in smoke_commands[index]] == tcp_commands[index]
    return {"default_is_native_error_logging": True, "native_unchanged": True,
            "fresh_per_worker_rocksdb_paths": True,
            "tcp_disables_both_workers_shm_only": True, "smoke_logging_is_opt_in": True,
            "invalid_options_rejected": True, "product_processes_started": 0}


transport_result = verify_transport_flags()
owned = subprocess.Popen(["sleep", "30"])
protected = subprocess.Popen(["sleep", "30"])
try:
    manifest = {"processes": [
        {"pid": protected.pid, "start_ticks": "deliberate-mismatch", "name": "must_survive"},
        {"pid": owned.pid, "start_ticks": runtime.start_ticks(owned.pid), "name": "owned"},
    ]}
    (args.out / "processes.json").write_text(json.dumps(manifest))
    runtime.stop(SimpleNamespace(task_root=str(args.out.parent), run_dir=str(args.out), stop_timeout=0.5))
    owned.wait(timeout=5)
    assert protected.poll() is None, "Mismatched identity process was signalled"
    results = json.loads((args.out / "stop-results.json").read_text())
    assert next(r for r in results if r["pid"] == protected.pid)["result"] == "already_stopped_or_reused"
    (args.out / "safety-result.json").write_text(json.dumps({"passed": True,
        "owned_process_terminated": owned.returncode < 0,
        "mismatched_identity_process_survived": True, "transport_flags": transport_result}, indent=2) + "\n")
    print("Exact-target cleanup safety passed")
finally:
    for process in [owned, protected]:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
