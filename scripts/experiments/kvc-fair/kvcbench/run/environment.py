"""Journal explicit runtime isolation changes and restore only those changes."""
from pathlib import Path
import fcntl
import json
import os
import shutil
import subprocess

from kvcbench.core.timing import started, finished
from kvcbench.platform.cgroup import drain
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, identity, regular
from kvcbench.platform.locks import open_path
from kvcbench.run import isolation

UNIT = "setroubleshootd.service"
SETTINGS = ("cpuset.cpus", "cpuset.mems", "cpu.max", "memory.max", "memory.swap.max")
TOOLS = ("iostat", "mpstat", "pidstat", "dstat", "strace", "numactl", "systemctl")


def settings(root):
    files = [root / name for name in ("memory.max", "memory.swap.max")]
    files += [root / role / name for role in isolation.PROFILES for name in SETTINGS]
    return {str(path.relative_to(root)): path.read_text().strip() for path in files}


def expected_settings():
    expected = {"memory.max": str(25 << 30), "memory.swap.max": "0"}
    for role, (cpus, memory) in isolation.PROFILES.items():
        expected.update({role + "/" + key: value for key, value in {
            "cpuset.cpus": cpus, "cpuset.mems": "0" if role != "monitor" else "1",
            "cpu.max": "800000 100000" if role != "monitor" else "200000 100000",
            "memory.max": str(memory), "memory.swap.max": "0"}.items()})
    return expected


def unit_state():
    result = subprocess.run(["systemctl", "is-active", UNIT], capture_output=True, text=True, timeout=30)
    if result.returncode not in (0, 3):
        raise ValueError("Cannot establish interference service state: " + result.stderr)
    return {"active": result.returncode == 0, "output": result.stdout.strip(), "returncode": result.returncode}


def prepare(root, out, *, resource_lock):
    begin = started()
    with open_path(Path(resource_lock)) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Path(out).mkdir(parents=True, exist_ok=False)
        try:
            return prepare_locked(root, out)
        except BaseException as exc:
            record_failure(out, begin, exc)
            raise


def record_failure(out, begin, error):
    out = Path(out)
    if out.is_dir() and not (out / "result.json").exists():
        atomic_json(out / "result.json", {"accepted": False, "error": str(error), **finished(begin)}, exclusive=True)


def prepare_locked(root, out):
    begin = started()
    out = Path(out)
    before = {"drained": drain(root), "settings": settings(root), "unit": unit_state(),
              "tools": {name: shutil.which(name) for name in TOOLS}, "uid": os.geteuid()}
    atomic_json(out / "before.json", before, exclusive=True)
    if before["uid"] != 0 or not all(before["tools"].values()):
        raise ValueError("Root and all required observation/qualification tools are required")
    expected = expected_settings()
    changes = [{"path": path, "before": before["settings"][path], "after": value}
               for path, value in expected.items() if before["settings"][path] != value]
    journal = {"cgroup": str(root), "unit": UNIT, "unit_was_active": before["unit"]["active"],
               "settings_changes": changes, "scope": "No process killing, file deletion or historical tmpfs cleanup"}
    atomic_json(out / "intent.json", journal, exclusive=True)
    error = None
    try:
        for change in changes:
            (root / change["path"]).write_text(change["after"] + "\n")
        if journal["unit_was_active"]:
            command = execute(["systemctl", "stop", UNIT], cwd=out, log=out / "stop.log",
                              record=out / "stop-command.json", timeout=30)
            if command["returncode"] != 0:
                raise ValueError("Interference unit stop failed")
        after = {"settings": settings(root), "unit": unit_state(), "drained": drain(root)}
        if after["settings"] != expected or after["unit"]["active"]:
            raise ValueError("Actual runtime isolation differs from the required profile")
        atomic_json(out / "after.json", after, exclusive=True)
    except BaseException as exc:
        error = str(exc)
        raise
    finally:
        atomic_json(out / "result.json", {"accepted": error is None, "error": error,
                    "intent": identity(out / "intent.json"), **finished(begin)}, exclusive=True)
    return {"accepted": True, "changed_settings": len(changes), "temporarily_stopped_units":
            [UNIT] if journal["unit_was_active"] else [], "duration_s": (json.loads((out / "result.json").read_text()))["duration_s"]}


def restore(root, preparation, out, *, resource_lock):
    begin = started()
    with open_path(Path(resource_lock)) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Path(out).mkdir(parents=True, exist_ok=False)
        try:
            return restore_locked(root, preparation, out)
        except BaseException as exc:
            record_failure(out, begin, exc)
            raise


def restore_locked(root, preparation, out):
    begin = started()
    preparation, out = Path(preparation), Path(out)
    journal_path = regular(preparation, "intent.json")
    journal = json.loads(journal_path.read_text())
    if journal["cgroup"] != str(root) or journal["unit"] != UNIT:
        raise ValueError("Restoration journal does not match this resource domain")
    initial = drain(root)
    atomic_json(out / "before.json", {"drained": initial, "settings": settings(root), "unit": unit_state()}, exclusive=True)
    for change in reversed(journal["settings_changes"]):
        if change["path"] not in expected_settings():
            raise ValueError("Unrecognized restoration setting")
        current = (root / change["path"]).read_text().strip()
        if current not in (change["after"], change["before"]):
            raise ValueError("Setting changed outside this attempt; restoration refused")
        (root / change["path"]).write_text(change["before"] + "\n")
    if journal["unit_was_active"]:
        command = execute(["systemctl", "start", UNIT], cwd=out, log=out / "start.log",
                          record=out / "start-command.json", timeout=30)
        if command["returncode"] != 0:
            raise ValueError("Interference unit restore failed")
    after = {"settings": settings(root), "unit": unit_state(), "drained": drain(root)}
    baseline = json.loads(regular(preparation, "before.json").read_text())
    if after["settings"] != baseline["settings"]:
        raise ValueError("Restored cgroup settings differ from the original snapshot")
    atomic_json(out / "result.json", {"accepted": True, "journal": identity(journal_path),
                "after": after, **finished(begin)}, exclusive=True)
    return {"accepted": True}
