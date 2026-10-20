#!/usr/bin/env python3
"""Low-frequency cgroup/process telemetry and independent host tools."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def pairs(value):
    if value is None:
        return None
    result = {}
    for line in value.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                result[parts[0]] = int(parts[1])
            except ValueError:
                result[parts[0]] = parts[1]
    return result


def group_stats(path):
    numeric = ["memory.current", "memory.peak", "memory.swap.current", "pids.current"]
    result = {name: int(value) if (value := read(path / name)) is not None and value.isdigit() else value for name in numeric}
    for name in ["cpu.stat", "memory.stat", "memory.events", "memory.events.local"]:
        result[name] = pairs(read(path / name))
    for name in ["cpu.pressure", "memory.pressure", "io.pressure", "io.stat", "memory.numa_stat", "cgroup.procs"]:
        result[name] = read(path / name)
    result["processes"] = []
    for pid in (result["cgroup.procs"] or "").split():
        stat = read(Path("/proc") / pid / "stat")
        if stat is None:
            continue
        end = stat.rfind(")")
        fields = stat[end + 2:].split()
        result["processes"].append({"pid": int(pid), "comm": stat[stat.find("(") + 1:end],
                                   "utime_ticks": int(fields[11]), "stime_ticks": int(fields[12]),
                                   "start_ticks": int(fields[19]), "rss_pages": int(fields[21]),
                                   "io": pairs(read(Path("/proc") / pid / "io")),
                                   "status": pairs(read(Path("/proc") / pid / "status"))})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    parser.add_argument("--interval", type=float, default=1)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    commands = {
        "iostat": ["iostat", "-x", "-y", "-t", "1"],
        "mpstat": ["mpstat", "-P", "ALL", "1"],
        "pidstat": ["pidstat", "-u", "-r", "-d", "-w", "1"],
        "dstat": ["dstat", "--time", "--cpu", "--mem", "--disk", "--net", "--nocolor", "--output", str(args.out / "dstat.csv"), "1"],
        "perf": ["perf", "stat", "-a", "-C", "0,2,4,6,8,10,12,14,16,18,20,22,24,26,28,30", "-I", "1000", "-e", "cycles,instructions,context-switches,cpu-migrations,page-faults"]}
    children, outputs = {}, []
    manifest = {"start_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "commands": {},
                "clk_tck": os.sysconf("SC_CLK_TCK"), "page_size": os.sysconf("SC_PAGE_SIZE"),
                "pressure_support": Path("/proc/pressure").exists(), "interval_s": args.interval}
    for name, command in commands.items():
        manifest["commands"][name] = {"argv": command, "available": shutil.which(command[0]) is not None}
        if not manifest["commands"][name]["available"]:
            continue
        out = (args.out / (name + ".log")).open("w")
        outputs.append(out)
        children[name] = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, env=dict(os.environ, LC_ALL="C", TZ="UTC"))
        manifest["commands"][name].update(pid=children[name].pid, startup_returncode=children[name].poll())
    (args.out / "monitor-manifest.json").write_text(json.dumps(manifest, indent=2))
    with (args.out / "samples.jsonl").open("a", buffering=1) as out:
        while running:
            started = time.monotonic()
            sample = {"wall_ns": time.time_ns(), "mono_ns": time.monotonic_ns(),
                      "groups": {name: group_stats(args.cgroup_root / name) for name in ["server", "client", "monitor"]},
                      "parent": group_stats(args.cgroup_root),
                      "host": {"meminfo": read("/proc/meminfo"), "stat": read("/proc/stat"),
                               "diskstats": read("/proc/diskstats"), "netdev": read("/proc/net/dev"),
                               "loadavg": read("/proc/loadavg"), "vmstat": read("/proc/vmstat")}}
            sample["tool_returncodes"] = {name: child.poll() for name, child in children.items()}
            sample["sampling_ms"] = (time.monotonic() - started) * 1000
            out.write(json.dumps(sample, separators=(",", ":")) + "\n")
            time.sleep(max(0.01, args.interval - (time.monotonic() - started)))
    for name, child in children.items():
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(5)
            except subprocess.TimeoutExpired:
                child.terminate(); child.wait(5)
        manifest["commands"][name]["returncode"] = child.returncode
    for out in outputs:
        out.close()
    manifest["end_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (args.out / "monitor-manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
