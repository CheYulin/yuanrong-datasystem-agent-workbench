#!/usr/bin/env python3
"""Start, stop, or reset the two experiment-owned LMCache services."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "runtime-state.json"


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        return None if fields[2] == "Z" else fields[21]
    except FileNotFoundError:
        return None


def group_members(group):
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().split()
            if fields[2] != "Z" and int(fields[4]) == group:
                members.append((int(entry.name), fields[21]))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return members


def stop():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    owned_children = []
    for server in state["servers"]:
        pid = server["pid"]
        if server["start_time"] is not None and identity(pid) == server["start_time"]:
            owned_children.extend(group_members(pid))
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    for server in state["servers"]:
        pid = server["pid"]
        while server["start_time"] is not None and identity(pid) == server["start_time"] and time.monotonic() < deadline:
            time.sleep(0.1)
        if server["start_time"] is not None and identity(pid) == server["start_time"]:
            os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 2
    for pid, start_time in owned_children:
        while identity(pid) == start_time and time.monotonic() < deadline:
            time.sleep(0.05)
        if identity(pid) == start_time:
            os.kill(pid, signal.SIGKILL)
    STATE.unlink()


def start(gib):
    if STATE.exists():
        raise RuntimeError("Existing LMCache runtime state; run stop/reset first")
    (ROOT / ".evidence").mkdir(exist_ok=True)
    log_dir = Path(os.environ.get("LOG_DIR", str(ROOT / ".evidence" / f"runtime-{time.time_ns()}")))
    log_dir.mkdir(parents=True, exist_ok=True)
    state = {"l1_gib_each": gib, "servers": []}
    try:
        for index in range(2):
            env = dict(os.environ, SERVER_INDEX=str(index), L1_GIB=str(gib))
            log_path = log_dir / f"server-{index}.log"
            with log_path.open("w") as log:
                proc = subprocess.Popen(
                    [sys.executable, str(ROOT.parent / "cgroup.py"), "--group", "server", "exec", "--", "bash", str(ROOT / "start_server.sh")],
                    env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
            state["servers"].append({"pid": proc.pid, "start_time": identity(proc.pid), "index": index, "log": str(log_path)})
            STATE.write_text(json.dumps(state, indent=2))
        deadline = time.monotonic() + 60
        for server in state["servers"]:
            url = f"http://127.0.0.1:{18080 + server['index']}/healthcheck"
            while True:
                if server["start_time"] is None or identity(server["pid"]) != server["start_time"]:
                    raise RuntimeError(f"LMCache startup failed: {server['log']}")
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"LMCache healthcheck timeout: {server['log']}")
                    time.sleep(0.25)
        print(json.dumps(state, indent=2))
    except BaseException:
        stop()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "reset", "status"])
    parser.add_argument("--gib", type=float, default=4)
    args = parser.parse_args()
    if args.action in ("stop", "reset"):
        stop()
    if args.action in ("start", "reset"):
        start(args.gib)
    if args.action == "status":
        print(STATE.read_text() if STATE.exists() else "{}")


if __name__ == "__main__":
    main()
