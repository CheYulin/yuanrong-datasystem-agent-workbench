#!/usr/bin/env python3
"""Offline DataSystem batch TCP evidence audit; never starts or contacts services."""

import argparse
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import time

from batch_gate import digest_items, payload, write_immutable
from tcp_probe_ds_sizes import trace_evidence


BATCHES = [1, 16, 256, 4096]
SOURCE_SHA = "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6"


def file_record(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("Evidence must be a regular nonsymlink file: " + str(path))
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def configuration_endpoints(raw):
    workers = raw.get("workers")
    if not isinstance(workers, list) or len(workers) != 2 or {row.get("worker_index") for row in workers} != {0, 1}:
        raise ValueError("Exactly two saved live Worker configuration observations are required")
    endpoints = []
    for worker in sorted(workers, key=lambda row: row["worker_index"]):
        flags = worker.get("flags", {})
        endpoint = flags.get("worker_address")
        if not isinstance(endpoint, str) or not endpoint or endpoint in endpoints:
            raise ValueError("Worker endpoints must be distinct and explicit")
        required = {"worker_address": endpoint, "ipc_through_shared_memory": "false",
                    "enable_rdma": "false", "enable_urma": "false"}
        argv = worker.get("live_argv")
        if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
            raise ValueError("Saved live Worker argv is absent")
        for key, value in required.items():
            if flags.get(key) != value or [item for item in argv if item.startswith("--" + key + "=")] != [f"--{key}={value}"]:
                raise ValueError("Saved live Worker TCP configuration mismatch: " + key)
        if type(worker.get("pid")) is not int or worker["pid"] <= 0 or not str(worker.get("start_ticks", "")).isdigit():
            raise ValueError("Saved Worker process identity is incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", worker.get("sha256", "")):
            raise ValueError("Saved Worker flags digest is absent")
        endpoints.append(endpoint)
    if not re.fullmatch(r"[0-9a-f]{64}", raw.get("processes_sha256", "")):
        raise ValueError("Saved Worker process manifest digest is absent")
    return endpoints


def selected_step(raw, count, name):
    steps = [step for step in raw.get("steps", []) if step.get("batch_keys") == count and step.get("name") == name]
    if len(steps) != 1:
        raise ValueError(f"Expected one {name} step for B={count}")
    step = steps[0]
    if (step.get("status") != "passed" or step.get("native_batch") is not True
            or type(step.get("start_wall_ns")) is not int or type(step.get("end_wall_ns")) is not int
            or step["end_wall_ns"] < step["start_wall_ns"]):
        raise ValueError("Native API step did not complete with a valid wall-clock interval")
    return step


def gate_cases(raw, index):
    if (raw.get("valid") is not True or raw.get("client_index") != index or raw.get("value_size") != 1024
            or raw.get("batch_sizes") != BATCHES or raw.get("failed_steps") != []):
        raise ValueError("Complete successful exact-endpoint 1KiB batch gate is required")
    metadata = raw.get("metadata", {})
    if metadata.get("source_sha") != SOURCE_SHA:
        raise ValueError("The gate does not name the pinned DataSystem rc44 source")
    cases = raw.get("cases", [])
    if (len(cases) != 4 or {case.get("batch_keys") for case in cases} != set(BATCHES)
            or not all(case.get("valid") is True and case.get("put_native_supported") is True
                       and case.get("get_native_supported") is True for case in cases)):
        raise ValueError("Native PUT/GET gate must cover each batch exactly once")
    return {case["batch_keys"]: case for case in cases}


@lru_cache(maxsize=64)
def payload_identity(prefix, count):
    keys = [prefix + "k" + str(i) for i in range(count)]
    return (digest_items(keys), digest_items(payload(key, 0, 1024) for key in keys),
            keys[0], payload(keys[0], 0, 1024)[:32].hex())


def compact_wire(wire, suite):
    result = {key: value for key, value in wire.items() if key != "trace_sha256"}
    for operation in result["operations"].values():
        operation["anchors"] = [{key: value for key, value in anchor.items() if key != "text"}
                                  for anchor in operation["anchors"]]
        for anchor in operation["anchors"]:
            anchor["path"] = str(Path(anchor["path"]).relative_to(suite))
    return result


def verify(suite):
    suite = Path(suite).resolve()
    directory = suite / "r0-datasystem"
    result = {"schema_version": 1, "kind": "datasystem_native_batch_tcp_audit", "suite": str(suite),
              "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(),
              "cases": [], "inputs": {}, "valid": False,
              "scope": "Captured gate clients only. Directional successful TCP syscall bytes include RPC overhead; a unique key-derived payload prefix must also occur. This does not prove every payload byte individually, RPC count, worker-worker transfers, or continuous tracing of formal performance runs."}
    def remember(path, expected=None):
        record = file_record(path)
        relative = str(path.relative_to(suite))
        if expected is not None and record != {"bytes": expected["bytes"], "sha256": expected["sha256"]}:
            raise ValueError("Trace evidence changed during parsing: " + relative)
        if relative in result["inputs"] and result["inputs"][relative] != record:
            raise ValueError("Evidence changed between batch checks: " + relative)
        result["inputs"][relative] = record
    try:
        config_path = directory / "tcp-configuration.json"
        remember(config_path)
        config = json.loads(config_path.read_text())
        endpoints = configuration_endpoints(config)
        manifest_path = directory / "processes.json"
        remember(manifest_path)
        if result["inputs"][str(manifest_path.relative_to(suite))]["sha256"] != config["processes_sha256"]:
            raise ValueError("Copied Worker process manifest SHA differs from the live snapshot")
        manifest = json.loads(manifest_path.read_text())
        for worker in config["workers"]:
            flags_path = directory / f"worker-{worker['worker_index']}.flags"
            remember(flags_path)
            if result["inputs"][str(flags_path.relative_to(suite))]["sha256"] != worker["sha256"]:
                raise ValueError("Copied Worker flags SHA differs from the live snapshot")
            pairs = [line[2:].split("=", 1) for line in flags_path.read_text().splitlines() if line.startswith("--")]
            flags = dict(pairs)
            if len(flags) != len(pairs) or any(flags.get(key) != value for key, value in worker["flags"].items()):
                raise ValueError("Copied Worker TCP flags do not match the live snapshot")
            processes = [row for row in manifest.get("processes", []) if row.get("name") == f"worker{worker['worker_index']}"]
            if (len(processes) != 1 or processes[0].get("pid") != worker["pid"]
                    or str(processes[0].get("start_ticks")) != str(worker["start_ticks"])):
                raise ValueError("Copied Worker process identity differs from the live snapshot")
        result["configuration"] = {"path": str(config_path.relative_to(suite)), "allowed_worker_endpoints": endpoints,
                                   "scope": "Saved live procfs/flag observation; this offline audit does not recheck current process liveness"}
        for index in [0, 1]:
            for mode in ["insert", "upsert"]:
                gate_path = directory / f"client-{index}-{mode}.json"
                remember(gate_path)
                raw = json.loads(gate_path.read_text())
                cases = gate_cases(raw, index)
                traces = directory / f"wire-client-{index}-{mode}"
                paths = sorted(traces.glob("strace.*"))
                if not paths:
                    raise ValueError("No per-thread -ff strace files: " + str(traces))
                for path in paths:
                    remember(path)
                for count in BATCHES:
                    case = cases[count]
                    prefix = case.get("key_prefix")
                    if not isinstance(prefix, str) or not prefix:
                        raise ValueError("The correctness gate did not preserve the exact key prefix")
                    keys_digest, values_digest, first_key, prefix_hex = payload_identity(prefix, count)
                    put = selected_step(raw, count, "put_initial_native_batch")
                    get = selected_step(raw, count, "get_full_exact")
                    if (put.get("result", {}).get("requested_keys") != count
                            or get.get("result", {}).get("requested_keys") != count
                            or get["result"].get("exact_bytes") is not True
                            or put["result"].get("key_list_sha256") != keys_digest
                            or get["result"].get("key_list_sha256") != keys_digest
                            or put["result"].get("input_values_sha256") != values_digest
                            or get["result"].get("expected_values_sha256") != values_digest
                            or get["result"].get("observed_values_sha256") != values_digest):
                        raise ValueError("Gate bytes/key digest does not match the documented payload derivation")
                    windows = {"put": {key: put[key] for key in ["start_wall_ns", "end_wall_ns"]},
                               "get": {key: get[key] for key in ["start_wall_ns", "end_wall_ns"]}}
                    wire = trace_evidence(traces, windows, prefix_hex, endpoints)
                    for record in wire["trace_sha256"]:
                        remember(Path(record["path"]), expected=record)
                    errors = []
                    required = count * 1024
                    for operation, observed in wire["operations"].items():
                        if observed["successful_bytes"] < required:
                            errors.append(operation + " has insufficient successful TCP socket bytes")
                        if observed["payload_prefix_observed"] is not True:
                            errors.append(operation + " lacks its key-derived payload prefix on TCP")
                    if wire["malformed"]:
                        errors.append("Malformed syscall evidence")
                    result["cases"].append({"client_index": index, "write_mode": mode, "batch_keys": count,
                                            "gate_path": str(gate_path.relative_to(suite)), "first_key": first_key,
                                            "payload_prefix_hex": prefix_hex, "minimum_directional_bytes": required,
                                            "windows": windows, "wire": compact_wire(wire, suite),
                                            "errors": errors, "valid": not errors})
        for path, expected in list(result["inputs"].items()):
            remember(suite / path, expected)
        result["valid"] = len(result["cases"]) == 16 and all(case["valid"] for case in result["cases"])
    except Exception as error:
        result["fatal"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        result.update(end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
        result["duration_ns"] = result["end_mono_ns"] - result["start_mono_ns"]
        result["duration_s"] = result["duration_ns"] / 1e9
        result["parser_sources"] = {str(Path(__file__).name): file_record(Path(__file__)),
                                    "batch_gate.py": file_record(Path(__file__).with_name("batch_gate.py")),
                                    "tcp_probe_ds_sizes.py": file_record(Path(__file__).with_name("tcp_probe_ds_sizes.py"))}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        parser.error("Refusing to replace an immutable TCP audit")
    result = verify(args.suite)
    write_immutable(args.out, result)
    print(json.dumps({"valid": result["valid"], "cases": len(result["cases"]),
                      "fatal": result.get("fatal"), "out": str(args.out), "duration_s": result["duration_s"]}))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
