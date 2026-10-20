#!/usr/bin/env python3
"""Bounded /proc CPU audit; external processes are observed, never signaled."""
import argparse
import json
import os
from pathlib import Path
import signal
import time


def snapshot():
    values = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            text = (proc / "stat").read_text()
            end = text.rfind(")")
            fields = text[end + 2:].split()
            group = (proc / "cgroup").read_text().strip()
            values[proc.name] = {"ticks": int(fields[11]) + int(fields[12]), "start_ticks": fields[19],
                                 "comm": text[text.index("(") + 1:end], "cgroup": group,
                                 "last_cpu": int(fields[36]),
                                 "affinity": sorted(os.sched_getaffinity(int(proc.name)))}
        except (OSError, ValueError, IndexError, ProcessLookupError):
            pass
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    previous, before = snapshot(), time.monotonic_ns()
    ticks = os.sysconf("SC_CLK_TCK")
    cpus = set(range(0, 32, 2))
    with args.out.open("x", buffering=1) as out:
        while running:
            time.sleep(1)
            current, now = snapshot(), time.monotonic_ns()
            seconds = (now - before) / 1e9
            rows = []
            for pid, row in current.items():
                old = previous.get(pid)
                if old is None or old["start_ticks"] != row["start_ticks"]:
                    continue
                cores = (row["ticks"] - old["ticks"]) / ticks / seconds
                if cores > 0 and "kvcfair20261020" not in row["cgroup"]:
                    rows.append({"pid": int(pid), **row, "cpu_cores": cores,
                                 "can_run_on_experiment_cpus": bool(cpus.intersection(row["affinity"]))})
            rows.sort(key=lambda row: row["cpu_cores"], reverse=True)
            out.write(json.dumps({"wall_ns": time.time_ns(), "mono_ns": now, "span_s": seconds,
                                  "external_cpu_cores": sum(row["cpu_cores"] for row in rows),
                                  "potential_shared_cpu_cores": sum(row["cpu_cores"] for row in rows if row["can_run_on_experiment_cpus"]),
                                  "top_processes": rows[:20],
                                  "scope": "Process CPU deltas, not per-thread exact CPU placement; short-lived processes may be missed"}) + "\n")
            previous, before = current, now


if __name__ == "__main__":
    main()
