"""Native whole-batch evidence checks shared by control-plane admission and analysis."""
from collections import Counter
import math
import re

from kvcbench.core.statistics import percentile

SOURCE_SHAS = {'datasystem': '2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6', 'mooncake': '719735896c86b56fabec6cf3e825fb2ea640597a', 'lmcache': '3e11e10812b448142c6cdb177a3058652bd826fa'}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)



def near(left, right):
    return finite(left) and finite(right) and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-9)



def measured_case(raw, row, context=None):
    errors = []
    context = context or {}
    parameters = raw.get("parameters", {})
    protocol = {"op": row["operation"], "batch": row["batch"], "size": 1024, "concurrency": 8,
                "pool_keys": 65536, "put_keys": 262144, "duration": 20, "warmup": 3}
    protocol.update(context.get("protocol", {}))
    if context.get("isolated_epochs"):
        protocol["put_warmup_keys"] = 32768
    observed_failure = (context.get("continue_readonly_errors") is True and raw.get("valid") is False
                        and row["operation"] in {"get", "exist_hit", "exist_miss"}
                        and type(raw.get("failures")) is int and raw["failures"] > 0)
    protocol.update({key: context[key] for key in ["adapter", "config", "run_id", "out", "skip_preload"] if key in context})
    for key, expected in protocol.items():
        if parameters.get(key) != expected:
            errors.append(f"parameters.{key} mismatch")
    phases = [("setup", "ready"), ("warmup", "warm"), ("clients", None), ("post_validation", None), ("closed", "closed")]
    for phase, flag in phases:
        records = raw.get(phase, [])
        ids = [item.get("client") for item in records if isinstance(item, dict)] if isinstance(records, list) else []
        if len(ids) != 8 or any(type(value) is not int for value in ids) or set(ids) != set(range(8)):
            errors.append(phase + " does not identify every distinct client")
        if isinstance(records, list) and any(not isinstance(item, dict) or item.get("fatal") or flag and item.get(flag) is not True for item in records):
            errors.append(phase + " contains failed or incomplete client acknowledgements")
    exits = raw.get("process_exit_codes")
    if not isinstance(exits, list) or len(exits) != 8 or any(type(code) is not int or code != 0 for code in exits):
        errors.append("Every client process must exit normally")
    if raw.get("fatal") or raw.get("exit_error"):
        errors.append("Result contains a fatal or process-exit error")
    row.update(status="valid" if raw.get("valid") is True else "invalid", valid=False,
               size=parameters.get("size"), concurrency=parameters.get("concurrency"),
               start_mono_ns=raw.get("measure_start_mono_ns"), end_mono_ns=raw.get("measure_end_mono_ns"),
               start_wall_ns=raw.get("measure_start_wall_ns"), failures=raw.get("failures"),
               partial_success_keys_unknown=raw.get("partial_success_keys_unknown"),
               fatal=raw.get("fatal"), client_metadata=[client.get("metadata") for client in raw.get("setup", [])])
    clients = raw.get("clients", [])
    hist = Counter()
    if len(clients) != 8:
        errors.append("Expected eight client observations")
    count = failed = 0
    private_pool = protocol["pool_keys"] // 8
    expected_target = protocol["put_keys"] // 8 // row["batch"]
    post = {item.get("client"): item for item in raw.get("post_validation", []) if isinstance(item, dict)}
    for setup in raw.get("setup", []):
        if setup.get("pool_keys") != private_pool or not isinstance(setup.get("metadata"), dict):
            errors.append("Setup key pool or native metadata mismatch")
        elif setup["metadata"].get("source_sha") != SOURCE_SHAS.get(row["product"]):
            errors.append("Client source identity differs from the pinned product")
        if type(parameters.get("skip_preload")) is not bool or setup.get("preloaded") is not (not parameters.get("skip_preload")):
            errors.append("Setup preload acknowledgement differs from the recorded execution phase")
    if context.get("isolated_epochs"):
        warmed = 4096 if row["operation"] == "put" else 0
        if any(item.get("put_inserted_keys") != warmed or item.get("put_verified_keys") != warmed for item in raw.get("warmup", [])):
            errors.append("PUT warmup did not insert and read back the fixed 32768-key background")
    for client in clients:
        success, failures = client.get("success"), client.get("failures")
        if type(success) is not int or success < 0 or type(failures) is not int or failures < 0:
            errors.append("Invalid client success/failure counts")
            continue
        count += success
        failed += failures
        client_errors = client.get("errors")
        if observed_failure:
            if (failures not in (0, 1) or not isinstance(client_errors, list) or len(client_errors) != failures
                    or any(not isinstance(error, str) or not re.match(r"^RuntimeError: code: \[[^\]\r\n]+\](?:,|\s|$)", error) for error in client_errors)):
                errors.append("Read-only failure lacks exact native error accounting")
        elif client_errors:
            errors.append("Client records native batch errors")
        if client.get("target") != expected_target:
            errors.append("Client fixed-volume target differs from the independent protocol")
        first, last = client.get("first_ns"), client.get("last_ns")
        start_mono = row["start_mono_ns"]
        if any(type(value) is not int for value in [first, last, start_mono]) or first < start_mono or last < first:
            errors.append("Client monotonic window is invalid")
        elif row["operation"] != "put" and not (observed_failure and failures) and last < start_mono + int(protocol["duration"] * 1e9):
            errors.append("Client did not finish the full timed protocol window")
        expected_checks = ((success - 1) // 128 + 1) * row["batch"] if row["operation"] == "get" and success else 0
        if client.get("validated_keys") != expected_checks:
            errors.append("Sampled full-batch GET content checks are incomplete")
        record = post.get(client.get("client"), {})
        post_expected = len({0, success // 2, success - 1}) * row["batch"] if row["operation"] == "put" and success else min(success * row["batch"], private_pool) if row["operation"] == "upsert" else 0
        restored = private_pool if row["operation"] == "upsert" and success else 0
        if record.get("post_validated_keys") != post_expected or record.get("restored_verified_keys") != restored:
            errors.append("Post-measure bytes verification or UPSERT restoration is incomplete")
        try:
            client_hist = {int(key): value for key, value in client.get("histogram", {}).items()}
            if any(key < 384 or type(value) is not int or value < 0 for key, value in client_hist.items()) or sum(client_hist.values()) != success:
                errors.append("Client histogram count mismatch")
            else:
                hist.update(client_hist)
        except (ValueError, TypeError):
            errors.append("Malformed client histogram")
    try:
        if dict(hist) != {int(key): value for key, value in raw.get("histogram", {}).items()}:
            errors.append("Merged histogram mismatch")
    except (ValueError, TypeError):
        errors.append("Malformed merged histogram")
    start, end = row["start_mono_ns"], row["end_mono_ns"]
    duration = (end - start) / 1e9 if type(start) is int and type(end) is int and end > start else None
    if clients and (any(type(client.get("last_ns")) is not int for client in clients) or max(client["last_ns"] for client in clients) != end):
        errors.append("Measurement end differs from the last client completion")
    if duration is None or not near(duration, raw.get("duration_s")):
        errors.append("Monotonic duration mismatch")
    if count != raw.get("success") or count * row["batch"] != raw.get("success_keys") or failed != raw.get("failures"):
        errors.append("Batch/key success or failure accounting mismatch")
    if raw.get("failed_keys_upper_bound") != failed * row["batch"] or raw.get("partial_success_keys_unknown") is not bool(failed):
        errors.append("Unknown partial-success accounting mismatch")
    if (failed or count <= 0) and not observed_failure:
        errors.append("Measurement has failed or zero completed batches")
    if row["operation"] == "put" and any(client.get("success") != expected_target for client in clients):
        errors.append("Fresh PUT did not finish its fixed volume")
    row.update(success=count, success_keys=count * row["batch"], duration_s=duration,
               batch_qps=count / duration if duration else None, keys_s=count * row["batch"] / duration if duration else None,
               throughput_mib_s=count * row["batch"] * parameters.get("size", 0) / duration / (1 << 20)
               if duration and row["operation"] in {"put", "upsert", "get"} else None,
               low_sample_p99=count < 1000, short_window=duration is not None and duration < 10)
    for name in ["batch_qps", "keys_s", "throughput_mib_s"]:
        if row[name] is None:
            if raw.get(name) is not None:
                errors.append(name + " must be null for this operation")
        elif not near(row[name], raw.get(name)):
            errors.append(name + " differs from completed batches / whole window")
    for name, fraction in [("p50", .5), ("p95", .95), ("p99", .99)]:
        result = percentile(hist, fraction) if count and sum(hist.values()) == count else None
        if result != raw.get(name):
            errors.append(name + " merged bucket mismatch")
        row[name + "_us"] = result["upper_us"] if result else None
        row[name + "_lower_us"] = result["lower_us"] if result else None
    if type(row["start_wall_ns"]) is int and duration:
        row.update(start_wall_ms=row["start_wall_ns"] // 1_000_000,
                   end_wall_ms=(row["start_wall_ns"] + end - start) // 1_000_000)
    row["post_validated_keys"] = sum(item.get("post_validated_keys", 0) for item in raw.get("post_validation", []))
    row["issues"] = errors
    row["valid"] = raw.get("valid") is True and not errors
    row["evidence_verified"] = (row["valid"] or observed_failure) and not errors
    row["status"] = "valid" if row["valid"] else "failed" if row["evidence_verified"] and observed_failure else "invalid"
    row["native_errors"] = [{"client": item.get("client"), "errors": item.get("errors", [])}
                            for item in clients if item.get("errors")] if observed_failure else []
    return row
