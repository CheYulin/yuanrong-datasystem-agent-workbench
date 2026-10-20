#!/usr/bin/env python3
"""Sample only task cgroup counters; requested period is not a timing guarantee."""
import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import statistics
import time


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def distribution(values):
    ordered = sorted(values)
    def percentile(fraction):
        return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))] if ordered else None
    return {"count": len(ordered), "min": min(ordered) if ordered else None,
            "p50": percentile(0.5), "p95": percentile(0.95), "max": max(ordered) if ordered else None,
            "mean": statistics.fmean(ordered) if ordered else None}


def group_stats(path):
    result, errors = {}, []
    for name in ["cpu.stat", "memory.current", "memory.stat", "memory.events", "io.stat"]:
        try:
            text = (path / name).read_text().strip()
            if name == "memory.current":
                result[name] = int(text)
            elif name == "io.stat":
                result[name] = text
            else:
                result[name] = {key: int(value) for key, value in (line.split() for line in text.splitlines())}
        except (OSError, ValueError) as exc:
            result[name] = None
            errors.append({"path": str(path / name), "error": str(exc)})
    return result, errors


def run(out, cgroup_root, interval=0.01, include_monitor=False):
    if not 0 < interval <= 1:
        raise ValueError("Sampling interval must be in (0, 1] seconds")
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in ["samples.jsonl", "monitor-manifest.json"]):
        raise FileExistsError("Refusing to replace existing light-monitor evidence")
    roles = ["server", "client"] + (["monitor"] if include_monitor else [])
    for role in roles:
        for name in ["cpu.stat", "memory.current", "memory.stat", "memory.events", "io.stat"]:
            if not (cgroup_root / role / name).is_file():
                raise FileNotFoundError("Required cgroup counter unavailable: " + str(cgroup_root / role / name))
    running = True
    def stop(*_):
        nonlocal running
        running = False
    for number in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(number, stop)
    manifest = {"schema_version": 1, "purpose": "supplemental_resource_only", "monitor": "cgroup_counters_only",
                "start_utc": utc(), "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(),
                "pid": os.getpid(), "cgroup_root": str(cgroup_root), "roles": roles, "interval_s": interval,
                "clockmeta": {"wall": "Unix CLOCK_REALTIME nanoseconds", "mono": "CLOCK_MONOTONIC nanoseconds",
                              "interval_s": interval, "interval_semantics": "requested schedule; use actual_interval_ms for observed spacing"},
                "sampling_ms_scope": "cgroup counter reads and sample assembly; excludes JSON serialization/write and sleep",
                "host_collection": False, "commands": {}}
    path = out / "monitor-manifest.json"
    atomic_json(path, manifest)
    period = int(interval * 1e9)
    deadline, previous = time.monotonic_ns(), None
    intervals, costs, loop_costs, errors = [], [], [], 0
    try:
        with (out / "samples.jsonl").open("x", buffering=1) as stream:
            while running:
                began = time.monotonic_ns()
                sample = {"wall_ns": time.time_ns(), "mono_ns": began, "groups": {}}
                row_errors = []
                for role in roles:
                    sample["groups"][role], failed = group_stats(cgroup_root / role)
                    row_errors.extend(failed)
                sample["sampling_ms"] = (time.monotonic_ns() - began) / 1e6
                sample["actual_interval_ms"] = (began - previous) / 1e6 if previous is not None else None
                if previous is not None:
                    intervals.append(sample["actual_interval_ms"])
                previous = began
                costs.append(sample["sampling_ms"])
                if row_errors:
                    sample["read_errors"] = row_errors
                    errors += len(row_errors)
                stream.write(json.dumps(sample, separators=(",", ":")) + "\n")
                loop_costs.append((time.monotonic_ns() - began) / 1e6)
                deadline += period
                current = time.monotonic_ns()
                if deadline <= current:
                    deadline += ((current - deadline) // period + 1) * period
                time.sleep(max(0, (deadline - time.monotonic_ns()) / 1e9))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        manifest.update(end_utc=utc(), end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns(),
                        sample_count=len(costs), read_error_count=errors, actual_interval_ms=distribution(intervals),
                        sampling_ms=distribution(costs), loop_work_ms=distribution(loop_costs),
                        loop_work_ms_scope="counter reads, sample assembly, JSON serialization and line write; excludes sleep and final fsync")
        atomic_json(path, manifest)
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    parser.add_argument("--interval", type=float, default=0.01)
    parser.add_argument("--include-monitor", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.out, args.cgroup_root, args.interval, args.include_monitor))


if __name__ == "__main__":
    main()
