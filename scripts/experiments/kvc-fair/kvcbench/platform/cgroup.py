"""Observe and drain the explicitly selected experiment resource domain."""
import json
import os
import time

from kvcbench.core.timing import utc


def members(root, roles=None):
    paths = [root / "cgroup.procs"] if roles is None else []
    for role in roles or ["server", "client", "monitor"]:
        group = root / role
        required = group / "cgroup.procs"
        if not required.is_file():
            raise FileNotFoundError("Required cgroup membership unavailable: " + str(required))
        paths.extend([required, *group.rglob("cgroup.procs")])
    if roles is None:
        paths.extend(root.rglob("cgroup.procs"))
    return {str(path): path.read_text().split() for path in sorted(set(paths))}


def drain(root, roles=None, timeout=3):
    deadline = time.monotonic() + timeout
    while True:
        observed = members(root, roles)
        busy = {path: values for path, values in observed.items() if values}
        trees = [root / role for role in roles] if roles is not None else [root]
        populated = [str(path) for tree in trees for path in tree.rglob("cgroup.events")
                     if dict(line.split() for line in path.read_text().splitlines()).get("populated") == "1"]
        if not busy and not populated:
            return {"checked_utc": utc(), "members": observed, "populated": populated}
        if time.monotonic() >= deadline:
            raise RuntimeError("Cgroup drain failed: " + json.dumps({"members": busy, "populated": populated}))
        time.sleep(0.05)


def placement(path):
    def enter():
        (path / "cgroup.procs").write_text(str(os.getpid()))
    return enter
