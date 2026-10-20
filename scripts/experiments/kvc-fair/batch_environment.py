#!/usr/bin/env python3
"""Capture current hardware and interference baseline before preparation changes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.out.exists() or args.out.is_symlink():
        p.error("Preserve existing environment observations")
    commands = [["uname", "-a"], ["lscpu", "-J"], ["numactl", "--hardware"],
                ["lsblk", "-J", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT"],
                ["df", "-h", "/home", "/dev/shm"],
                ["ps", "-eo", "pid,comm,pcpu,pmem", "--sort=-pcpu"],
                ["systemctl", "is-active", "setroubleshootd.service"]]
    result = {"start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(), "commands": [],
              "proc": {name: Path("/proc", name).read_text() for name in ["meminfo", "loadavg", "stat", "diskstats"]},
              "cpu_online": Path("/sys/devices/system/cpu/online").read_text().strip()}
    for argv in commands:
        start, mono = time.time_ns(), time.monotonic_ns()
        try:
            proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30,
                                  env=dict(os.environ, LC_ALL="C"))
            row = {"returncode": proc.returncode, "output": proc.stdout}
        except (OSError, subprocess.TimeoutExpired) as exc:
            row = {"returncode": None, "error": str(exc)}
        result["commands"].append({"argv": argv, "start_wall_ns": start, "end_wall_ns": time.time_ns(),
                                   "duration_s": (time.monotonic_ns() - mono) / 1e9, **row})
    result.update(end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
    result["duration_s"] = (result["end_mono_ns"] - result["start_mono_ns"]) / 1e9
    with args.out.open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({"out": str(args.out), "duration_s": result["duration_s"]}))


if __name__ == "__main__":
    main()
