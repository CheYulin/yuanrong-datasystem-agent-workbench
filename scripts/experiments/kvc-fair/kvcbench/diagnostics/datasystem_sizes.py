#!/usr/bin/env python3
"""Correctness-only rc44 TCP gate; never starts or reconfigures product services.

Run with the installed DataSystem interpreter, existing adapter/config and a fresh
--out JSON and --log-dir. --self-test uses only labelled offline fixtures.
"""

import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in {None, ""}:
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
from decimal import Decimal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

from kvcbench.collect.traces import trace_evidence


SIZES = (1024, 65536, 1048576)
OPERATIONS = ("DS_KV_CLIENT_SET", "DS_KV_CLIENT_GET")
LOG_NAME = "tcp_probe_access"
ENVIRONMENT = {
    "DATASYSTEM_PERF_TRACE_LOG": "true",
    "DATASYSTEM_LOG_V": "1",
    "DATASYSTEM_MIN_LOG_LEVEL": "0",
    "DATASYSTEM_LOG_MONITOR_ENABLE": "true",
    "DATASYSTEM_LOG_MONITOR_INTERVAL_MS": "100",
    "DATASYSTEM_CLIENT_ACCESS_LOG_NAME": LOG_NAME,
}
SOURCE = {
    "tag": "v0.9.2.rc44",
    "sha": "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6",
    "python_set": "python/yr/datasystem/kv_client.py:353; src/datasystem/pybind_api/pybind_register_kv.cpp:276-291",
    "python_get": "python/yr/datasystem/kv_client.py:595; src/datasystem/pybind_api/pybind_register_kv.cpp:459-498",
    "python_access_limit": "These pybind recorders omit TrackedTransportType; no transportType field is assumed.",
    "get_materialize": "src/datasystem/client/object_cache/routed_mode.cpp:635-639",
    "ipc_gate": "src/datasystem/worker/object_cache/worker_oc_server.cpp:134; src/datasystem/worker/worker_service_impl.cpp:491",
    "tcp_payload_set": "src/datasystem/client/object_cache/transport/data_plane/tcp_transporter.cpp:149-176",
    "inline_rpc_fallback": "src/datasystem/client/object_cache/transport/data_plane/shm_transporter.h:259-288",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def parse_access_line(line):
    fields = [part.strip() for part in line.split("|")]
    positions = [index for index, part in enumerate(fields) if part in OPERATIONS]
    if not positions:
        return None
    if len(positions) != 1:
        raise ValueError("Ambiguous client access operation")
    index = positions[0]
    if index < 1 or len(fields) <= index + 4:
        raise ValueError("Incomplete rc44 client access record")
    if not re.fullmatch(r"-?\d+", fields[index - 1]):
        raise ValueError("Access status is not a numeric status code")
    if not re.fullmatch(r"\d+", fields[index + 1]) or not re.fullmatch(r"\d+", fields[index + 2]):
        raise ValueError("Access latency/data-size fields are invalid")
    request = fields[index + 3]
    if not request.startswith("{") or not request.endswith("}"):
        raise ValueError("Access request parameter object is absent")
    key_fields = re.findall(r"(?:^|[,{])\s*Object_key:\s*(\[[^\]]*\]|[^,}]+)", request)
    media = re.findall(r"(?:^|[,{])\s*transportType:\s*([^,}\s]+)", request)
    if len(key_fields) != 1 or len(media) > 1:
        raise ValueError("An exact Object_key and unambiguous optional transportType are required")
    encoded = key_fields[0].strip()
    keys = encoded[1:-1].split(",") if encoded.startswith("[") else [encoded]
    return {"operation": fields[index], "code": int(fields[index - 1]),
            "elapsed_us": int(fields[index + 1]), "data_size": int(fields[index + 2]),
            "keys": [key.strip() for key in keys], "actual_medium": media[0] if media else None}


def read_access_logs(log_dir):
    logs, records, malformed = [], [], []
    for path in sorted(log_dir.glob(LOG_NAME + ".log*")):
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_bytes()
        logs.append({"path": str(path), "bytes": len(content), "sha256": digest(content)})
        for number, raw in enumerate(content.splitlines(), 1):
            try:
                line = raw.decode("utf-8", errors="strict")
                record = parse_access_line(line)
            except (ValueError, UnicodeDecodeError) as error:
                malformed.append({"path": str(path), "line": number, "error": str(error),
                                  "line_sha256": digest(raw), "text": raw.decode("utf-8", errors="replace")})
                continue
            if record is not None:
                records.append({**record, "path": str(path), "line": number,
                                "line_sha256": digest(raw), "text": line})
    return {"logs": logs, "records": records, "malformed": malformed}


def anchor(path, number, raw):
    return {"path": str(path), "line": number, "line_sha256": digest(raw),
            "text": raw.decode("utf-8", errors="replace")}


def read_materialize(log_dir, key):
    selected, logs = [], []
    expression = re.compile(r"\[TransportGet\]\[Materialize\] Materialize object, key: "
                            + re.escape(key) + r", transport: ([A-Z_]+), data size: (\d+), payload size: (\d+),")
    for path in sorted(log_dir.glob("*.INFO.log*")):
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_bytes()
        logs.append({"path": str(path), "bytes": len(content), "sha256": digest(content)})
        for number, raw in enumerate(content.splitlines(), 1):
            found = expression.search(raw.decode("utf-8", errors="replace"))
            if found:
                selected.append({**anchor(path, number, raw), "actual_medium": found[1],
                                 "data_size": int(found[2]), "payload_size": int(found[3]), "key": key})
    return selected, logs


def process_snapshot(log_dir, name):
    maps_path = log_dir / (name + ".maps")
    maps_path.write_bytes(Path("/proc/self/maps").read_bytes())
    shared = [line for line in maps_path.read_text().splitlines()
              if len(line.split()) >= 2 and line.split()[1].startswith("rw") and line.split()[1].endswith("s")]
    unix = {line.split()[6] for line in Path("/proc/self/net/unix").read_text().splitlines()[1:]
            if len(line.split()) >= 7}
    descriptors = []
    for path in Path("/proc/self/fd").iterdir():
        try:
            target = os.readlink(path)
        except FileNotFoundError:
            continue
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        descriptors.append({"fd": int(path.name), "target": target,
                            "unix_socket": bool(match and match[1] in unix)})
    return {"maps": {"path": str(maps_path), "sha256": digest(maps_path.read_bytes())},
            "writable_shared_mappings": shared, "fds": descriptors,
            "unix_socket_fds": [fd for fd in descriptors if fd["unix_socket"]]}


def configuration_evidence(run_dir, endpoints):
    manifest_path = run_dir / "processes.json"
    manifest = json.loads(manifest_path.read_text())
    evidence = []
    for index in [0, 1]:
        path = run_dir / f"w{index}/worker.flags"
        raw = path.read_bytes()
        flags = dict(line[2:].split("=", 1) for line in raw.decode().splitlines() if line.startswith("--"))
        required = {"worker_address": endpoints[index], "ipc_through_shared_memory": "false",
                    "enable_rdma": "false", "enable_urma": "false"}
        if any(flags.get(k) != v for k, v in required.items()):
            raise ValueError("Worker TCP flags are absent or inconsistent: " + str(path))
        process = next(item for item in manifest["processes"] if item["name"] == f"worker{index}")
        pid = process["pid"]
        live_args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().strip("\0").split("\0")
        live_stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if live_stat[0] == "Z" or str(process["start_ticks"]) != live_stat[19]:
            raise ValueError("Worker PID identity is no longer live")
        if any(f"--{k}={v}" not in live_args for k, v in required.items()):
            raise ValueError("Live worker command disagrees with TCP flag evidence")
        evidence.append({"worker_index": index, "path": str(path), "sha256": digest(raw),
                         "flags": required, "pid": pid, "start_ticks": live_stat[19], "live_argv": live_args})
    return {"workers": evidence, "processes_path": str(manifest_path),
            "processes_sha256": digest(manifest_path.read_bytes()), "source": SOURCE}


def audit_evidence(evidence, materialize, wire, child_result, key, size, *, returncode=0, timed_out=False):
    errors = []
    if timed_out or returncode != 0 or child_result.get("correctness") is not True:
        errors.append("SDK child did not complete within timeout with all exact-byte/EXIST checks")
    if not evidence["logs"] or evidence["malformed"]:
        errors.append("Missing or malformed client access log")
    access_anchors = {}
    for operation in OPERATIONS:
        selected = [record for record in evidence["records"] if record["operation"] == operation and record["keys"] == [key]]
        access_anchors[operation] = selected
        if len(selected) != 1 or any(r["code"] != 0 or r["data_size"] != size for r in selected):
            errors.append("Expected one successful exact-size access record for " + operation)
        if any(r["actual_medium"] not in [None, "TCP"] for r in selected):
            errors.append("Optional actual access medium is non-TCP")
    if len(materialize) != 1 or any(r["key"] != key or r["actual_medium"] != "TCP" or r["data_size"] != size
                                    or r["payload_size"] != size for r in materialize):
        errors.append("Missing, duplicate or non-TCP exact-key/size GET materialization")
    for operation in ["put", "get"]:
        observed = wire["operations"][operation]
        if observed["successful_bytes"] < size or observed["payload_prefix_observed"] is not True:
            errors.append(operation.upper() + " lacks full-volume TCP syscall bytes and its unique payload prefix")
    for name in ["before", "after"]:
        snapshot = child_result.get("process_snapshots", {}).get(name)
        if snapshot is None or snapshot["writable_shared_mappings"] or snapshot["unix_socket_fds"]:
            errors.append("Missing or unexpected shared mappings/UNIX socket descriptors in " + name + " snapshot")
    return {"passed": not errors, "errors": errors, "actual_media": ["TCP"] if not errors else [],
            "access_anchors": access_anchors, "actual_medium_anchors": {
                OPERATIONS[0]: wire["operations"]["put"]["anchors"],
                OPERATIONS[1]: materialize + wire["operations"]["get"]["anchors"]},
            "get_materialize_anchors": materialize, "tcp_syscalls": wire["operations"],
            "trace_sha256": wire["trace_sha256"], "log_sha256": evidence["logs"],
            "malformed": evidence["malformed"], "access_record_count": len(evidence["records"])}


def child(args):
    log_dir = args.log_dir.resolve()
    os.environ.update(ENVIRONMENT)
    os.environ["DATASYSTEM_CLIENT_LOG_DIR"] = str(log_dir)
    config = json.loads(args.config.read_text())
    config.update(worker_index=args.worker_index, client_index=args.worker_index, log_dir=str(log_dir))
    result = {"correctness": False, "key": args.key, "size": args.size,
              "worker_index": args.worker_index, "checks": {}, "environment": ENVIRONMENT}
    adapter = None
    try:
        spec = importlib.util.spec_from_file_location("tcp_probe_existing_adapter", args.adapter)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result["process_snapshots"] = {"before": process_snapshot(log_dir, "before")}
        adapter = module.new_adapter(config)
        block = hashlib.sha256(args.key.encode()).digest()
        payload = block * (args.size // len(block))
        result["payload_prefix_hex"] = block.hex()
        result["windows"] = {}
        absent = args.key + "m"
        result["checks"]["fresh_key_absent"] = adapter.exists(args.key) is False
        result["checks"]["independent_miss_before"] = adapter.exists(absent) is False
        if not all(result["checks"].values()):
            raise AssertionError("Fresh probe key space was unexpectedly populated")
        for operation, function in [("put", lambda: adapter.put(args.key, payload)),
                                    ("get", lambda: adapter.get(args.key))]:
            start = time.time_ns()
            answer = function()
            result["windows"][operation] = {"start_wall_ns": start, "end_wall_ns": time.time_ns()}
        result["checks"]["get_exact_bytes"] = isinstance(answer, bytes) and answer == payload
        result["checks"]["exists_hit"] = adapter.exists(args.key) is True
        result["checks"]["exists_miss"] = adapter.exists(absent) is False
        result.update(payload_sha256=digest(payload), returned_sha256=digest(answer),
                      returned_bytes=len(answer), correctness=all(result["checks"].values()))
        if result["correctness"] is not True:
            raise AssertionError("Exact bytes or EXIST boolean check failed")
        result["process_snapshots"]["after"] = process_snapshot(log_dir, "after")
    except BaseException:
        result["error"] = traceback.format_exc()
        result["correctness"] = False
    finally:
        if adapter is not None:
            try:
                adapter.close()
            except BaseException:
                result["close_error"] = traceback.format_exc()
                result["correctness"] = False
        write_json(log_dir / "child-result.json", result)
    return 0 if result["correctness"] else 1


def self_test(out, log_dir):
    results, key, size = [], "offline_fixture", 1024
    def access(op):
        return {"operation": op, "code": 0, "data_size": size, "keys": [key], "actual_medium": None}
    evidence = {"logs": [{"path": "fixture", "sha256": "fixture"}],
                "records": [access(op) for op in OPERATIONS], "malformed": []}
    materialize = [{"actual_medium": "TCP", "data_size": size, "payload_size": size, "key": key}]
    child_result = {"correctness": True, "process_snapshots": {
        name: {"writable_shared_mappings": [], "unix_socket_fds": []} for name in ["before", "after"]}}
    wire = {"operations": {name: {"successful_bytes": size, "payload_prefix_observed": True, "anchors": []}
                            for name in ["put", "get"]}, "trace_sha256": []}
    def check(name, expected, e=evidence, m=materialize, w=wire, c=child_result, **kw):
        actual = audit_evidence(e, m, w, c, key, size, **kw)
        assert actual["passed"] == expected, (name, actual)
        results.append({"name": name, "passed": True})
    check("Official Python access records without transportType accepted only with independent TCP proof", True)
    for medium in ["SHM", "UB", "RDMA", "URMA", "TCP_ONLY"]:
        check("Non-TCP actual materialization rejected: " + medium, False,
              m=[{**materialize[0], "actual_medium": medium}])
    check("Missing materialization rejected", False, m=[])
    check("Duplicate materialization rejected", False, m=materialize * 2)
    check("Wrong materialized size rejected", False, m=[{**materialize[0], "data_size": 1}])
    check("Other key materialization rejected", False, m=[{**materialize[0], "key": key + "other"}])
    check("Child timeout rejected", False, timed_out=True)
    check("SDK failure rejected", False, returncode=1)
    check("Incorrect bytes rejected", False, c={**child_result, "correctness": False})
    for name in ["put", "get"]:
        for field, value in [("successful_bytes", size - 1), ("payload_prefix_observed", False)]:
            changed = json.loads(json.dumps(wire))
            changed["operations"][name][field] = value
            check(name + " insufficient " + field + " rejected", False, w=changed)
    changed = json.loads(json.dumps(child_result))
    changed["process_snapshots"]["after"]["writable_shared_mappings"] = ["offline shared map"]
    check("Shared mapping rejected", False, c=changed)
    changed = json.loads(json.dumps(child_result))
    changed["process_snapshots"]["after"]["unix_socket_fds"] = [{"fd": 99}]
    check("UNIX socket descriptor rejected", False, c=changed)
    raw = ("2026-10-20 | I | 1:1 | x | 0 | DS_KV_CLIENT_SET | 123 | 1024 | "
           "{Object_key:offline_fixture,Write_mode:0} | ")
    assert parse_access_line(raw)["actual_medium"] is None
    results.append({"name": "Real rc44 Python access layout parsed without invented medium", "passed": True})
    prefix_hex = bytes(range(32)).hex()
    encoded = "".join("\\x" + prefix_hex[i:i + 2] for i in range(0, 64, 2))
    trace = log_dir / "strace.1"
    trace.write_text('1.100000 writev(9<TCP:[127.0.0.1:9000->127.0.0.1:31531]>, [{iov_base="'
                     + encoded + '", iov_len=1024}], 1) = 1024 <0.000010>\n'
                     '1.200000 readv(9<TCP:[127.0.0.1:9000->127.0.0.1:31531]>,  <unfinished ...>\n'
                     '1.210000 <... readv resumed>[{iov_base="' + encoded
                     + '", iov_len=1024}], 1) = 1024 <0.010000>\n'
                     '1.500000 writev(9<TCP:[127.0.0.1:9000->127.0.0.1:31531]>, [], 1) = 999999 <0.0>\n')
    windows = {"put": {"start_wall_ns": 1050000000, "end_wall_ns": 1150000000},
               "get": {"start_wall_ns": 1900000000, "end_wall_ns": 2000000000}}
    windows["get"] = {"start_wall_ns": 1190000000, "end_wall_ns": 1220000000}
    parsed = trace_evidence(log_dir, windows, prefix_hex, "127.0.0.1:31531")
    assert all(r["successful_bytes"] == 1024 and r["payload_prefix_observed"] for r in parsed["operations"].values())
    results.append({"name": "Directional TCP prefix/count windows and resumed syscall parser", "passed": True})
    wrong = trace_evidence(log_dir, windows, prefix_hex, "127.0.0.1:31532")
    assert all(r["successful_bytes"] == 0 for r in wrong["operations"].values())
    results.append({"name": "Other worker endpoint cannot satisfy wire proof", "passed": True})
    routed = trace_evidence(log_dir, windows, prefix_hex, ["127.0.0.1:31532", "127.0.0.1:31531"])
    assert all(r["payload_prefix_observed"] for r in routed["operations"].values())
    results.append({"name": "Routed payload to either configured worker is recorded", "passed": True})
    write_json(out, {"passed": True, "scope": "Offline parser/decision fixtures only; not DataSystem TCP evidence.",
                     "checks": len(results), "results": results, "source": SOURCE})
    print(json.dumps({"passed": True, "checks": len(results)}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Experiment root; defaults adapter to ROOT/scripts/datasystem/adapter_datasystem.py")
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--run-dir", type=Path, help="Live DS run dir; default OUT.parent/datasystem/run")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="Fresh machine-readable result JSON")
    parser.add_argument("--log-dir", type=Path, required=True, help="Fresh parent directory for six isolated SDK log directories")
    parser.add_argument("--timeout-s", type=float, default=90)
    parser.add_argument("--log-timeout-s", type=float, default=15,
                        help="Reserved compatibility option; logs are validated by parent after SDK process exit/flush")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-index", type=int, choices=[0, 1], help=argparse.SUPPRESS)
    parser.add_argument("--size", type=int, choices=SIZES, help=argparse.SUPPRESS)
    parser.add_argument("--key", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not all(math.isfinite(value) and value > 0 for value in [args.timeout_s, args.log_timeout_s]) or args.log_timeout_s >= args.timeout_s:
        parser.error("Timeouts must be positive and log timeout must be less than child timeout")
    if args.child:
        return child(args)
    if args.out.exists():
        parser.error("--out must not already exist; existing evidence is never reused")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=False)
    if args.self_test:
        return self_test(args.out, args.log_dir)
    if args.adapter is None and args.root is not None:
        args.adapter = args.root / "scripts/datasystem/adapter_datasystem.py"
    if args.adapter is None or args.config is None:
        parser.error("--adapter and --config are required outside --self-test")
    args.adapter, args.config = args.adapter.resolve(strict=True), args.config.resolve(strict=True)
    config = json.loads(args.config.read_text())
    endpoints = config.get("endpoints", [])
    if len(endpoints) != 2 or len(set(endpoints)) != 2:
        parser.error("Config must identify exactly two distinct worker endpoints")
    configuration = configuration_evidence(args.run_dir or args.out.parent / "datasystem/run", endpoints)
    strace_version = subprocess.check_output(["strace", "-V"], text=True).splitlines()[0]
    started_wall, started_mono = time.time_ns(), time.monotonic_ns()
    cases = []
    for worker_index in [0, 1]:
        for size in SIZES:
            directory = args.log_dir.resolve() / f"worker{worker_index}-size{size}"
            directory.mkdir()
            key = "tg" + uuid.uuid4().hex[:12] + f"w{worker_index}s{size}"
            command = ["strace", "-ff", "-ttt", "-T", "-yy", "-xx", "-s", "2048",
                       "-e", "trace=network,read,readv,write,writev", "-o", str(directory / "strace"),
                       sys.executable, str(Path(__file__).resolve()), "--child", "--adapter", str(args.adapter),
                       "--config", str(args.config), "--out", str(directory / "child-result.json"),
                       "--log-dir", str(directory), "--worker-index", str(worker_index),
                       "--size", str(size), "--key", key, "--timeout-s", str(args.timeout_s),
                       "--log-timeout-s", str(args.log_timeout_s)]
            timed_out, returncode = False, None
            begin = time.monotonic_ns()
            with (directory / "child.stdout.log").open("wb") as stream:
                try:
                    process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                               stdin=subprocess.DEVNULL, start_new_session=True,
                                               env={**os.environ, **ENVIRONMENT})
                    try:
                        returncode = process.wait(timeout=args.timeout_s)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        returncode = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    timed_out = True
            child_path = directory / "child-result.json"
            child_result = json.loads(child_path.read_text()) if child_path.exists() else {}
            materialize, info_logs = read_materialize(directory, key)
            wire = trace_evidence(directory, child_result.get("windows", {}),
                                  child_result.get("payload_prefix_hex", ""), endpoints)
            audit = audit_evidence(read_access_logs(directory), materialize, wire, child_result, key, size,
                                   returncode=returncode, timed_out=timed_out)
            audit["log_sha256"].extend(info_logs)
            cases.append({"worker_index": worker_index, "bound_endpoint": endpoints[worker_index],
                          "size": size, "key": key, "duration_s": (time.monotonic_ns() - begin) / 1e9,
                          "returncode": returncode, "timed_out": timed_out, "child": child_result, **audit})
    result = {"passed": len(cases) == 6 and all(case["passed"] for case in cases),
              "schema_version": 2,
              "scope": "Six exact-byte/EXIST cases; PUT and GET each require directional TCP socket bytes with a key-specific payload prefix, GET also requires actual TCP materialization. Python access logs omit transportType. No performance or worker-worker payload claim.",
              "configuration_evidence": configuration, "strace_version": strace_version,
              "source": SOURCE, "start_wall_ns": started_wall, "end_wall_ns": time.time_ns(),
              "duration_s": (time.monotonic_ns() - started_mono) / 1e9,
              "environment": ENVIRONMENT, "python": sys.executable,
              "input_sha256": {"probe": digest(Path(__file__).read_bytes()),
                               "adapter": digest(args.adapter.read_bytes()), "config": digest(args.config.read_bytes())},
              "cases": cases}
    write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
