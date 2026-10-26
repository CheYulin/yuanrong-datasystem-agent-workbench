"""Private path, identity, admission and cgroup primitives for remote execution."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import time


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 << 20), b""):
            value.update(block)
    return value.hexdigest()


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("Invalid job_id")
    return value


def validate_request(request, limits):
    if not isinstance(request, dict) or set(request) - {"job_id", "action", "config", "plan"}:
        raise ValueError("Unexpected request fields")
    job_id(request.get("job_id"))
    if request.get("action") not in {"deploy", "run", "stop"}:
        raise ValueError("Unknown action")
    if request["action"] == "stop":
        return request
    config = request.get("config")
    fields = {"nodes", "workers_per_node", "clients", "server_cpus", "client_cpus", "memory_gib",
              "products", "batches", "value_sizes", "operations", "duration_s", "repeats"}
    if not isinstance(config, dict) or set(config) - {"profile", "master_cpus"} != fields:
        raise ValueError("Complete, explicit experiment configuration required")
    bounds = {"nodes": (1, 8), "workers_per_node": (1, 8), "clients": (1, 64),
              "server_cpus": (1, limits.get("max_server_cpus", 24)),
              "client_cpus": (1, limits.get("max_client_cpus", 12)),
              "memory_gib": (2, limits.get("max_memory_gib", 80)), "repeats": (1, 5)}
    for name, (low, high) in bounds.items():
        if type(config[name]) is not int or not low <= config[name] <= high:
            raise ValueError("Out of range: " + name)
    if config["nodes"] * config["workers_per_node"] > 8:
        raise ValueError("At most eight workers are allowed")
    if config.get("profile", "baseline") not in {"baseline", "worker_scaling", "client_scaling", "metadata_scaling"}:
        raise ValueError("Unknown experiment profile")
    master_cpus = config.get("master_cpus", min(2, config["server_cpus"]))
    if type(master_cpus) is not int or master_cpus not in {1, 2, 4, 8} or master_cpus > config["server_cpus"]:
        raise ValueError("master_cpus must be 1, 2, 4 or 8 and within the server total")
    if type(config["duration_s"]) not in (float, int) or not math.isfinite(config["duration_s"]) or not 1 <= config["duration_s"] <= 120:
        raise ValueError("duration_s must be finite and between 1 and 120")
    allowed = {"products": {"datasystem", "mooncake"}, "batches": {1, 16, 256, 4096},
               "operations": {"put", "get", "exist_hit", "exist_miss"}}
    for name, choices in allowed.items():
        values = config[name]
        expected_type = int if name == "batches" else str
        if not isinstance(values, list) or not values or any(type(value) is not expected_type for value in values) or len(values) != len(set(values)) or any(value not in choices for value in values):
            raise ValueError("Invalid experiment list: " + name)
    values = config["value_sizes"]
    if not isinstance(values, list) or not 1 <= len(values) <= 4 or any(type(v) is not int or not 1 <= v <= 1 << 20 for v in values) or len(set(values)) != len(values):
        raise ValueError("One to four distinct value sizes between 1 and 1MiB are required")
    budget = config["memory_gib"] * (1 << 30)
    if max(config["batches"]) * config["clients"] * max(values) > budget // 8:
        raise ValueError("One in-flight batch per client exceeds the admitted server memory budget")
    plan = request.get("plan")
    if plan is not None:
        if not isinstance(plan, dict) or not isinstance(plan.get("variants"), list) or not 1 <= len(plan["variants"]) <= 16:
            raise ValueError("Explicit bounded variants required")
        for variant in plan["variants"]:
            if not isinstance(variant, dict) or not isinstance(variant.get("config"), dict):
                raise ValueError("Each variant requires a complete configuration")
            validate_request({"job_id": request["job_id"], "action": request["action"], "config": variant["config"]}, limits)
            if set(variant["config"]["products"]) != set(config["products"]):
                raise ValueError("All variants must use the same verified products")
    return request


def ticks(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def member(pid, group):
    try:
        line = Path(f"/proc/{pid}/cgroup").read_text().strip()
        path = str(Path(group)).removeprefix("/sys/fs/cgroup")
        return line == "0::" + path or line.startswith("0::" + path + "/")
    except (FileNotFoundError, ProcessLookupError):
        return False


def terminate(identity, group, grace=5):
    pid = identity["pid"]
    if ticks(pid) != identity["start_ticks"] or not member(pid, group):
        return {"pid": pid, "status": "already_stopped_or_reused"}
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return {"pid": pid, "status": "already_stopped"}
    try:
        if ticks(pid) != identity["start_ticks"] or not member(pid, group):
            return {"pid": pid, "status": "identity_changed"}
        for signum, timeout in [(signal.SIGTERM, grace), (signal.SIGKILL, 3)]:
            try:
                signal.pidfd_send_signal(fd, signum)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + timeout
            while ticks(pid) == identity["start_ticks"] and time.monotonic() < deadline:
                time.sleep(.05)
            if ticks(pid) != identity["start_ticks"]:
                return {"pid": pid, "status": "terminated" if signum == signal.SIGTERM else "killed_after_grace"}
        if ticks(pid) == identity["start_ticks"]:
            raise RuntimeError("Owned process did not exit: " + str(pid))
        return {"pid": pid, "status": "terminated"}
    finally:
        os.close(fd)


def physical_cpus(sysroot=Path("/sys/devices/system/cpu")):
    found = {}
    for path in sysroot.glob("cpu[0-9]*"):
        number = int(path.name[3:])
        if (path / "online").exists() and (path / "online").read_text().strip() == "0":
            continue
        topology = path / "topology"
        identity = ((topology / "physical_package_id").read_text().strip(),
                    (topology / "core_id").read_text().strip())
        found[identity] = min(number, found.get(identity, number))
    return sorted(found.values())


def setup_groups(root, config):
    root = Path(root)
    if root.exists():
        raise RuntimeError("Fresh cgroup required")
    cpus = physical_cpus()
    servers, clients = config["server_cpus"], config["client_cpus"]
    if len(cpus) < servers + clients + 2:
        raise ValueError("Insufficient distinct physical cores for server, client and monitor")
    root.parent.mkdir(parents=True, exist_ok=True)
    (root.parent / "cgroup.subtree_control").write_text("+cpu +cpuset +memory +io")
    root.mkdir()
    (root / "cgroup.subtree_control").write_text("+cpu +cpuset +memory +io")
    server_mem = config["memory_gib"] << 30
    client_mem = 8 << 30
    (root / "memory.max").write_text(str(server_mem + client_mem + (1 << 30)))
    (root / "memory.swap.max").write_text("0")
    roles = {"server": (cpus[:servers], servers, server_mem),
             "client": (cpus[servers:servers + clients], clients, client_mem),
             "monitor": (cpus[-2:], 2, 1 << 30)}
    result = {}
    for role, (selected, count, memory) in roles.items():
        group = root / role
        group.mkdir()
        settings = {"cpuset.cpus": ",".join(map(str, selected)), "cpuset.mems": "0-1",
                    "cpu.max": f"{count * 100000} 100000", "memory.max": str(memory), "memory.swap.max": "0"}
        for name, value in settings.items():
            (group / name).write_text(value)
        if role == "server":
            (group / "cgroup.subtree_control").write_text("+cpu +cpuset +memory +io")
            for name in ["master", "workers"]:
                (group / name).mkdir()
            (group / "master/cpu.max").write_text(f"{config.get('master_cpus', min(2, count)) * 100000} 100000")
        result[role] = {"path": str(group), **{name: (group / name).read_text().strip() for name in settings}}
    return result
