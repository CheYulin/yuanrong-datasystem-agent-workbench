#!/usr/bin/env python3
"""Task-scoped cgroup v2 setup and pre-exec process placement."""
import argparse
import json
import os
from pathlib import Path

ROOT = Path("/sys/fs/cgroup/kvcfair20261020")
PROFILES = {"server": ("0,2,4,6,8,10,12,14", 12 << 30),
            "client": ("16,18,20,22,24,26,28,30", 12 << 30),
            "monitor": ("1,3", 1 << 30)}


def setup():
    ROOT.mkdir(exist_ok=True)
    (ROOT / "cgroup.subtree_control").write_text("+cpu +cpuset +memory +io")
    (ROOT / "memory.max").write_text(str(25 << 30))
    (ROOT / "memory.swap.max").write_text("0")
    for name, (cpus, memory) in PROFILES.items():
        group = ROOT / name
        group.mkdir(exist_ok=True)
        (group / "cpuset.mems").write_text("0" if name != "monitor" else "1")
        (group / "cpuset.cpus").write_text(cpus)
        (group / "memory.max").write_text(str(memory))
        (group / "memory.swap.max").write_text("0")
        (group / "cpu.max").write_text("800000 100000" if name != "monitor" else "200000 100000")
    return {name: {p: (ROOT / name / p).read_text().strip() for p in ["cpuset.cpus.effective", "cpuset.mems.effective", "cpu.max", "memory.max", "memory.swap.max"]} for name in PROFILES}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "exec"])
    parser.add_argument("--group", choices=PROFILES, default="client")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "setup":
        print(json.dumps(setup(), indent=2)); return
    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        parser.error("exec requires command")
    (ROOT / args.group / "cgroup.procs").write_text(str(os.getpid()))
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
