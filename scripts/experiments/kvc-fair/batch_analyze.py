#!/usr/bin/env python3
"""Recompute batch statistics and resource coverage from original suite evidence."""
import argparse
import bisect
from collections import Counter, defaultdict
import csv
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

from kvcbench.core.statistics import percentile
from kvcbench.core.case_validation import finite, near, measured_case
from kvcbench.collect.resources import counter_rate, known_sum, reduce_samples

PRODUCTS = ["datasystem", "mooncake", "lmcache"]
BATCHES = [1, 16, 256, 4096]
OPERATIONS = ["put", "upsert", "get", "exist_hit", "exist_miss"]
SOURCE_SHAS = {"datasystem": "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6",
               "mooncake": "719735896c86b56fabec6cf3e825fb2ea640597a",
               "lmcache": "3e11e10812b448142c6cdb177a3058652bd826fa"}
METRICS = ["batch_qps", "keys_s", "throughput_mib_s", "p50_us", "p95_us", "p99_us",
           "total_cpu_cores", "peak_charged_memory_gib", "peak_anon_shmem_gib", "total_read_mib_s", "total_write_mib_s"]


def quantile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    at = (len(values) - 1) * fraction
    low, high = math.floor(at), math.ceil(at)
    return values[low] + (values[high] - values[low]) * (at - low)


def identity(product, repeat, batch, operation):
    return f"r{repeat}-{product}/b{batch}-{operation}"


def resource_window(row, samples, times):
    row.update(resource_samples=0, resource_span_s=None, resource_coverage=None,
               resource_interval_p50_ms=None, resource_interval_p95_ms=None, resource_max_gap_ms=None,
               peak_charged_memory_gib=None, peak_anon_shmem_gib=None,
               server_cpu_cores=None, client_cpu_cores=None, total_cpu_cores=None,
               total_read_mib_s=None, total_write_mib_s=None)
    start, end = row.get("start_mono_ns"), row.get("end_mono_ns")
    if type(start) is not int or type(end) is not int or end <= start:
        return
    inside = samples[bisect.bisect_left(times, start):bisect.bisect_right(times, end)]
    row["resource_samples"] = len(inside)
    for metric, keys in [("peak_charged_memory_gib", ["memory"]), ("peak_anon_shmem_gib", ["anon", "shmem"])]:
        values = [total / (1 << 30) for sample in inside
                  if (total := known_sum(sample[role].get(key) for role in ["server", "client"] for key in keys)) is not None]
        row[metric] = max(values) if values else None
    if len(inside) < 2:
        return
    span = (inside[-1]["mono_ns"] - inside[0]["mono_ns"]) / 1e9
    if span <= 0:
        return
    intervals = [(right["mono_ns"] - left["mono_ns"]) / 1e6 for left, right in zip(inside, inside[1:])]
    row.update(resource_span_s=span, resource_coverage=span / row["duration_s"],
               resource_interval_p50_ms=quantile(intervals, .5), resource_interval_p95_ms=quantile(intervals, .95),
               resource_max_gap_ms=max(intervals))
    for role in ["server", "client"]:
        row[role + "_cpu_cores"] = counter_rate([sample[role].get("cpu_usec") for sample in inside], span, 1e6)
        for direction in ["read", "write"]:
            row[role + "_" + direction + "_mib_s"] = counter_rate([sample[role].get(direction + "_bytes") for sample in inside], span, 1 << 20)
    row["total_cpu_cores"] = known_sum(row[role + "_cpu_cores"] for role in ["server", "client"])
    for direction in ["read", "write"]:
        row["total_" + direction + "_mib_s"] = known_sum(row[role + "_" + direction + "_mib_s"] for role in ["server", "client"])


def measured_row(raw, row, samples, times, context=None):
    result = measured_case(raw, row, context)
    resource_window(result, samples, times)
    return result


def aggregate(rows, repeats):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["product"], row["operation"], row["batch"])].append(row)
    result = []
    for (product, operation, batch), members in sorted(groups.items()):
        valid = [row for row in members if row["valid"]]
        complete = len(valid) == repeats and len({row["repeat"] for row in valid}) == repeats
        na = len(members) == repeats and all(row["status"] == "not_supported" for row in members)
        failed = (len(members) == repeats and len({row["repeat"] for row in members}) == repeats
                  and any(row["status"] == "failed" for row in members)
                  and all(row["status"] in {"valid", "failed"} for row in members))
        row = {"product": product, "operation": operation, "batch": batch,
               "status": "not_supported" if na else "complete" if complete else "failed" if failed else "incomplete",
               "valid_repeats": len(valid), "expected_repeats": repeats, "metrics": {}}
        for name in METRICS:
            values = [member.get(name) for member in valid]
            known = complete and all(finite(value) and value >= 0 for value in values)
            row["metrics"][name] = {"median": statistics.median(values) if known else None,
                                     "min": min(values) if known else None, "max": max(values) if known else None,
                                     "cv_percent": statistics.pstdev(values) / statistics.mean(values) * 100
                                     if known and statistics.mean(values) > 0 else None,
                                     "known_repeats": sum(finite(value) for value in values)}
        result.append(row)
    return result


def analyze(suite, device="253:2"):
    suite = Path(suite)
    plan = json.loads((suite / "plan.json").read_text())
    summary = json.loads((suite / "summary.json").read_text()) if (suite / "summary.json").exists() else {}
    parameters = json.loads((suite / "parameters.json").read_text()) if (suite / "parameters.json").exists() else {}
    events = [json.loads(line) for line in (suite / "events.jsonl").read_text().splitlines() if line.strip()] if (suite / "events.jsonl").exists() else []
    samples = reduce_samples(suite / "resources/samples.jsonl", device)
    times = [sample["mono_ns"] for sample in samples]
    issues, rows, expected = [], [], {}
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("Resource monotonic timestamps must strictly increase")
    repeats = len({group["repeat"] for group in plan})
    if not plan or repeats < 1:
        raise ValueError("A nonempty actual suite plan is required")
    if parameters.get("action") == "run" and (repeats != 3 or parameters.get("repeats") != 3 or parameters.get("duration") != 20 or parameters.get("warmup") != 3 or parameters.get("put_keys") != 262144):
        issues.append({"errors": ["Formal suite parameters differ from the fixed three-repeat scientific protocol"]})
    for group in plan:
        for spec in group["operations"]:
            name = identity(group["product"], group["repeat"], spec["batch"], spec["operation"])
            if name in expected:
                raise ValueError("Duplicate planned identity: " + name)
            expected[name] = (group, spec)
    full_grid = {identity(product, repeat, batch, operation) for repeat in range(repeats)
                 for product in PRODUCTS for batch in BATCHES for operation in OPERATIONS}
    if set(expected) != full_grid:
        issues.append({"errors": ["Plan differs from the complete product/batch/API/repetition protocol grid"]})
    for name, (group, spec) in expected.items():
        supported = group["product"] != "lmcache" or spec["operation"].startswith("exist") or spec["batch"] == 1 and spec["operation"] in {"put", "get"}
        if spec["supported"] is not supported:
            issues.append({"identity": name, "errors": ["Planned native support differs from the pinned capability contract"]})
    product_file = suite / "products.json"
    product_records = json.loads(product_file.read_text()) if product_file.exists() else []
    if isinstance(product_records, dict):
        product_records = product_records.get("products", [])
    product_map = {product["name"]: product for product in product_records}
    for name, (group, spec) in expected.items():
        path = suite / (name + ".json")
        row = {"identity": name, "product": group["product"], "repeat": group["repeat"],
               "batch": spec["batch"], "operation": spec["operation"], "supported": spec["supported"],
               "valid": False, "status": "missing", "evidence": "results/" + name + ".json", "issues": []}
        if path.exists():
            raw = json.loads(path.read_text())
            if raw.get("status") == "not_supported":
                if spec["supported"] or any(raw.get(key) != row[key] for key in ["product", "repeat", "batch", "operation"]):
                    row.update(status="invalid", issues=["Unplanned or identity-mismatched N/A"])
                else:
                    row.update(status="not_supported", reason=raw.get("reason"))
            else:
                if not spec["supported"]:
                    row.update(status="invalid", issues=["Measurement exists for a planned unsupported API"])
                else:
                    isolated_epochs = parameters.get("workload_protocol") == "separate-read-and-fresh-put-v1"
                    candidates = [item for item in group["operations"] if item["supported"]
                                  and (not isolated_epochs or item.get("epoch") == spec.get("epoch"))]
                    first = candidates[0]
                    context = {"run_id": suite.name + f"-r{row['repeat']}",
                               "skip_preload": spec is not first, "isolated_epochs": isolated_epochs,
                               "continue_readonly_errors": parameters.get("continue_readonly_errors") is True}
                    if row["product"] in product_map:
                        context.update({key: product_map[row["product"]][key] for key in ["adapter", "config"]})
                    else:
                        issues.append({"identity": name, "errors": ["Missing product adapter/config provenance"]})
                    if parameters.get("out"):
                        context["out"] = str(Path(parameters["out"]) / (name + ".json"))
                    if parameters.get("action") == "smoke":
                        context["protocol"] = {"duration": 1, "warmup": .1, "put_keys": 32768}
                    row = measured_row(raw, row, samples, times, context)
                    if row["status"] == "failed":
                        outcome_path = suite / name.split('/')[0] / "case-outcomes" / (path.stem + ".json")
                        outcome = json.loads(outcome_path.read_text()) if outcome_path.is_file() and not outcome_path.is_symlink() else {}
                        expected_result = str(Path(parameters.get("out", "/missing")) / (name + ".json"))
                        commands = [event for event in events if event.get("event") == "command"
                                    and isinstance(event.get("argv"), list) and "--out" in event["argv"]
                                    and event["argv"][event["argv"].index("--out") + 1:][:1] == [expected_result]]
                        failed_events = [event for event in events if event.get("event") == "measure_failed" and event.get("identity") == name]
                        if (outcome.get("identity") != name or outcome.get("returncode") != 1 or outcome.get("status") != "failed"
                                or outcome.get("read_only") is not True or outcome.get("evidence_verified") is not True
                                or outcome.get("errors") != row["native_errors"] or outcome.get("result") != expected_result
                                or outcome.get("result_sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
                                or len(commands) != 1 or commands[0].get("returncode") != 1
                                or len(failed_events) != 1 or failed_events[0].get("returncode") != 1
                                or failed_events[0].get("failures") != row["failures"]
                                or failed_events[0].get("success") != row["success"]
                                or not near(failed_events[0].get("duration_s"), row["duration_s"])):
                            row["issues"].append("Observed native failure lacks matching exit/event/result-hash evidence")
                            row.update(status="invalid", evidence_verified=False)
                        else:
                            row["outcome_evidence"] = "results/" + outcome_path.relative_to(suite).as_posix()
        if row["issues"]:
            issues.append({"identity": name, "errors": row["issues"]})
        rows.append(row)
    for path in suite.glob("r*-*/b*-*.json"):
        if path.relative_to(suite).as_posix()[:-5] not in expected:
            issues.append({"path": str(path), "errors": ["Unplanned result file"]})
    windows = sorted((row["start_mono_ns"], row["end_mono_ns"], row["identity"]) for row in rows if row["valid"] or row["status"] == "failed")
    for previous, current in zip(windows, windows[1:]):
        if previous[1] > current[0]:
            issues.append({"identities": [previous[2], current[2]], "errors": ["SDK windows overlap despite serialized experiment protocol"]})
    counts = Counter(row["status"] for row in rows)
    completed = summary.get("completed", [])
    expected_groups = {f"r{group['repeat']}-{group['product']}" for group in plan}
    if summary.get("valid") is True and (counts["missing"] or counts["invalid"] or counts["failed"] or issues or len(completed) != len(expected_groups) or set(completed) != expected_groups):
        issues.append({"errors": ["Suite claims valid but planned evidence or completed group set is incomplete"]})
    if summary.get("valid") is True and (not summary.get("ended") or summary.get("failed") or summary.get("fatal") or summary.get("monitor_exit") != 0 or summary.get("interference_exit") != 0):
        issues.append({"errors": ["Suite claims valid without a terminal successful lifecycle and normal monitor exit"]})
    if summary.get("profile") != "native-batch-tcp" or summary.get("action") != parameters.get("action"):
        issues.append({"errors": ["Suite profile/action provenance mismatch"]})
    drained = summary.get("drained", {})
    if summary.get("valid") is True and (not drained.get("checked_utc") or drained.get("populated") or any(drained.get("members", {}).values())):
        issues.append({"errors": ["Suite is missing its final empty-cgroup evidence"]})
    if parameters.get("continue_readonly_errors") is True:
        expected_failures = [row["identity"] for row in rows if row["status"] == "failed"]
        if summary.get("measurement_failures") != expected_failures:
            issues.append({"errors": ["Terminal native-failure identities differ from verified result outcomes"]})
        if summary.get("execution_complete") is True and (
                counts["missing"] or counts["invalid"] or len(completed) != len(expected_groups) or set(completed) != expected_groups
                or not summary.get("ended") or summary.get("failed") or summary.get("fatal")
                or summary.get("monitor_exit") != 0 or summary.get("interference_exit") != 0
                or not drained.get("checked_utc") or drained.get("populated") or any(drained.get("members", {}).values())
                or summary.get("valid") is not (not expected_failures)):
            issues.append({"errors": ["Suite claims complete without all terminal cases, clean lifecycle and honest validity"]})
    intervals = [(right - left) / 1e6 for left, right in zip(times, times[1:])]
    sampling = {"samples": len(samples), "interval_p50_ms": quantile(intervals, .5),
                "interval_p95_ms": quantile(intervals, .95), "interval_max_ms": max(intervals) if intervals else None}
    epochs = []
    if parameters.get("workload_protocol") == "separate-read-and-fresh-put-v1":
        from batch_epoch_verify import verify
        epoch_issues, epochs = verify(suite, plan, parameters, summary, rows, events)
        issues.extend(epoch_issues)
    elif parameters.get("action") == "run":
        issues.append({"errors": ["Formal comparison requires separate hot-read and fresh-PUT service lifetimes"]})
    return {"schema_version": 1, "profile": "native-batch-tcp", "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "suite": str(suite), "parameters": parameters, "plan": plan, "summary": summary, "rows": rows,
            "aggregates": aggregate(rows, repeats), "repeats": repeats, "counts": dict(counts), "issues": issues,
            "passed": summary.get("valid") is True and not issues and not counts["missing"] and not counts["invalid"],
            "evidence_verified": (summary.get("execution_complete") is True or summary.get("valid") is True)
                                 and not issues and not counts["missing"] and not counts["invalid"],
            "samples": samples, "sampling": sampling, "events": events, "io_device": device, "epochs": epochs,
            "workload_protocol": parameters.get("workload_protocol", "legacy-shared-service-lifetime"),
            "resource_scope": "Only samples inside each SDK monotonic window; CPU/IO use first-to-last counters and stay null below two samples. Memory is the maximum observed charged memory, not an unsampled true peak.",
            "latency_scope": "P50/P95/P99 are whole-batch merged histogram upper bounds; never divided by B. Fewer than1000 completed batches marks low-sample P99.",
            "aggregate_scope": "Medians/ranges/CV require the complete planned set of distinct valid repetitions; missing/unsupported values remain null."}


def export(analysis, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    public = {key: value for key, value in analysis.items() if key != "samples"}
    public["samples_source"] = "Original suite resources/samples.jsonl; raw evidence archive retains every native sample. Samples are not duplicated in this JSON."
    (out / "analysis.json").write_text(json.dumps(public, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    fields = ["identity", "product", "repeat", "batch", "operation", "status", "size", "concurrency", "success", "success_keys", "failures", "duration_s", *METRICS,
              "p50_lower_us", "p95_lower_us", "p99_lower_us", "low_sample_p99", "short_window", "resource_samples", "resource_span_s", "resource_coverage", "resource_interval_p50_ms", "resource_interval_p95_ms", "resource_max_gap_ms", "evidence", "outcome_evidence"]
    with (out / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(analysis["rows"])
    lines = ["# 原生批量结果重算", "", analysis["aggregate_scope"], "", analysis["latency_scope"], "", analysis["resource_scope"], "",
             "| 产品 | API | 每批 keys | 状态 | 有效重复 | keys/s 中位数 | whole-batch P99 µs | CPU 核 |", "|---|---|---:|---|---:|---:|---:|---:|"]
    for row in analysis["aggregates"]:
        def value(name):
            item = row["metrics"][name]["median"]
            return "N/A" if row["status"] == "not_supported" else "未观测" if item is None else f"{item:.4g}"
        lines.append(f"| {row['product']} | {row['operation']} | {row['batch']} | {row['status']} | {row['valid_repeats']} | {value('keys_s')} | {value('p99_us')} | {value('total_cpu_cores')} |")
    lines.extend(["", "重算问题：" + str(len(analysis["issues"])), ""])
    (out / "analysis.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="253:2")
    args = parser.parse_args()
    result = analyze(args.suite, args.device)
    export(result, args.out)
    print(json.dumps({key: result[key] for key in ["passed", "counts", "issues", "sampling"]}, ensure_ascii=False))
    raise SystemExit(0 if not result["issues"] else 1)


if __name__ == "__main__":
    main()
