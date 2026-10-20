#!/usr/bin/env python3
"""Correctness-only native batch probes with step timings; never starts services."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import traceback
import uuid


OPERATIONS = {"put": "put_batch", "get": "get_batch", "exists": "exists_batch"}


def load_adapter(path, config):
    spec = importlib.util.spec_from_file_location("_kvc_correctness_batch_adapter", path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load batch adapter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.new_adapter(config)


def payload(key, version, size):
    return hashlib.shake_256(f"kvc-batch-gate-v1\0{key}\0{version}".encode()).digest(size)


def digest_items(items):
    digest = hashlib.sha256()
    for item in items:
        value = item.encode() if isinstance(item, str) else item
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def supports(metadata, operation, count):
    caps = metadata.get("capabilities")
    if not isinstance(caps, dict):
        raise ValueError("Adapter metadata must declare capabilities")
    entry = caps.get(operation, caps.get(OPERATIONS[operation]))
    if type(entry) is bool:
        maximum = metadata.get("max_batch_keys", {}).get(operation)
        if maximum is not None and (type(maximum) is not int or maximum <= 0):
            raise ValueError("Invalid native batch limit in metadata")
        return entry and (maximum is None or count <= maximum)
    if isinstance(entry, dict):
        sizes = entry.get("supported_batch_keys")
        if not isinstance(sizes, list) or any(type(size) is not int or size <= 0 for size in sizes):
            raise ValueError("Invalid supported_batch_keys in metadata")
        return count in sizes
    raise ValueError(f"Missing capability for {operation}")


class GateMismatch(AssertionError):
    def __init__(self, message, details):
        self.details = details
        super().__init__(message)


class StepFailed(Exception):
    pass


def read_exact(adapter, keys, expected):
    observed = adapter.get_batch(keys)
    details = {"requested_keys": len(keys), "key_list_sha256": digest_items(keys),
               "expected_values_sha256": digest_items(expected)}
    if not isinstance(observed, list) or len(observed) != len(keys):
        details.update(returned_type=type(observed).__name__,
                       returned_keys=len(observed) if isinstance(observed, list) else None)
        raise GateMismatch("GET batch result type or cardinality mismatch", details)
    invalid = [i for i, value in enumerate(observed) if not isinstance(value, bytes)]
    if invalid:
        details["invalid_byte_indices"] = invalid[:16]
        raise GateMismatch("GET did not return complete bytes", details)
    details["observed_values_sha256"] = digest_items(observed)
    mismatches = [i for i, (got, want) in enumerate(zip(observed, expected)) if got != want]
    anchors = sorted({0, len(keys) // 2, len(keys) - 1, *mismatches[:4]})
    details["anchors"] = [{"index": i, "key": keys[i],
                           "expected_sha256": hashlib.sha256(expected[i]).hexdigest(),
                           "observed_sha256": hashlib.sha256(observed[i]).hexdigest()} for i in anchors]
    details.update(exact_bytes=not mismatches, returned_keys=len(observed), mismatched_keys=len(mismatches))
    if mismatches:
        raise GateMismatch("GET full bytes or requested order mismatch", details)
    return details


def exists_exact(adapter, keys, expected):
    observed = adapter.exists_batch(keys)
    details = {"requested_keys": len(keys), "key_list_sha256": digest_items(keys),
               "expected_bits": "".join("1" if value else "0" for value in expected)}
    if not isinstance(observed, list) or len(observed) != len(keys) or any(type(value) is not bool for value in observed):
        details.update(returned_type=type(observed).__name__,
                       returned_keys=len(observed) if isinstance(observed, list) else None)
        raise GateMismatch("EXIST did not return one boolean per requested key", details)
    details["observed_bits"] = "".join("1" if value else "0" for value in observed)
    details.update(hit_keys=sum(observed), miss_keys=len(observed) - sum(observed), exact_vector=observed == expected)
    if observed != expected:
        raise GateMismatch("EXIST full sparse vector or requested order mismatch", details)
    return details


class Recorder:
    def __init__(self):
        self.steps = []

    def step(self, name, function, **identity):
        record = {"name": name, **identity, "benchmark_eligible": False,
                  "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns()}
        self.steps.append(record)
        try:
            result = function()
            json.dumps(result, allow_nan=False)
            record.update(status="passed", result=result)
            return result
        except Exception as error:
            record.update(status="failed", error={"type": type(error).__name__, "message": str(error),
                                                  "traceback": traceback.format_exc()})
            details = getattr(error, "details", None)
            if details is not None:
                try:
                    record["error"]["details"] = json.loads(json.dumps(details, allow_nan=False))
                except (TypeError, ValueError):
                    record["error"]["details_unserializable_type"] = type(details).__name__
            if hasattr(error, "failed_keys"):
                record["error"]["failed_keys"] = list(error.failed_keys)
            raise StepFailed(name) from error
        finally:
            record.update(end_mono_ns=time.monotonic_ns(), end_wall_ns=time.time_ns())
            record["duration_ns"] = record["end_mono_ns"] - record["start_mono_ns"]
            record["duration_s"] = record["duration_ns"] / 1e9

    def not_applicable(self, name, reason, **identity):
        self.steps.append({"name": name, **identity, "status": "not_applicable", "reason": reason,
                           "benchmark_eligible": False, "duration_ns": None, "duration_s": None})


def _put(adapter, keys, values):
    result = adapter.put_batch(keys, values)
    if result is not None:
        raise GateMismatch("put_batch must return None after completion", {"returned_type": type(result).__name__})
    return {"adapter_calls": 1, "requested_keys": len(keys), "key_list_sha256": digest_items(keys),
            "input_values_sha256": digest_items(values)}


def _scalar_setup(adapter, keys, values):
    for key, value in zip(keys, values):
        _put(adapter, [key], [value])
    return {"adapter_calls": len(keys), "keys_per_call": 1, "setup_scalar_only": True,
            "requested_keys": len(keys), "input_values_sha256": digest_items(values)}


def _scalar_read(adapter, keys, values):
    checks = [read_exact(adapter, [key], [value]) for key, value in zip(keys, values)]
    return {"adapter_calls": len(keys), "keys_per_call": 1, "scalar_correctness_only": True, "checks": checks}


def _require(condition, message):
    if not condition:
        raise ValueError(message)
    return {"required_capability": True}


def check_case(recorder, adapter, metadata, count, prefix):
    keys = [prefix + "k" + str(i) for i in range(count)]
    absent = [prefix + "m" + str(i) for i in range(count)]
    values = [payload(key, 0, metadata["gate_value_size"]) for key in keys]
    changed = [payload(key, 1, metadata["gate_value_size"]) for key in keys]
    if len(set(values)) != count or any(old == new for old, new in zip(values, changed)):
        raise ValueError("Payload derivation failed distinct-key/version checks")
    put_native, get_native = supports(metadata, "put", count), supports(metadata, "get", count)
    case = {"batch_keys": count, "key_prefix": prefix, "initial_payload_version": 0,
            "put_native_supported": put_native, "get_native_supported": get_native, "valid": False}
    def step(name, function, **identity):
        return recorder.step(name, function, batch_keys=count, **identity)
    if not supports(metadata, "exists", count):
        step("require_native_exists", lambda: _require(False, "Native EXISTS is required for every gate batch"))
    step("fresh_keys_initially_absent", lambda: exists_exact(adapter, keys, [False] * count), native_batch=True)
    if put_native:
        step("put_initial_native_batch", lambda: _put(adapter, keys, values), native_batch=True)
    else:
        recorder.not_applicable("put_initial_native_batch", "Native PUT does not support this batch cardinality", batch_keys=count)
        if not supports(metadata, "put", 1):
            step("require_scalar_setup", lambda: _require(False, "Single-key PUT setup is unavailable"))
        step("setup_scalar_puts", lambda: _scalar_setup(adapter, keys, values), native_batch=False, setup_scalar_only=True)
    indices = sorted({0, count // 2, count - 1})
    subset = list(dict.fromkeys([count - 1, 0, count // 2, count // 3]))
    if get_native:
        step("get_full_exact", lambda: read_exact(adapter, keys, values), native_batch=True)
        step("get_reverse_exact", lambda: read_exact(adapter, keys[::-1], values[::-1]), native_batch=True)
        if supports(metadata, "get", len(subset)):
            step("get_subset_order_exact", lambda: read_exact(adapter, [keys[i] for i in subset], [values[i] for i in subset]), native_batch=True)
        else:
            recorder.not_applicable("get_subset_order_exact", "Native GET does not support this subset cardinality", batch_keys=count)
    else:
        recorder.not_applicable("get_full_exact", "Native GET does not support this batch cardinality", batch_keys=count)
        recorder.not_applicable("get_reverse_exact", "Native GET does not support this batch cardinality", batch_keys=count)
        recorder.not_applicable("get_subset_order_exact", "Native GET does not support this batch cardinality", batch_keys=count)
        if not supports(metadata, "get", 1):
            step("require_scalar_get", lambda: _require(False, "Single-key GET correctness probes are unavailable"))
        step("get_scalar_mapping_spotchecks", lambda: _scalar_read(adapter, [keys[i] for i in indices], [values[i] for i in indices]), native_batch=False, scalar_correctness_only=True)
    step("exists_full_hit", lambda: exists_exact(adapter, keys, [True] * count), native_batch=True)
    if count > 1:
        bits = [i % 3 != 1 for i in range(count)]
        mixed = [keys[i] if bits[i] else absent[i] for i in range(count)]
        step("exists_mixed_sparse", lambda: exists_exact(adapter, mixed, bits), native_batch=True)
        step("exists_mixed_reverse", lambda: exists_exact(adapter, mixed[::-1], bits[::-1]), native_batch=True)
    else:
        recorder.not_applicable("exists_mixed_sparse", "A one-key vector cannot contain both hit and miss; each state is checked separately", batch_keys=count)
    step("exists_all_miss", lambda: exists_exact(adapter, absent, [False] * count), native_batch=True)
    semantics = metadata.get("native_semantics", {}).get("write_existing_key")
    if semantics not in {"success_without_rewrite", "insert_or_update"}:
        step("require_existing_write_semantics", lambda: _require(False, "Adapter must declare existing-key write semantics"))
    expected = values if semantics == "success_without_rewrite" else changed
    if put_native:
        step("write_existing_native_control", lambda: _put(adapter, keys, changed), native_batch=True,
             expected_existing_key_semantics=semantics)
        if get_native:
            step("verify_existing_write_full_exact", lambda: read_exact(adapter, keys, expected), native_batch=True)
        else:
            step("verify_existing_write_scalar_spotchecks", lambda: _scalar_read(adapter, [keys[i] for i in indices], [expected[i] for i in indices]), native_batch=False, scalar_correctness_only=True)
    else:
        step("write_existing_scalar_control", lambda: _scalar_setup(adapter, [keys[i] for i in indices], [changed[i] for i in indices]), native_batch=False,
             scalar_correctness_only=True, expected_existing_key_semantics=semantics)
        step("verify_existing_write_scalar_spotchecks", lambda: _scalar_read(adapter, [keys[i] for i in indices], [expected[i] for i in indices]), native_batch=False, scalar_correctness_only=True)
    step("exists_after_existing_write", lambda: exists_exact(adapter, keys, [True] * count), native_batch=True)
    case.update(valid=True, expected_existing_key_semantics=semantics,
                semantic_verification_keys=count if put_native and get_native else len(indices))
    return case


def run_gate(adapter_path, config, *, client_index, batch_sizes, value_size, run_id, namespace=None, loader=load_adapter):
    if type(client_index) is not int or client_index < 0 or type(value_size) is not int or value_size <= 0:
        raise ValueError("client_index must be nonnegative and value_size positive")
    if not batch_sizes or len(set(batch_sizes)) != len(batch_sizes) or any(type(size) is not int or size <= 0 for size in batch_sizes):
        raise ValueError("batch_sizes must be distinct positive integers")
    if not isinstance(config, dict) or not isinstance(run_id, str) or not run_id:
        raise ValueError("config must be an object and run_id a nonempty string")
    nonce = namespace if namespace is not None else uuid.uuid4().hex
    nonce = hashlib.sha256(str(nonce).encode()).hexdigest()[:24]
    prefix = "bg" + hashlib.sha256(run_id.encode()).hexdigest()[:12] + "n" + nonce + "c" + str(client_index)
    settings = dict(config, client_index=client_index, value_size=value_size, run_id=run_id)
    recorder = Recorder()
    result = {"schema_version": 1, "kind": "native_batch_correctness_gate", "benchmark_eligible": False,
              "adapter": str(adapter_path), "client_index": client_index, "value_size": value_size,
              "batch_sizes": list(batch_sizes), "run_id": run_id, "key_namespace": prefix,
              "payload_derivation": "SHAKE256(UTF8(kvc-batch-gate-v1 + NUL + key + NUL + version), value_size)",
              "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(),
              "steps": recorder.steps, "cases": [], "valid": False,
              "scope": "API correctness probes, not a wire-protocol proof or timed benchmark; no retries; SDK-internal retries remain product behavior"}
    adapter = None
    failed = False
    try:
        holder = {}
        def initialize():
            holder["adapter"] = loader(adapter_path, settings)
            return {"initialized": True}
        try:
            recorder.step("initialize_adapter", initialize)
        finally:
            adapter = holder.get("adapter")
        def inspect_metadata():
            metadata = getattr(adapter, "metadata")
            metadata = metadata() if callable(metadata) else metadata
            if not isinstance(metadata, dict):
                raise ValueError("Adapter metadata must be a JSON object")
            metadata = json.loads(json.dumps(metadata, allow_nan=False))
            for operation, method in OPERATIONS.items():
                if not callable(getattr(adapter, method, None)):
                    raise ValueError("Adapter lacks " + method)
                supports(metadata, operation, 1)
            return metadata
        metadata = recorder.step("inspect_metadata", inspect_metadata)
        result["metadata"] = metadata
        case_metadata = dict(metadata, gate_value_size=value_size)
        for index, count in enumerate(batch_sizes):
            try:
                result["cases"].append(check_case(recorder, adapter, case_metadata, count, prefix + "b" + str(count)))
            except StepFailed:
                failed = True
                result["cases"].append({"batch_keys": count, "valid": False, "status": "failed"})
                for remaining in batch_sizes[index + 1:]:
                    result["cases"].append({"batch_keys": remaining, "valid": False, "status": "not_run_after_failure"})
                break
    except StepFailed:
        failed = True
    except Exception as error:
        failed = True
        result["fatal"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    finally:
        if adapter is not None:
            try:
                recorder.step("close_adapter", lambda: adapter.close())
            except StepFailed:
                failed = True
        result.update(end_mono_ns=time.monotonic_ns(), end_wall_ns=time.time_ns())
        result["duration_ns"] = result["end_mono_ns"] - result["start_mono_ns"]
        result["duration_s"] = result["duration_ns"] / 1e9
        result["valid"] = not failed and len(result["cases"]) == len(batch_sizes) and all(case["valid"] for case in result["cases"])
        result["failed_steps"] = [step["name"] for step in recorder.steps if step["status"] == "failed"]
    return result


def write_immutable(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    descriptor, pending = tempfile.mkstemp(prefix=path.name + ".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(pending, path)
    finally:
        os.unlink(pending)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--client-index", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 16, 256, 4096])
    parser.add_argument("--value-size", type=int, default=1024)
    parser.add_argument("--write-mode", choices=["insert", "upsert"])
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        parser.error("Refusing to execute: immutable gate output already exists")
    config = json.loads(args.config.read_text())
    if args.write_mode is not None:
        config["write_mode"] = args.write_mode
    result = run_gate(args.adapter, config, client_index=args.client_index, batch_sizes=args.batch_sizes,
                      value_size=args.value_size, run_id=args.run_id)
    write_immutable(args.out, result)
    print(json.dumps({"valid": result["valid"], "out": str(args.out), "client_index": args.client_index,
                      "cases": len(result["cases"]), "failed_steps": result["failed_steps"],
                      "duration_s": result["duration_s"]}))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
