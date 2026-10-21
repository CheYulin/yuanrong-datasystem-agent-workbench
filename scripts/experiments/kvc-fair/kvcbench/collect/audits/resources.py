#!/usr/bin/env python3
"""Read-only cgroup and leader-affinity audit of collected batch SDK windows."""
import argparse
import bisect
import datetime
import hashlib
import json
import math
from pathlib import Path

from kvcbench.platform.files import digest


ROLES = ("server", "client", "monitor", "parent")
EVENTS = ("low", "high", "max", "oom", "oom_kill", "oom_group_kill")
CPU = ("usage_usec", "nr_periods", "nr_throttled", "throttled_usec")


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def counter(values):
    if len(values) < 2 or any(not numeric(value) for value in values):
        return None
    if any(right < left for left, right in zip(values, values[1:])):
        return None
    return values[-1] - values[0]


def known_max(values):
    values = [value for value in values if numeric(value)]
    return max(values) if values else None


def group(sample, role):
    return sample.get("parent", {}) if role == "parent" else sample.get("groups", {}).get(role, {})


def group_audit(samples, role, span):
    records = [group(sample, role) for sample in samples]
    cpu = {name: counter([record.get("cpu.stat", {}).get(name) for record in records]) for name in CPU}
    event_tables = {}
    for table in ("memory.events", "memory.events.local"):
        event_tables[table] = {
            name: {"first": records[0].get(table, {}).get(name) if records else None,
                   "last": records[-1].get(table, {}).get(name) if records else None,
                   "delta": counter([record.get(table, {}).get(name) for record in records])}
            for name in EVENTS}
    anon_shmem = []
    for record in records:
        memory = record.get("memory.stat", {})
        anon, shmem = memory.get("anon"), memory.get("shmem")
        anon_shmem.append(anon + shmem if numeric(anon) and numeric(shmem) else None)
    periods, throttled = cpu["nr_periods"], cpu["nr_throttled"]
    return {"cpu_delta": cpu,
            "cpu_cores": cpu["usage_usec"] / 1e6 / span if span and cpu["usage_usec"] is not None else None,
            "throttled_period_fraction": throttled / periods if periods and throttled is not None else None,
            "throttled_time_wall_fraction": cpu["throttled_usec"] / 1e6 / span if span and cpu["throttled_usec"] is not None else None,
            "memory_events": event_tables,
            "peak_observed_memory_bytes": known_max([record.get("memory.current") for record in records]),
            "peak_observed_anon_shmem_bytes": known_max(anon_shmem),
            "peak_observed_swap_bytes": known_max([record.get("memory.swap.current") for record in records]),
            "historical_memory_peak_first": records[0].get("memory.peak") if records else None,
            "historical_memory_peak_last": records[-1].get("memory.peak") if records else None}


def host_busy_ticks(sample):
    try:
        line = sample["host"]["stat"].splitlines()[0].split()
        if line[0] != "cpu" or len(line) < 9:
            return None
        values = [int(value) for value in line[1:9]]
        if any(value < 0 for value in values):
            return None
        return sum(values) - values[3] - values[4]
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def process_observations(samples, role, span, clk_tck):
    totals = {}
    for left, right in zip(samples, samples[1:]):
        previous = {(p.get("pid"), p.get("start_ticks")): p for p in group(left, role).get("processes", [])}
        for process in group(right, role).get("processes", []):
            key = process.get("pid"), process.get("start_ticks")
            before = previous.get(key)
            if before is None or any(value is None for value in key):
                continue
            values = [before.get("utime_ticks"), before.get("stime_ticks"), process.get("utime_ticks"), process.get("stime_ticks")]
            if any(not numeric(value) for value in values) or values[2] < values[0] or values[3] < values[1]:
                continue
            item = totals.setdefault(key, {"pid": key[0], "start_ticks": key[1], "comm": process.get("comm"),
                                           "observed_cpu_ticks": 0, "paired_intervals": 0})
            item["observed_cpu_ticks"] += values[2] + values[3] - values[0] - values[1]
            item["paired_intervals"] += 1
    for item in totals.values():
        item["cpu_cores_over_observed_sample_span"] = item["observed_cpu_ticks"] / clk_tck / span if span and clk_tck else None
    return sorted(totals.values(), key=lambda item: item["observed_cpu_ticks"], reverse=True)[:10]


def interference_window(records, start, end):
    inside = []
    for record in records:
        at, span = record.get("mono_ns"), record.get("span_s")
        if type(at) is int and numeric(span) and span > 0 and start <= at - round(span * 1e9) and at <= end:
            inside.append(record)
    intervals = sorted((r["mono_ns"] - round(r["span_s"] * 1e9), r["mono_ns"]) for r in inside)
    covered = 0
    last = None
    for left, right in intervals:
        covered += max(0, right - max(left, last if last is not None else left))
        last = max(last, right) if last is not None else right
    span = sum(r["span_s"] for r in inside)
    values = {}
    for metric in ("external_cpu_cores", "potential_shared_cpu_cores"):
        valid = bool(inside) and all(numeric(r.get(metric)) for r in inside)
        values[metric + "_weighted_mean"] = sum(r[metric] * r["span_s"] for r in inside) / span if valid else None
        values[metric + "_max_observed"] = max(r[metric] for r in inside) if valid else None
    processes = {}
    for record in inside:
        for process in record.get("top_processes", []):
            key = process.get("pid"), process.get("start_ticks")
            if any(value is None for value in key) or not numeric(process.get("cpu_cores")):
                continue
            item = processes.setdefault(key, {"pid": key[0], "start_ticks": key[1], "comm": process.get("comm"),
                                              "cgroup": process.get("cgroup"), "listed_cpu_seconds": 0,
                                              "listed_intervals": 0, "potential_shared_in_any_listed_interval": False})
            item["listed_cpu_seconds"] += process["cpu_cores"] * record["span_s"]
            item["listed_intervals"] += 1
            item["potential_shared_in_any_listed_interval"] |= process.get("can_run_on_experiment_cpus") is True
    for item in processes.values():
        item["listed_cpu_cores_over_included_intervals"] = item["listed_cpu_seconds"] / span if span else None
    return {"fully_contained_intervals": len(inside), "included_interval_seconds": span if inside else None,
            "union_coverage": covered / (end - start) if end > start and inside else None, **values,
            "top_listed_processes": sorted(processes.values(), key=lambda item: item["listed_cpu_seconds"], reverse=True)[:10]}


def window_audit(samples, times, interference, start, end, clk_tck):
    inside = samples[bisect.bisect_left(times, start):bisect.bisect_right(times, end)]
    span = (inside[-1]["mono_ns"] - inside[0]["mono_ns"]) / 1e9 if len(inside) > 1 else None
    groups = {role: group_audit(inside, role, span) for role in ROLES}
    host_delta = counter([host_busy_ticks(sample) for sample in inside])
    host_cores = host_delta / clk_tck / span if span and clk_tck and host_delta is not None else None
    parent_cores = groups["parent"]["cpu_cores"]
    residual = host_cores - parent_cores if host_cores is not None and parent_cores is not None else None
    residual_negative = residual is not None and residual < 0
    return {"start_mono_ns": start, "end_mono_ns": end, "sdk_duration_s": (end - start) / 1e9,
            "sample_count": len(inside), "first_sample_mono_ns": inside[0]["mono_ns"] if inside else None,
            "last_sample_mono_ns": inside[-1]["mono_ns"] if inside else None,
            "sample_span_s": span, "sample_coverage": span * 1e9 / (end - start) if span else None,
            "groups": groups, "host_busy_cpu_cores": host_cores,
            "host_minus_parent_cpu_cores_estimate": None if residual_negative else residual,
            "host_minus_parent_negative_difference": residual_negative,
            "process_cpu_observations": {role: process_observations(inside, role, span, clk_tck) for role in ROLES if role != "parent"},
            "external_leader_affinity_estimate": interference_window(interference, start, end)}


def sample_for_audit(raw):
    def selected(record, keys):
        return {key: record[key] for key in keys if key in record}
    def counters(record, key, keys):
        value = record.get(key)
        return selected(value, keys) if isinstance(value, dict) else value
    def compact_group(record):
        result = selected(record, ('memory.current', 'memory.peak', 'memory.swap.current', 'io.stat'))
        for key, fields in [('cpu.stat', CPU), ('memory.stat', ('anon', 'shmem')),
                            ('memory.events', EVENTS), ('memory.events.local', EVENTS)]:
            if key in record:
                result[key] = counters(record, key, fields)
        if 'processes' in record:
            result['processes'] = [selected(process, ('pid', 'start_ticks', 'comm', 'utime_ticks', 'stime_ticks'))
                                   for process in record['processes']]
        return result
    result = selected(raw, ('mono_ns', 'tool_returncodes'))
    if 'host' in raw:
        host = raw['host']
        if isinstance(host, dict):
            host = selected(host, ('stat',))
            if isinstance(host.get('stat'), str):
                host['stat'] = host['stat'].split('\n', 1)[0]
        result['host'] = host
    if 'parent' in raw:
        result['parent'] = compact_group(raw['parent'])
    if 'groups' in raw:
        result['groups'] = {role: compact_group(raw['groups'][role])
                            for role in ROLES if role != 'parent' and role in raw['groups']}
    return result


def read_jsonl(path, required=False, project=None):
    if not path.exists() and not required:
        return []
    records, previous = [], None
    with path.open() as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            at = record.get('mono_ns')
            if type(at) is not int or previous is not None and at <= previous:
                raise ValueError('Raw samples must have strictly increasing monotonic timestamps: ' + str(path))
            previous = at
            records.append(project(record) if project else record)
    return records


def read_samples(path):
    return read_jsonl(path, required=True, project=sample_for_audit)


def file_record(path, suite):
    return {"path": path.relative_to(suite).as_posix(), "sha256": digest(path), "bytes": path.stat().st_size}


def audit(suite):
    suite = Path(suite).resolve(strict=True)
    sample_file = suite / "resources/samples.jsonl"
    interference_file = suite / "resources/interference.jsonl"
    manifest_file = suite / "resources/monitor-manifest.json"
    summary_file = suite / "summary.json"
    samples = read_samples(sample_file)
    interference = read_jsonl(interference_file)
    manifest = json.loads(manifest_file.read_bytes()) if manifest_file.exists() else {}
    summary = json.loads(summary_file.read_bytes())
    clk_tck = manifest.get("clk_tck")
    if not numeric(clk_tck) or clk_tck <= 0:
        clk_tck = None
    times = [sample["mono_ns"] for sample in samples]
    inputs = [sample_file, summary_file]
    inputs += [path for path in [interference_file, manifest_file] if path.exists()]
    windows, unsupported, unmeasured = [], [], []
    for path in sorted(suite.glob("r*-*/b*-*.json")):
        raw = json.loads(path.read_bytes())
        inputs.append(path)
        if raw.get("status") == "not_supported":
            unsupported.append(path.relative_to(suite).as_posix())
            continue
        start, end = raw.get("measure_start_mono_ns"), raw.get("measure_end_mono_ns")
        if type(start) is not int or type(end) is not int or end <= start:
            if raw.get("valid") is True:
                raise ValueError("Invalid SDK monotonic window: " + str(path))
            unmeasured.append(path.relative_to(suite).as_posix())
            continue
        result = window_audit(samples, times, interference, start, end, clk_tck)
        result["identity"] = path.relative_to(suite).as_posix()[:-5]
        result["result_valid_recorded"] = raw.get("valid")
        result["result_success_recorded"] = raw.get("success")
        result["result_failures_recorded"] = raw.get("failures")
        result["result_fatal_recorded"] = raw.get("fatal")
        windows.append(result)
    whole = window_audit(samples, times, interference, times[0], times[-1], clk_tck) if len(times) > 1 else None
    return {"schema_version": 2, "kind": "native_batch_collected_resource_audit",
            "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "suite": str(suite),
            "suite_valid_recorded": summary.get("valid"), "sdk_window_count": len(windows),
            "valid_sdk_window_count": sum(row["result_valid_recorded"] is True for row in windows),
            "invalid_sdk_window_count": sum(row["result_valid_recorded"] is not True for row in windows),
            "unmeasured_results": unmeasured,
            "unsupported_count": len(unsupported), "unsupported": unsupported, "windows": windows,
            "whole_monitor_span": whole, "monitor_commands": manifest.get("commands"),
            "monitor_end_utc": manifest.get("end_utc"), "suite_monitor_exit": summary.get("monitor_exit"),
            "suite_interference_exit": summary.get("interference_exit"),
            "inputs": [file_record(path, suite) for path in inputs],
            "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scope": ["Read-only postprocessing, not another measurement or benchmark validity gate.",
                      "SDK-window counters use only samples whose monotonic timestamps lie inside that window; suite counters span the monitor first/last sample, including setup and teardown.",
                      "Counter deltas require at least two observations, all present and nondecreasing; missing/reset data remain null. Nonzero historical baselines are not new events.",
                      "Parent cgroup counters include children: parent/monitor are displayed separately and never added to server+client. memory.events may propagate; do not sum hierarchy levels.",
                      "Memory is maximum observed memory.current or anon+shmem, not an unsampled true peak. memory.peak first/last is historical and never used as the window peak.",
                      "Host busy CPU excludes idle/iowait and guest double counting; host-minus-parent is only an asynchronous-counter estimate, with negative differences null.",
                      "External process figures are the collector's leader-affinity estimates, not exact thread placement, causality or an interference bound. Only fully contained collector intervals are used; missing intervals remain unknown.",
                      "Process CPU lists pair PID plus start_ticks across adjacent samples. New/exited/short-lived processes may be missed; external top lists are truncated. These lists are not a complete CPU accounting sum.",
                      "throttled_usec divided by wall time is a recorded cgroup counter ratio, not per-request added latency or a causal explanation of throughput."]}


def markdown(result):
    lines = ["# 原生批量资源审计", "", f"SDK 窗：{result['sdk_window_count']}；N/A：{result['unsupported_count']}。仅后处理原始证据。", "",
             "| SDK 窗 | 采样数 | 覆盖 | server throttle µs | client throttle µs | 外部潜在共享 CPU 估计 |", "|---|---:|---:|---:|---:|---:|"]
    def cell(value):
        return "未知" if value is None else f"{value:.5g}" if type(value) is float else str(value)
    for row in result["windows"]:
        values = [row["identity"], row["sample_count"], row["sample_coverage"],
                  row["groups"]["server"]["cpu_delta"]["throttled_usec"], row["groups"]["client"]["cpu_delta"]["throttled_usec"],
                  row["external_leader_affinity_estimate"]["potential_shared_cpu_cores_weighted_mean"]]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "口径：", "", *["- " + text for text in result["scope"]], ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    suite, out = args.suite.resolve(strict=True), args.out.resolve()
    if out == suite or suite in out.parents or out in suite.parents or out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh output directory outside the raw suite")
    result = audit(suite)
    out.mkdir(parents=True, exist_ok=True)
    (out / "resource-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (out / "resource-audit.md").write_text(markdown(result))
    print(json.dumps({"sdk_windows": result["sdk_window_count"], "unsupported": result["unsupported_count"], "out": str(out)}))


if __name__ == "__main__":
    main()
