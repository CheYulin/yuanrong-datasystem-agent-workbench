#!/usr/bin/env python3
"""Start and stop only this experiment's isolated two-worker DataSystem cluster."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time


def start_ticks(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def wait_port(port, process, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited during startup: pid={process.pid}, code={process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"Service port did not open: {port}")


def wait_ready(path, process, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Worker exited before readiness: pid={process.pid}, code={process.returncode}")
        if path.is_file():
            return
        time.sleep(0.2)
    raise TimeoutError(f"Worker did not become ready: {path}")


def launch(args):
    root = Path(args.task_root).resolve()
    source = root / "datasystem"
    run_dir = Path(args.run_dir).resolve() if args.run_dir else root / "runtime" / "ds"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = run_dir / "processes.json"
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text())
        if any(start_ticks(p["pid"]) == p["start_ticks"] for p in manifest["processes"]):
            raise RuntimeError("Experiment cluster is already running; stop it before restarting")
    for port in [args.etcd_port, args.etcd_port + 1, args.worker_port, args.worker_port + 1]:
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(("127.0.0.1", port))
    etcd_binary = Path("/usr/local/bin/etcd")
    etcd_hash = hashlib.sha256()
    with etcd_binary.open("rb") as data:
        for chunk in iter(lambda: data.read(4 << 20), b""):
            etcd_hash.update(chunk)
    manifest = {"source": str(source), "run_dir": str(run_dir), "cgroup": args.cgroup, "processes": [],
                "etcd_version": subprocess.check_output([str(etcd_binary), "--version"], text=True).strip(),
                "etcd_sha256": etcd_hash.hexdigest()}

    def preexec():
        if args.cgroup:
            Path(args.cgroup, "cgroup.procs").write_text(str(os.getpid()))

    def spawn(name, command, env=None):
        out = open(run_dir / f"{name}.stdout.log", "ab", buffering=0)
        proc = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, cwd=run_dir,
                                env=env, start_new_session=True, preexec_fn=preexec)
        out.close()
        manifest["processes"].append({"name": name, "pid": proc.pid, "start_ticks": start_ticks(proc.pid),
                                      "command": command})
        manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")
        return proc

    etcd_command = [str(etcd_binary), "--name", "kvc-fair-ds", "--data-dir", str(run_dir / "etcd-data"),
                    "--listen-client-urls", f"http://127.0.0.1:{args.etcd_port}",
                    "--advertise-client-urls", f"http://127.0.0.1:{args.etcd_port}",
                    "--listen-peer-urls", f"http://127.0.0.1:{args.etcd_port + 1}",
                    "--initial-advertise-peer-urls", f"http://127.0.0.1:{args.etcd_port + 1}",
                    "--initial-cluster", f"kvc-fair-ds=http://127.0.0.1:{args.etcd_port + 1}",
                    "--initial-cluster-token", "kvc-fair-ds-20261020"]
    etcd = spawn("etcd", etcd_command)
    wait_port(args.etcd_port, etcd)
    env = os.environ.copy()
    env["KVC_FAIR_HOST_ID"] = "kvc-fair-host-20261020"
    env["LD_LIBRARY_PATH"] = str(source / "output" / "datasystem" / "service" / "lib") + ":" + env.get("LD_LIBRARY_PATH", "")
    workers = []
    for index in range(2):
        instance = run_dir / f"w{index}"
        logs = instance / "logs"
        instance_id = hashlib.sha256(str(instance).encode()).hexdigest()[:10]
        uds = root / "u" / ("ds" + instance_id)
        logs.mkdir(parents=True, exist_ok=True)
        uds.mkdir(parents=True, exist_ok=True)
        (instance / "ready").unlink(missing_ok=True)
        (instance / "healthy").unlink(missing_ok=True)
        config = {
            "worker_address": f"127.0.0.1:{args.worker_port + index}",
            "etcd_address": f"127.0.0.1:{args.etcd_port}",
            "coordinator_address": "",
            "cluster_name": "kvc-fair-ds-20261020",
            "host_id_env_name": "KVC_FAIR_HOST_ID",
            "shared_memory_size_mb": str(args.memory_mb_per_worker),
            "l2_cache_type": "none",
            "spill_directory": "",
            "spill_size_limit": "0",
            "log_dir": str(logs),
            "ready_check_path": str(instance / "ready"),
            "health_check_path": str(instance / "healthy"),
            "unix_domain_socket_dir": str(uds),
            "enable_urma": "false",
            "enable_rdma": "false",
            "oc_thread_num": "8",
            "brpc_server_num_threads": "8",
            "brpc_max_concurrency": "128",
            "log_monitor": "false",
            "minloglevel": "2",
        }
        flagfile = instance / "worker.flags"
        flagfile.write_text("".join(f"--{key}={value}\n" for key, value in config.items()))
        command = [str(source / "output" / "datasystem" / "service" / "datasystem_worker")]
        command.extend(f"--{key}={value}" for key, value in config.items())
        worker = spawn(f"worker{index}", command, env)
        workers.append(worker)
        wait_port(args.worker_port + index, worker)
        wait_ready(instance / "ready", worker)
    manifest["endpoints"] = [f"127.0.0.1:{args.worker_port + i}" for i in range(2)]
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


def stop(args):
    root = Path(args.task_root).resolve()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else root / "runtime" / "ds"
    manifest_file = run_dir / "processes.json"
    if not manifest_file.exists():
        return
    manifest = json.loads(manifest_file.read_text())
    results = []
    for process in reversed(manifest["processes"]):
        pid = process["pid"]
        try:
            pidfd = os.pidfd_open(pid)
        except ProcessLookupError:
            results.append({"pid": pid, "result": "already_stopped_or_reused"})
            continue
        try:
            if start_ticks(pid) != process["start_ticks"]:
                results.append({"pid": pid, "result": "already_stopped_or_reused"})
                continue
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGTERM)
            except ProcessLookupError:
                results.append({"pid": pid, "result": "already_stopped_or_reused"})
                continue
            deadline = time.monotonic() + args.stop_timeout
            while start_ticks(pid) == process["start_ticks"] and time.monotonic() < deadline:
                time.sleep(0.1)
            if start_ticks(pid) == process["start_ticks"]:
                try:
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                kill_deadline = time.monotonic() + 5
                while start_ticks(pid) == process["start_ticks"] and time.monotonic() < kill_deadline:
                    time.sleep(0.05)
                if start_ticks(pid) == process["start_ticks"]:
                    raise RuntimeError(f"Experiment service did not exit after SIGKILL: {pid}")
                results.append({"pid": pid, "result": "killed_after_grace"})
            else:
                results.append({"pid": pid, "result": "terminated"})
        finally:
            os.close(pidfd)
    (run_dir / "stop-results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("--task-root", default="/home/worktrees/kvc-fair-20261020")
    parser.add_argument("--run-dir")
    parser.add_argument("--cgroup")
    parser.add_argument("--worker-port", type=int, default=31531)
    parser.add_argument("--etcd-port", type=int, default=32379)
    parser.add_argument("--memory-mb-per-worker", type=int, default=4096)
    parser.add_argument("--stop-timeout", type=float, default=15)
    args = parser.parse_args()
    (launch if args.action == "start" else stop)(args)
