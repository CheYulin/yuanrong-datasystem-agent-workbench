#!/usr/bin/env python3
import argparse
import ctypes
import json
import os
import platform
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile


def pidfd_open(pid):
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid)
    libc = ctypes.CDLL(None, use_errno=True)
    if platform.machine() not in ("x86_64", "aarch64"):
        raise RuntimeError("pidfd fallback only defines Linux x86_64/aarch64 syscall numbers")
    libc.syscall.restype = ctypes.c_long
    fd = libc.syscall(ctypes.c_long(434), ctypes.c_int(pid), ctypes.c_uint(0))
    if fd < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    return fd


def pidfd_term(fd):
    if hasattr(signal, "pidfd_send_signal"):
        return signal.pidfd_send_signal(fd, signal.SIGTERM)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    if libc.syscall(ctypes.c_long(424), ctypes.c_int(fd), ctypes.c_int(signal.SIGTERM), ctypes.c_void_p(None), ctypes.c_uint(0)) < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def proc_stat(pid):
    parts = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    return parts[0], int(parts[19])


def record(path, pid):
    records = json.loads(path.read_text()) if path.exists() else []
    records.append({"pid": pid, "start_ticks": proc_stat(pid)[1]})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records))
    tmp.replace(path)


def stop(path, task_root):
    for identity in reversed(json.loads(path.read_text())):
        pid = identity["pid"]
        try:
            fd = pidfd_open(pid)
        except ProcessLookupError:
            continue
        try:
            try:
                state, ticks = proc_stat(pid)
            except FileNotFoundError:
                continue
            if ticks != identity["start_ticks"]:
                raise RuntimeError(f"PID identity changed; refusing to signal {pid}")
            if state == "Z":
                continue
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
            if os.fsencode(task_root) not in command:
                raise RuntimeError(f"PID command belongs to another task; refusing to signal {pid}")
            pidfd_term(fd)
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            if not poller.poll(30000):
                raise RuntimeError(f"Mooncake PID {pid} did not exit within30s; no forced termination performed")
        finally:
            os.close(fd)


def selftest(task_root):
    with tempfile.TemporaryDirectory(prefix="pid-guard-", dir=task_root) as directory:
        path = Path(directory) / "identities.json"
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", task_root])
        try:
            record(path, child.pid)
            identities = json.loads(path.read_text())
            identities[0]["start_ticks"] += 1
            path.write_text(json.dumps(identities))
            try:
                stop(path, task_root)
            except RuntimeError as exc:
                if "PID identity changed" not in str(exc):
                    raise
            else:
                raise AssertionError("Stale start_ticks did not prevent signalling")
            assert child.poll() is None, "Stale PID record killed child"
            path.unlink()
            record(path, child.pid)
            stop(path, task_root)
            child.wait(5)
            print(json.dumps({"stale_identity_rejected": True, "stale_identity_child_alive": True,
                              "fresh_identity_stopped_via_pidfd": True}))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(5)


parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["record", "stop", "selftest"])
parser.add_argument("--state", type=Path)
parser.add_argument("--pid", type=int)
parser.add_argument("--task-root", required=True)
args = parser.parse_args()
if args.action == "record":
    record(args.state, args.pid)
elif args.action == "stop":
    stop(args.state, args.task_root)
else:
    selftest(args.task_root)
