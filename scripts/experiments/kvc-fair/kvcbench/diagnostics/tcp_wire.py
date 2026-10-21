#!/usr/bin/env python3
"""Bounded bytes correctness probe; external tracing establishes the wire path."""

import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in {None, ""}:
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
import uuid

from kvcbench.run.workers import scalar as benchmark
VALUE_SIZE = 1 << 20


def utc(ns):
    seconds, nanos = divmod(ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}Z"


def stamp():
    wall = time.time_ns()
    return {"wall_ns": wall, "utc": utc(wall), "mono_ns": time.monotonic_ns()}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def network_snapshot():
    result = {"clock": stamp(), "scope": "aggregate current network namespace", "errors": []}
    try:
        result["netns"] = os.readlink("/proc/self/ns/net")
    except OSError as error:
        result["errors"].append(f"netns: {error}")
    try:
        lines = Path("/proc/net/snmp").read_text().splitlines()
        snmp = {}
        for header, values in zip(lines[::2], lines[1::2]):
            names, numbers = header.split(), values.split()
            if names[0] != numbers[0] or len(names) != len(numbers):
                raise ValueError("unpaired SNMP counter row")
            if names[0] == "Tcp:":
                snmp = dict(zip(names[1:], map(int, numbers[1:])))
        if not snmp:
            raise ValueError("TCP SNMP row missing")
        result["tcp"] = snmp
    except (OSError, ValueError, IndexError) as error:
        result["tcp"] = None
        result["errors"].append(f"snmp: {error}")
    try:
        interfaces = {}
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, counters = line.split(":", 1)
            values = list(map(int, counters.split()))
            interfaces[name.strip()] = {
                "rx_bytes": values[0], "rx_packets": values[1],
                "tx_bytes": values[8], "tx_packets": values[9],
            }
        result["interfaces"] = interfaces
    except (OSError, ValueError, IndexError) as error:
        result["interfaces"] = None
        result["errors"].append(f"netdev: {error}")
    result["finished_clock"] = stamp()
    return result


def counter_delta(before, after):
    def difference(first, last):
        if first is None or last is None:
            return None
        return {key: last[key] - value if last[key] >= value else None
                for key, value in first.items() if key in last}

    first, last = before.get("interfaces"), after.get("interfaces")
    return {
        "tcp": difference(before.get("tcp"), after.get("tcp")),
        "interfaces": ({name: difference(values, last[name])
                        for name, values in first.items() if name in last}
                       if first is not None and last is not None else None),
        "scope": "namespace aggregate; not process attributed; loopback RX/TX must not be added as application bytes",
    }


def json_safe(value):
    return json.loads(json.dumps(value, default=lambda item: {"python_type": type_name(item)}))


def type_name(value):
    return f"{type(value).__module__}.{type(value).__qualname__}"


def adapter_identity(adapter):
    attributes = vars(adapter) if hasattr(adapter, "__dict__") else {}
    return {
        "class": type_name(adapter),
        "metadata": json_safe(attributes.get("metadata")),
        "declared_transport": json_safe(attributes.get("transport")),
        "component_classes": {name: type_name(attributes[name])
                              for name in ("client", "store", "context", "transfer", "engine", "connection", "socket")
                              if name in attributes},
        "transport_decision": "unproven: inspect externally captured AF_INET/AF_INET6 socket payloads; TCP metadata/control traffic alone is insufficient",
    }


def invoke(report, operation, callback, validate=None, **details):
    before = network_snapshot()
    row = {"operation": operation, **details, "network_before": before}
    report["operations"].append(row)
    row["started"] = stamp()
    try:
        answer = callback()
    except BaseException as error:
        row["ended"] = stamp()
        row["status"] = "failed"
        row["error"] = f"{type(error).__name__}: {error}"
        raise
    else:
        row["ended"] = stamp()
        row["status"] = "ok"
    finally:
        row["duration_s"] = (row["ended"]["mono_ns"] - row["started"]["mono_ns"]) / 1e9
        row["network_after"] = network_snapshot()
        row["network_delta"] = counter_delta(before, row["network_after"])
    if validate:
        try:
            row["validation"] = validate(answer)
        except BaseException as error:
            row["status"] = "failed_validation"
            row["error"] = f"{type(error).__name__}: {error}"
            raise
    return answer


def expect_exists(expected):
    def validate(answer):
        if not isinstance(answer, bool) or answer is not expected:
            raise AssertionError(f"EXISTS expected {expected}, got {type_name(answer)} {answer!r}")
        return {"exists": answer, "expected": expected, "valid": True}
    return validate


def expect_bytes(expected):
    expected_hash = hashlib.sha256(expected).hexdigest()

    def validate(answer):
        if not isinstance(answer, bytes):
            raise AssertionError(f"GET expected bytes, got {type_name(answer)}")
        actual_hash = hashlib.sha256(answer).hexdigest()
        if answer != expected:
            raise AssertionError(f"GET exact bytes mismatch: length={len(answer)}, sha256={actual_hash}, expected={expected_hash}")
        return {"valid": True, "length": len(answer), "sha256": actual_hash, "exact_bytes_equal": True}
    return validate


def write_new(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix="." + path.name + ".", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", required=True)
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--objects", type=int, default=4)
    parser.add_argument("--client-index", type=int)
    parser.add_argument("--concurrency", type=int)
    args = parser.parse_args(argv)
    if args.out.exists() or args.out.is_symlink():
        parser.error(f"output already exists: {args.out}")
    if not 2 <= args.objects <= 16:
        parser.error("--objects must be in [2,16] (bounded correctness probe)")
    config = json.loads(args.config.read_text())
    if not isinstance(config, dict):
        parser.error("config must be a JSON object")
    index = args.client_index if args.client_index is not None else int(config.get("client_index", 0))
    concurrency = args.concurrency if args.concurrency is not None else int(config.get("concurrency", max(1, index + 1)))
    if not 0 <= index < concurrency:
        parser.error("require 0 <= client-index < concurrency")
    run_id = "tcp-probe-" + uuid.uuid4().hex
    config.update(client_index=index, concurrency=concurrency, value_size=VALUE_SIZE, run_id=run_id)
    report = {
        "schema": "kvc-tcp-wire-probe-v1", "purpose": "bounded correctness and external wire-attribution smoke; not a performance repetition",
        "product": args.product, "probe_id": run_id, "pid": os.getpid(), "started": stamp(),
        "value_size": VALUE_SIZE, "objects": args.objects, "client_index": index, "concurrency": concurrency,
        "source": {"adapter": str(args.adapter.resolve()), "adapter_sha256": sha256(args.adapter),
                   "config": str(args.config.resolve()), "config_sha256": sha256(args.config),
                   "benchmark": str(Path(benchmark.__file__).resolve()), "benchmark_sha256": sha256(benchmark.__file__),
                   "probe_sha256": sha256(__file__)},
        "environment": {name: os.environ.get(name) for name in ("MC_STORE_MEMCPY", "MC_STORE_LOCAL_HOT_CACHE_SIZE", "MC_STORE_LOCAL_HOT_CACHE_USE_SHM")},
        "observability_boundary": "Network counters cover this namespace and include unrelated/control traffic. No automatic TCP pass claim. Outer strace must show full payload on data sockets; tracing is confined to smoke, outside performance runs.",
        "key_lifecycle": "fresh UUID keys only; no existing key touched; no delete; owning service lifecycle reclaims this bounded dataset",
        "operations": [], "valid": False,
    }
    adapter = None
    try:
        adapter = invoke(report, "initialize", lambda: benchmark.load_adapter(str(args.adapter), config))
        report["adapter"] = adapter_identity(adapter)
        for index in range(args.objects):
            key = f"{run_id}-c{report['client_index']}-object-{index}"
            absent = key + "-never-written"
            value = hashlib.shake_256(key.encode()).digest(VALUE_SIZE)
            details = {"object_index": index, "key": key}
            invoke(report, "exist_miss_before_put", lambda: adapter.exists(key), expect_exists(False), **details)
            invoke(report, "put", lambda: adapter.put(key, value), value_bytes=VALUE_SIZE,
                   value_sha256=hashlib.sha256(value).hexdigest(), **details)
            invoke(report, "get", lambda: adapter.get(key), expect_bytes(value), value_bytes=VALUE_SIZE, **details)
            invoke(report, "exist_hit", lambda: adapter.exists(key), expect_exists(True), **details)
            invoke(report, "exist_miss", lambda: adapter.exists(absent), expect_exists(False),
                   object_index=index, key=absent)
        report["valid"] = True
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
    finally:
        if adapter is not None:
            try:
                invoke(report, "close", adapter.close)
            except BaseException as error:
                report["valid"] = False
                report["close_error"] = f"{type(error).__name__}: {error}"
        report["ended"] = stamp()
        report["duration_s"] = (report["ended"]["mono_ns"] - report["started"]["mono_ns"]) / 1e9
        report["status"] = "passed_correctness_wire_path_unproven" if report["valid"] else "failed"
        write_new(args.out, report)
    print(json.dumps({"out": str(args.out), "product": args.product, "status": report["status"],
                      "objects": args.objects, "operations": len(report["operations"])}))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
