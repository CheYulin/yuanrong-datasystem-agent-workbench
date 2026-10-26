"""Native product lifecycle and SDK cases behind the fixed remote control protocol."""
import json
import math
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

from .remote_support import atomic, digest, member, read, setup_groups, terminate, ticks
from .remote_observe import Monitor
from .acceptance import reconcile
from .artifacts import manifest


def latency_us(raw, name):
    value = raw.get(name)
    if isinstance(value, dict):
        value = value.get('upper_us')
    return value if type(value) in (float, int) and math.isfinite(value) else None


class Cancelled(RuntimeError):
    pass


def workload_sizes(config, size):
    pools, puts = [], {}
    for batch in config["batches"]:
        count = batch * config["clients"]
        key_budget = max(count, int(config["memory_gib"] * (1 << 30) * .02 / size))
        pools.append(count * max(1, min(65536 // count, key_budget // count)))
        puts[batch] = count * max(1, min(262144 // count, key_budget // count))
    pool = max(pools)
    if any(pool % (batch * config["clients"]) for batch in config["batches"]):
        raise ValueError("Common pool must cover complete batches for every selected cardinality")
    return pool, puts


class Engine:
    def __init__(self, settings, request, emit):
        self.settings, self.request, self.emit = settings, request, emit
        self.state = Path(settings["state_root"])
        self.job = self.state / "jobs" / request["job_id"]
        self.job.mkdir(parents=True, exist_ok=False)
        self.events = (self.job / "events.jsonl").open("x", buffering=1)
        self.config = request.get("config", {})
        self.native = Path(settings["native_root"])
        self.lab = Path(__file__).resolve().parent.parent
        self.fair = Path(settings["fair_root"])
        self.deployment = read(self.state / "deployment.json", {})
        self.commands = []
        self.results = []
        self.sequence = 0
        self.variant = "baseline"
        self.profile = self.config.get("profile", "baseline")
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0",
                        OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                        DATASYSTEM_ENABLE_LOAD_AWARE_SCHEDULER="false", DATASYSTEM_MIN_LOG_LEVEL="2",
                        DATASYSTEM_LOG_MONITOR_ENABLE="false", GLOG_minloglevel="2", MC_LOG_LEVEL="ERROR",
                        MC_STORE_MEMCPY="0", MC_STORE_LOCAL_HOT_CACHE_SIZE="0", MOONCAKE_STORE_CHECKSUM="false")
        self.env["PYTHONPATH"] = str(self.lab) + os.pathsep + str(self.fair)
        self.env["LD_LIBRARY_PATH"] = str(self.native / "mooncake/deps/lib") + os.pathsep + str(self.native / "datasystem/output/datasystem/service/lib") + os.pathsep + self.env.get("LD_LIBRARY_PATH", "")
        self.env.update(MC_STORE_CLIENT_METRIC="0", MC_STORE_CLIENT_METRIC_INTERVAL="0", MC_TE_METRIC="0", MC_RPC_PROTOCOL="tcp",
                        MC_TCP_LANES_PER_PEER="4", MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER="4096",
                        MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER="4096", MC_TCP_ADMISSION_TIMEOUT_MS="1000")

    def event(self, kind, **details):
        value = {"kind": kind, "job_id": self.request["job_id"], "wall_ns": time.time_ns(),
                 "mono_ns": time.monotonic_ns(), **details}
        self.events.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.emit(value)

    def cancelled(self):
        return (self.job / "cancel.json").exists()

    def check_cancel(self):
        if self.cancelled():
            raise Cancelled("Cancellation requested by the owning queue")

    def artifact(self, path):
        return str(Path(path).relative_to(self.job))

    def step(self, name, begin, status, artifact, **details):
        self.event("step", name=name, status=status, duration_s=(time.monotonic_ns() - begin) / 1e9,
                   artifact=self.artifact(artifact), **details)

    def persist(self):
        atomic(self.state / "deployment.json", self.deployment)
        atomic(self.job / "deployment.json", self.deployment)

    def spawn(self, name, argv, group, directory, env=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / (name + ".log")
        command = [sys.executable, "-B", "-m", "kvclab.remote_exec", "--group", str(group), "--", *map(str, argv)]
        with log.open("ab", buffering=0) as output:
            proc = subprocess.Popen(command, cwd=self.lab, env=env or self.env, stdout=output,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        identity = {"name": name, "pid": proc.pid, "start_ticks": ticks(proc.pid), "group": str(group),
                    "argv": list(map(str, argv)), "log": str(log), "started_wall_ns": time.time_ns()}
        self.deployment.setdefault("processes", []).append(identity)
        self.persist()
        self.commands.append(identity)
        atomic(self.job / "commands.json", self.commands)
        return proc, identity

    def command(self, name, argv, group, directory, timeout=1200, env=None, check=True):
        begin = time.monotonic_ns()
        proc, identity = self.spawn(name, argv, group, directory, env)
        try:
            while proc.poll() is None:
                self.check_cancel()
                if (time.monotonic_ns() - begin) / 1e9 > timeout:
                    raise TimeoutError("Command exceeded its admitted timeout: " + name)
                time.sleep(.2)
        except BaseException:
            terminate(identity, group)
            proc.wait(timeout=5)
            raise
        finally:
            identity.update(returncode=proc.poll(), duration_s=(time.monotonic_ns() - begin) / 1e9)
            atomic(Path(directory) / (name + "-command.json"), identity)
            self.persist()
        if check and proc.returncode:
            raise RuntimeError(f"{name} exited {proc.returncode}; inspect {identity['log']}")
        return proc.returncode

    def wait(self, host, port, process, ready=None):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            self.check_cancel()
            if process.poll() is not None:
                raise RuntimeError("Native service exited before ready: " + str(process.returncode))
            try:
                with socket.create_connection((host, port), timeout=.2):
                    if ready is None or Path(ready).is_file():
                        return
            except OSError:
                pass
            time.sleep(.2)
        raise TimeoutError("Service did not become ready")

    def stop(self):
        begin = time.monotonic_ns()
        deployment = self.deployment
        root = Path(deployment.get("cgroup", "/nonexistent"))
        allowed = Path(self.settings.get("cgroup_root", "/sys/fs/cgroup/kvc-lab"))
        if root == Path("/nonexistent"):
            return
        if root == allowed or allowed not in root.parents or any(p.is_symlink() for p in [root, *root.parents]):
            raise ValueError("Deployment cgroup is outside the owned prefix")
        outcomes = []
        for identity in reversed(deployment.get("processes", [])):
            group = Path(identity["group"])
            if root != group and root not in group.parents:
                raise ValueError("Process cgroup outside deployment")
            outcomes.append(terminate(identity, group))
        # Descendants can outlive their parent briefly; populated is propagated asynchronously.
        deadline = time.monotonic() + 10
        settle_start = time.monotonic_ns()
        while True:
            for group in sorted(root.rglob("cgroup.procs"), reverse=True) if root.exists() else []:
                for pid_text in group.read_text().split():
                    pid = int(pid_text)
                    identity = {"pid": pid, "start_ticks": ticks(pid)}
                    if identity["start_ticks"] is not None:
                        outcomes.append(terminate(identity, group.parent))
            populated = (root / "cgroup.events").read_text().splitlines() if root.exists() else []
            if not root.exists() or "populated 0" in populated:
                break
            if time.monotonic() >= deadline:
                path = self.job / f"stop-{self.sequence:03}.json"
                atomic(path, {"outcomes": outcomes, "cgroup_events": populated, "valid": False,
                              "settle_duration_s": (time.monotonic_ns() - settle_start) / 1e9,
                              "error": "Owned deployment domain did not become empty within 10 seconds"})
                self.step("stop_owned_deployment", begin, "failed", path)
                raise RuntimeError("Owned deployment domain is still populated after bounded cleanup wait")
            time.sleep(.05)
        deployment.update(active=False, stopped_wall_ns=time.time_ns(), cleanup=outcomes)
        self.persist()
        path = self.job / f"stop-{self.sequence:03}.json"
        atomic(path, {"outcomes": outcomes, "cgroup_events": populated, "valid": True,
                      "settle_duration_s": (time.monotonic_ns() - settle_start) / 1e9})
        self.step("stop_owned_deployment", begin, "passed", path)

    def deploy(self, product):
        self.check_cancel()
        self.stop()
        self.sequence += 1
        begin = time.monotonic_ns()
        directory = self.job / f"v-{self.variant}" / f"{self.sequence:03}-{product}"
        directory.mkdir(parents=True)
        group = Path(self.settings.get("cgroup_root", "/sys/fs/cgroup/kvc-lab")) / (self.request["job_id"] + "-" + str(self.sequence) + "-" + uuid.uuid4().hex[:8])
        groups = setup_groups(group, self.config)
        self.deployment = {"job_id": self.request["job_id"], "product": product, "active": False,
                           "config": self.config, "variant_id": self.variant, "profile": self.profile,
                           "cgroup": str(group), "resource_groups": groups, "directory": str(directory),
                           "processes": [], "logical_nodes": self.config["nodes"],
                           "workers": self.config["nodes"] * self.config["workers_per_node"],
                           "topology_scope": "single physical host; distinct loopback endpoints and DataSystem host identities, shared kernel/network"}
        self.persist()
        ports = [("127.0.0.1", 32511)] if product == "datasystem" else [("127.0.0.1", 28551), ("127.0.0.1", 28553)]
        for index in range(self.deployment["workers"]):
            node = index // self.config["workers_per_node"]
            host = f"127.70.{node + 1}.1" if product == "datasystem" else f"127.71.{node + 1}.{index % self.config['workers_per_node'] + 1}"
            ports.append((host, (32531 if product == "datasystem" else 29001) + index))
        for host, port in ports:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((host, port))
        if product == "datasystem":
            self.start_datasystem(directory, group)
        else:
            self.start_mooncake(directory, group)
        self.deployment["active"] = True
        self.persist()
        atomic(directory / "resource-limits.json", groups)
        self.step("deploy_" + product, begin, "passed", directory / "resource-limits.json")
        self.event("deployment", deployment=self.deployment)
        return directory, group

    def start_datasystem(self, directory, group):
        source = self.native / "datasystem"
        lib = source / "output/datasystem/service/lib"
        env = dict(self.env, LD_LIBRARY_PATH=str(lib) + os.pathsep + self.env.get("LD_LIBRARY_PATH", ""))
        coordinator = source / "output/datasystem/service/datasystem_coordinator"
        flags = {"coordinator_address": "127.0.0.1:32511", "coordinator_raft_initial_peers": "",
                 "coordinator_raft_data_dir": str(directory / "coordinator-state"), "log_dir": str(directory / "coordinator-logs"),
                 "minloglevel": "2", "log_monitor": "false", "json_log_monitor": "false",
                 "request_sample_rate": "0", "access_sample_rate": "0", "diagnostic_sample_rate": "0",
                 "rpc_thread_num": "8", "brpc_server_num_threads": "8", "brpc_max_concurrency": "1024", "watch_event_dispatch_thread": "4"}
        atomic(directory / "coordinator-config.json", flags)
        proc, _ = self.spawn("coordinator", [coordinator, *[f"--{k}={v}" for k, v in flags.items()]], group / "server/master", directory, env)
        self.wait("127.0.0.1", 32511, proc)
        endpoints = []
        for index in range(self.deployment["workers"]):
            node = index // self.config["workers_per_node"]
            host, port = f"127.70.{node + 1}.1", 32531 + index
            worker = directory / f"worker-{index}"
            worker.mkdir()
            (worker / "logs").mkdir()
            uds = self.state / "u" / hashlib.sha256(str(worker).encode()).hexdigest()[:16]
            uds.mkdir(parents=True, exist_ok=False)
            rocksdb = worker / "rocksdb-disabled"
            rocksdb.mkdir()
            node_env = dict(env, KVC_LAB_NODE_ID=f"kvc-lab-{self.request['job_id']}-node-{node}")
            memory_mb = max(128, int(self.config["memory_gib"] * 1024 * .60 / self.deployment["workers"]))
            flags = {"worker_address": f"{host}:{port}", "coordinator_address": "127.0.0.1:32511", "etcd_address": "",
                     "cluster_name": "kvc-lab", "host_id_env_name": "KVC_LAB_NODE_ID", "shared_memory_size_mb": str(memory_mb),
                     "rocksdb_store_dir": str(rocksdb), "rocksdb_write_mode": "none", "l2_cache_type": "none", "spill_directory": "",
                     "spill_size_limit": "0", "log_dir": str(worker / "logs"), "ready_check_path": str(worker / "ready"),
                     "health_check_path": str(worker / "healthy"), "unix_domain_socket_dir": str(uds),
                     "enable_urma": "false", "enable_rdma": "false", "ipc_through_shared_memory": "false",
                     "oc_thread_num": "8", "brpc_server_num_threads": "8", "brpc_max_concurrency": "1024",
                     "log_monitor": "false", "minloglevel": "2", "sc_regular_socket_num": "0", "sc_stream_socket_num": "0",
                     "json_log_monitor": "false", "enable_perf_trace_log": "false", "request_sample_rate": "0", "access_sample_rate": "0"}
            atomic(worker / "effective-config.json", {"flags": flags, "node_identity": node_env["KVC_LAB_NODE_ID"]})
            proc, _ = self.spawn("worker-" + str(index), [source / "output/datasystem/service/datasystem_worker", *[f"--{k}={v}" for k, v in flags.items()]], group / "server/workers", directory, node_env)
            self.wait(host, port, proc, worker / "ready")
            endpoints.append(f"{host}:{port}")
        self.deployment.update(endpoints=endpoints, metadata_backend="coordinator", placement_policy="PREFERRED_META_OWNER",
                               actual_master_sha256=digest(coordinator), python=str(source / ".venv/bin/python"))

    def start_mooncake(self, directory, group):
        root = self.native / "mooncake"
        python = root / "venv/bin/python"
        master = root / "venv/lib/python3.11/site-packages/mooncake/mooncake_master"
        env = dict(self.env, LD_LIBRARY_PATH=str(root / "deps/lib") + os.pathsep + self.env.get("LD_LIBRARY_PATH", ""))
        flags = {"rpc_address": "127.0.0.1", "rpc_port": "28551", "rpc_thread_num": str(max(4, self.config.get("master_cpus", 2) * 2)),
                 "metrics_host": "127.0.0.1", "metrics_port": "28553", "enable_ha": "false", "enable_offload": "false",
                 "enable_http_metadata_server": "false", "dynamic_replication_mode": "off", "enable_oplog": "false",
                 "enable_metric_reporting": "false", "enable_snapshot": "false", "enable_snapshot_restore": "false",
                 "enable_disk_eviction": "false", "root_fs_dir": "", "enable_kv_events": "false", "enable_multi_tenants": "false",
                 "offload_on_evict": "false", "offload_force_evict": "false", "promotion_on_hit": "false", "eviction_ratio": "0", "eviction_high_watermark_ratio": "1"}
        atomic(directory / "master-config.json", flags)
        proc, _ = self.spawn("master", [master, *[f"--{k}={v}" for k, v in flags.items()]], group / "server/master", directory, env)
        self.wait("127.0.0.1", 28551, proc)
        endpoints = []
        for index in range(self.deployment["workers"]):
            host = f"127.71.{index // self.config['workers_per_node'] + 1}.{index % self.config['workers_per_node'] + 1}"
            port = 29001 + index
            capacity = int(self.config["memory_gib"] * (1 << 30) * .60 / self.deployment["workers"])
            proc, _ = self.spawn("peer-" + str(index), [python, "-B", "-m", "kvclab.remote_peer", "--host", host,
                                 "--port", str(port), "--master", "127.0.0.1:28551", "--capacity", str(capacity)], group / "server/workers", directory, env)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                self.check_cancel()
                if proc.poll() is not None:
                    raise RuntimeError("Mooncake peer exited before ready")
                if '"state": "ready"' in (directory / ("peer-" + str(index) + ".log")).read_text(errors="replace"):
                    break
                time.sleep(.2)
            else:
                raise TimeoutError("Mooncake peer did not become ready")
            endpoints.append(f"{host}:{port}")
        self.deployment.update(endpoints=endpoints, master_address="127.0.0.1:28551", actual_master_sha256=digest(master), python=str(python))

    def adapter_config(self, product, directory, size):
        path = directory / f"adapter-{size}.json"
        logs = directory / "sdk-logs"
        logs.mkdir(exist_ok=True)
        config = {"endpoints": self.deployment["endpoints"], "log_dir": str(logs), "value_size": size,
                  "master_server_addr": "127.0.0.1:28551", "local_hostname": "127.0.0.1", "local_buffer_size": 64 << 20,
                  "preload_batch": 64, "get_check_batch": min(self.config["batches"])}
        atomic(path, config)
        return path

    def qualify(self, product, directory, group):
        begin = time.monotonic_ns()
        config = self.adapter_config(product, directory, 1024)
        adapter = self.lab / "kvclab/adapters" / (product + ".py")
        out = directory / "qualification.json"
        command = [self.deployment["python"], "-B", "-m", "kvclab.remote_qualification", "--adapter", str(adapter), "--config", str(config), "--out", str(out)]
        traces = []
        if shutil.which("strace"):
            for process in list(self.deployment["processes"]):
                if process["name"].startswith("worker-") or process["name"] == "master":
                    proc, identity = self.spawn("qualification-trace-" + process["name"], ["strace", "-ff", "-ttt", "-yy", "-s", "4096", "-e", "trace=network,read,write,readv,writev", "-p", str(process["pid"]), "-o", str(directory / ("network-" + process["name"]))], group / "monitor", directory)
                    traces.append((proc, identity))
            command = ["strace", "-ff", "-ttt", "-yy", "-s", "4096", "-e", "trace=network,read,write,readv,writev", "-o", str(directory / "network-client"), *command]
            time.sleep(.3)
        try:
            rc = self.command("qualification", command, group / "client", directory, timeout=180, check=False)
        finally:
            for proc, identity in traces:
                terminate(identity, group / "monitor")
                proc.wait(timeout=5)
        result = read(out, {"valid": False, "error": "Qualification did not emit its result"})
        result["one_hop"] = {"verified": False, "status": "pending_protocol_audit" if product == "datasystem" else "not_applicable",
                             "reason": "Policy and raw network traces are captured; protocol-level RPC attribution is required to prove runtime hop count",
                             "network_files": [p.name for p in directory.glob("network-*") if p.is_file()]}
        if product == "datasystem" and rc == 0 and result["valid"]:
            from .onehop import verify_one_hop
            probe_start = time.monotonic_ns()
            proof = verify_one_hop(self.settings, self.deployment, config, directory / "one-hop")
            result["one_hop"] = proof
            self.deployment["one_hop"] = {key: proof.get(key) for key in ("verified", "status", "scope", "observed_rpc_calls", "observed_rpc_attempts", "reason")}
            self.persist()
            self.step("qualify_datasystem_one_hop", probe_start, "passed" if proof["verified"] else "failed", directory / "one-hop/one-hop.json")
            if not proof["verified"]:
                result["valid"] = False
                result["one_hop_error"] = proof.get("reason", "Runtime one-hop qualification did not pass")
        atomic(out, result)
        self.step("qualify_" + product, begin, "passed" if rc == 0 and result["valid"] else "failed", out)
        if rc or not result["valid"]:
            raise RuntimeError("Native qualification failed; inspect " + str(out))

    def measure(self, product, directory, group, *, repeats=None, sizes=None):
        tools = []
        for name, argv in [("iostat", ["iostat", "-x", "-y", "1"]), ("mpstat", ["mpstat", "-P", "ALL", "1"]),
                           ("pidstat", ["pidstat", "-u", "-r", "-d", "-p", "ALL", "1"]),
                           ("dstat", ["dstat", "-t", "-c", "-m", "-d", "-n", "1"])]:
            if shutil.which(argv[0]):
                tools.append(self.spawn("observe-" + name, argv, group / "monitor", directory))
        monitor = Monitor(group, directory / "resources.jsonl")
        monitor.start()
        try:
            for repeat in range(self.config["repeats"]) if repeats is None else repeats:
                for size in self.config["value_sizes"] if sizes is None else sizes:
                    config = self.adapter_config(product, directory, size)
                    pool, puts = workload_sizes(self.config, size)
                    common_run = self.request["job_id"] + "-" + self.variant + f"-r{repeat}-s{size}"
                    if any(operation != "put" for operation in self.config["operations"]):
                        preloaded = directory / f"preload-r{repeat}-s{size}.json"
                        before = time.monotonic_ns()
                        self.command("common-preload", [self.deployment["python"], "-B", "-m", "kvclab.remote_preload",
                            "--adapter", str(self.lab / "kvclab/adapters" / (product + ".py")), "--config", str(config),
                            "--clients", str(self.config["clients"]), "--size", str(size), "--pool-keys", str(pool),
                            "--run-id", common_run, "--out", str(preloaded)], group / "client", directory, timeout=660)
                        accepted = read(preloaded)
                        if not accepted.get("valid"):
                            raise RuntimeError("Common read pool did not pass complete bytes verification")
                        self.step("preload_" + product, before, "passed", preloaded,
                                  pool_keys=pool, reused_across_batches=self.config["batches"])
                    for batch in self.config["batches"]:
                        for operation in self.config["operations"]:
                            self.check_cancel()
                            begin = time.monotonic_ns()
                            name = f"r{repeat}-s{size}-b{batch}-{operation}"
                            cell = directory / name
                            cell.mkdir()
                            count = batch * self.config["clients"]
                            put = puts[batch]
                            # PUT has one bounded fresh warm/insert namespace per B; reads share the accepted pool.
                            case_run = common_run + f"-put-b{batch}" if operation == "put" else common_run
                            command = [self.deployment["python"], "-B", "-m", "kvcbench.run.workers.batch", "--adapter", str(self.lab / "kvclab/adapters" / (product + ".py")),
                                       "--config", str(config), "--op", operation, "--batch", str(batch), "--size", str(size),
                                       "--concurrency", str(self.config["clients"]), "--pool-keys", str(pool), "--put-keys", str(put),
                                       "--put-warmup-keys", str(min(put, max(count, count * (32768 // count)))), "--duration", str(self.config["duration_s"]), "--warmup", "1",
                                       "--run-id", case_run, "--skip-preload", "--out", str(cell / "raw.json")]
                            rc = self.command("native-case", command, group / "client", cell, timeout=max(300, self.config["duration_s"] + 180), check=False)
                            raw = read(cell / "raw.json", {})
                            valid = rc == 0 and raw.get("valid") is True
                            resources = monitor.summarize(raw.get("measure_start_mono_ns", 0), raw.get("measure_end_mono_ns", 0))
                            result = {"product": product, "variant_id": self.variant, "profile": self.profile, "nodes": self.config["nodes"],
                                      "workers": self.deployment["workers"], "workers_per_node": self.config["workers_per_node"], "clients": self.config["clients"],
                                      "batch": batch, "size": size, "value_size": size, "operation": operation, "repeat": repeat,
                                      "status": "valid" if valid else "failed", "keys_s": raw.get("keys_s") if valid else None,
                                      "batch_qps": raw.get("batch_qps") if valid else None, "p99_us": latency_us(raw, "p99") if valid else None,
                                      "p50_us": latency_us(raw, "p50") if valid else None, "p95_us": latency_us(raw, "p95") if valid else None,
                                      "throughput_mib_s": raw.get("throughput_mib_s") if valid else None,
                                      "reason": None if valid else raw.get("fatal") or [c.get("errors") for c in raw.get("clients", [])] or f"Native process returned {rc}",
                                      "raw_artifact": self.artifact(cell / "raw.json"), "duration_s": raw.get("duration_s"), "short_window": raw.get("short_window"), "low_sample_p99": raw.get("low_sample_p99"), "put_scope": "bounded fresh insert cardinality, not a timed upsert loop" if operation == "put" else None,
                                      "server_cpus": self.config["server_cpus"], "client_cpus": self.config["client_cpus"],
                                      "master_cpus": self.config.get("master_cpus", min(2, self.config["server_cpus"])), "memory_gib": self.config["memory_gib"], **resources}
                            result["build_sha"] = getattr(self, "verified_builds", {}).get(product, {}).get("sha")
                            result["preload_artifact"] = self.artifact(preloaded) if operation != "put" else None
                            result["common_pool_keys"] = pool if operation != "put" else 0
                            atomic(cell / "result.json", result)
                            self.results.append(result)
                            atomic(self.job / "results.json", self.results)
                            self.step("case_" + product + "_" + name, begin, "passed" if valid else "failed", cell / "result.json")
                            self.event("result", result=result)
        finally:
            monitor.stop()
            for proc, identity in tools:
                terminate(identity, group / "monitor")
                proc.wait(timeout=5)

    def verify_build(self):
        begin = time.monotonic_ns()
        catalog = read(self.settings["build_catalog"])
        verified = {}
        for product in self.config["products"]:
            row = catalog["records"][product]
            checks = {}
            for relative, expected in row["artifact_hashes"].items():
                path = self.native / relative
                if self.native not in path.parents or any(p.is_symlink() for p in [path, *path.parents]):
                    raise ValueError("Build artifact outside pinned native source root")
                actual = digest(path)
                checks[relative] = {"expected_sha256": expected, "sha256": actual, "valid": actual == expected}
            if not checks or not all(x["valid"] for x in checks.values()):
                raise ValueError("Pinned build artifact identity mismatch: " + product)
            verified[product] = {"tag": row["tag"], "sha": row["sha"], "artifacts": checks}
        self.verified_builds = verified
        path = self.job / "build-verification.json"
        atomic(path, verified)
        self.step("verify_builds", begin, "passed", path)

    def execute(self):
        atomic(self.job / "request.json", self.request)
        try:
            if self.request["action"] == "stop":
                self.stop()
            else:
                self.verify_build()
                variants = self.request.get("plan", {}).get("variants", [{"config": self.config, "id": "baseline"}])
                for index, variant in enumerate(variants):
                    self.variant = "v" + str(index)
                    self.config = variant["config"]
                    self.profile = self.config.get("profile", "baseline")
                    if self.request["action"] == "run":
                        for repeat in range(self.config["repeats"]):
                            products = self.config["products"]
                            order = products[repeat % len(products):] + products[:repeat % len(products)]
                            for size in self.config["value_sizes"]:
                                for product in order:
                                    directory, group = self.deploy(product)
                                    self.qualify(product, directory, group)
                                    self.measure(product, directory, group, repeats=[repeat], sizes=[size])
                    else:
                        for product in self.config["products"]:
                            directory, group = self.deploy(product)
                            self.qualify(product, directory, group)
                    if self.request["action"] == "deploy" and len(self.config["products"]) > 1:
                        self.event("log", message="Products were prepared and qualified sequentially to avoid interference; starting the first selected product for the retained deployment")
                        accepted_directory, accepted_group = self.deploy(self.config["products"][0])
                        self.qualify(self.config["products"][0], accepted_directory, accepted_group)
            status = "completed_with_failures" if any(row["status"] == "failed" for row in self.results) else "succeeded"
            result = {"kind": "complete", "job_id": self.request["job_id"], "status": status,
                      "results": self.results, "deployment": self.deployment, "artifact_root": str(self.job)}
        except BaseException as exc:
            import traceback
            error = traceback.format_exc()
            cleanup_error = None
            try:
                self.stop()
            except BaseException:
                cleanup_error = traceback.format_exc()
                error += "\nCleanup error:\n" + cleanup_error
            cancelled = isinstance(exc, Cancelled)
            result = {"kind": "complete", "job_id": self.request["job_id"],
                      "status": "cancelled" if cancelled and cleanup_error is None else "failed", "cancelled": cancelled,
                      "cleanup_error": cleanup_error, "error": error, "results": self.results,
                      "deployment": self.deployment, "artifact_root": str(self.job)}
        if self.request["action"] == "run":
            plan = self.request.get("plan") or {"variants": [{"config": self.request["config"]}]}
            coverage = reconcile(plan, self.results, include_missing=False)["coverage"]
            result["coverage"] = coverage
            path = self.job / "coverage.json"
            atomic(path, coverage)
            self.step("accept_complete_matrix", time.monotonic_ns(), "passed" if coverage["complete"] else "failed", path)
            if not coverage["complete"] and result["status"] == "succeeded":
                result.update(status="failed", error="Result matrix did not close against the submitted plan")
        self.event("log", message="Remote execution reached terminal state", status=result["status"])
        self.events.close()
        result["artifacts"] = manifest(self.job)
        path = self.job / "artifact-manifest.json"
        atomic(path, result["artifacts"])
        result["artifacts"].append({"name": path.name, "size": path.stat().st_size, "downloadable": True, "sha256": digest(path)})
        atomic(self.job / "complete.json", result)
        return result
