# Historical 2026-10-20 summary collector; not called by the source-build service.
#!/usr/bin/env python3
import datetime
import hashlib
import json
from pathlib import Path
import platform
import subprocess

root = Path(__file__).resolve().parent


def utc(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat()


def birth(path):
    return int(subprocess.check_output(["stat", "-c", "%W", str(path)], text=True))


def artifact(path, scope):
    return {"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size, "scope": scope}


def timestamp(filename, fallback):
    path = root / filename
    return datetime.datetime.fromisoformat(path.read_text().strip().replace("Z", "+00:00")).timestamp() if path.exists() else fallback


started = timestamp("source-build-started.utc", birth(root / "build.log"))
finished = timestamp("build-finished.utc", (root / "build.log").stat().st_mtime)
dependency_start = timestamp("dependency-started.utc", birth(root / "clone.log") if (root / "clone.log").exists() else started)
smoke = json.loads(next(line for line in (root / "smoke.log").read_text().splitlines() if line.startswith('{"ok"')))
output = {
    "product": "Mooncake Store", "tag": "v0.3.13.post1",
    "sha": subprocess.check_output(["git", "-C", str(root / "source"), "rev-parse", "HEAD"], text=True).strip(),
    "python": platform.python_version(),
    "python_executable": str(root / "venv/bin/python"),
    "compiler": subprocess.check_output(["/opt/rh/gcc-toolset-15/root/usr/bin/g++", "--version"], text=True).splitlines()[0],
    "build_started_utc": utc(started), "build_finished_utc": utc(finished),
    "build_exit": int((root / "build.exit").read_text()),
    "smoke_exit": int((root / "smoke.exit").read_text()),
    "stop_exit": int((root / "smoke-stop.exit").read_text()),
    "timing_scope": {
        "own_compile_including_failed_configuration_attempt_s": finished - started,
        "dependency_download_install_and_all_compile_s": finished - dependency_start,
        "dependency_download_install_start_utc": utc(dependency_start),
        "timestamp_method": ("explicit build.sh UTC timestamps (~1s precision); source build window includes wheel install and artifact-lock capture"
                             if (root / "source-build-started.utc").exists() else
                             "initial build.log filesystem birth time (~1s precision), final successful pip log mtime; dependency total starts at clone.log birth; includes shell/setup intervals and one failed USE_HTTP=OFF attempt"),
        "failed_attempt_exit": int((root / "build-use-http-off.exit").read_text()) if (root / "build-use-http-off.exit").exists() else None,
        "successful_attempt_configuration": "USE_HTTP=ON; runtime P2PHANDSHAKE and HTTP metadata server disabled",
    },
    "artifact_hashes": [artifact(p, "Mooncake compiled from pinned tag source") for p in (root / "wheels").glob("*.whl")]
                       + [artifact(root / "deps/lib/libxxhash.so.0.8.3", "private dependency xxHash0.8.3 compiled from source")]
                       + [artifact(root / p, "dependency/configuration lock") for p in ["requirements.lock", "requirements-installed.lock", "os-packages.lock"]],
    "script_hashes": [artifact(path, "reproduction script revision; does not change original build/tool evidence")
                      for path in sorted([*root.glob("*.sh"), *root.glob("*.py")])],
    "source_worktree_clean": not subprocess.check_output(["git", "-C", str(root / "source"), "status", "--porcelain"], text=True).strip(),
    "launch_contract": {
        "start": ["bash", str(root / "start.sh")],
        "stop": ["bash", str(root / "stop.sh")],
        "start_cgroup": "server (all descendant master/store-peer processes inherit)",
        "environment_script": str(root / "env.sh"),
        "adapter": str(root / "adapter.py"),
        "adapter_factory": "new_adapter(config)",
        "adapter_methods": ["put", "get", "exists", "delete", "close"],
        "adapter_config": {"master_server_addr": "127.0.0.1:18551", "local_buffer_size": 134217728},
        "store_processes": 2, "store_capacity_bytes": 8589934592,
        "master_rpc_threads": 4, "data_protocol": "tcp", "memory_replicas": 1,
    },
    "smoke": smoke,
    "common_harness_smoke": json.loads((root / "common-smoke/summary.json").read_text()) if (root / "common-smoke/summary.json").exists() else None,
    "pid_reuse_guard_selftest": json.loads((root / "pid-guard.log").read_text()) if (root / "pid-guard.log").exists() else None,
    "limitations": ["single-node CPU/TCP only", "no RDMA/GPU/LLM or recovery experiment", "wheel internal package version0.3.12.post1 differs from source release tag", "optional io_uring disabled because liburing-devel unavailable", "startup emits Local segment descriptor not found once; setup and smoke return0"],
}
(root / "build-summary.json").write_text(json.dumps(output, indent=2))
print(json.dumps({k: output[k] for k in ["product", "tag", "sha", "build_exit", "smoke_exit", "source_worktree_clean"]}))
