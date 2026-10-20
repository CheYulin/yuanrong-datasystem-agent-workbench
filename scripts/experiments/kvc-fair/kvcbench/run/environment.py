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
NOISE_UNIT = "mihumo.service"
SETTINGS = ("cpuset.cpus", "cpuset.mems", "cpu.max", "memory.max", "memory.swap.max")
TOOLS = ("iostat", "mpstat", "pidstat", "dstat", "strace", "numactl", "systemctl")
MASK = Path("/run/systemd/system") / UNIT


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


def unit_state(unit=UNIT):
    show = subprocess.run(["systemctl", "show", unit, "--property=LoadState,ActiveState,SubState,ExecMainStatus,Restart,UnitFileState"],
                          capture_output=True, text=True, timeout=30)
    properties = dict(line.split("=", 1) for line in show.stdout.splitlines() if "=" in line)
    result = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=30)
    if show.returncode != 0 or result.returncode not in (0, 3) and properties.get("LoadState") != "not-found":
        raise ValueError("Cannot establish interference service state: " + result.stderr)
    return {"active": result.returncode == 0, "output": result.stdout.strip(), "returncode": result.returncode,
            "properties": properties}


def failed_restart_loop(state):
    properties = state.get("properties", {})
    return (properties.get("LoadState") == "loaded" and properties.get("Restart") == "always"
            and properties.get("ExecMainStatus") == "203" and properties.get("ActiveState") in {"activating", "failed"})


def mask_state():
    present = MASK.exists() or MASK.is_symlink()
    link = os.readlink(MASK) if MASK.is_symlink() else None
    return {"exists": present, "is_mask": present and link == "/dev/null", "link": link}


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
    before = {"drained": drain(root), "settings": settings(root), "unit": unit_state(), "noise_unit": unit_state(NOISE_UNIT),
              "runtime_mask": mask_state(),
              "tools": {name: shutil.which(name) for name in TOOLS}, "uid": os.geteuid()}
    atomic_json(out / "before.json", before, exclusive=True)
    if before["uid"] != 0 or not all(before["tools"].values()):
        raise ValueError("Root and all required observation/qualification tools are required")
    already_masked = (before["runtime_mask"]["is_mask"] or
                      before["unit"].get("properties", {}).get("UnitFileState") in {"masked", "masked-runtime"})
    if (before["runtime_mask"]["exists"] and not before["runtime_mask"]["is_mask"]
            or already_masked and before["unit"]["active"]):
        raise ValueError("Existing runtime override or already-masked live service cannot be safely changed")
    expected = expected_settings()
    changes = [{"path": path, "before": before["settings"][path], "after": value}
               for path, value in expected.items() if before["settings"][path] != value]
    journal = {"cgroup": str(root), "unit": UNIT, "unit_was_active": before["unit"]["active"],
               "baseline": identity(out / "before.json"),
               "noise_unit": NOISE_UNIT, "noise_was_restart_loop": failed_restart_loop(before["noise_unit"]),
               "runtime_mask_planned": not already_masked,
               "settings_changes": changes, "scope": "Explicit cgroup changes, owned runtime diagnostic mask and confirmed failed-restart unit quiescence"}
    atomic_json(out / "intent.json", journal, exclusive=True)
    error = None
    try:
        if journal["noise_was_restart_loop"]:
            command = execute(["systemctl", "stop", NOISE_UNIT], cwd=out, log=out / "stop-noise.log",
                              record=out / "stop-noise-command.json", timeout=30)
            if command["returncode"] != 0:
                raise ValueError("Confirmed failing restart loop could not be stopped")
        for change in changes:
            (root / change["path"]).write_text(change["after"] + "\n")
        if journal["runtime_mask_planned"] or journal["unit_was_active"]:
            argv = ["systemctl", "mask", "--runtime", "--now", UNIT] if journal["runtime_mask_planned"] else ["systemctl", "stop", UNIT]
            command = execute(argv, cwd=out, log=out / "stop.log",
                              record=out / "stop-command.json", timeout=30)
            if command["returncode"] != 0:
                raise ValueError("Interference unit stop failed")
        after = {"settings": settings(root), "unit": unit_state(), "noise_unit": unit_state(NOISE_UNIT),
                 "runtime_mask": mask_state(), "drained": drain(root)}
        noise_unquiet = journal["noise_was_restart_loop"] and (after["noise_unit"]["active"] or failed_restart_loop(after["noise_unit"]))
        blocked = after["runtime_mask"]["is_mask"] or after["unit"].get("properties", {}).get("UnitFileState") == "masked"
        if after["settings"] != expected or after["unit"]["active"] or noise_unquiet or not blocked:
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
    baseline_path = regular(preparation, "before.json")
    if journal.get("baseline") is not None and identity(baseline_path) != journal["baseline"]:
        raise ValueError("Preparation baseline changed after the intent was recorded")
    if (journal["cgroup"] != str(root) or journal["unit"] != UNIT
            or journal.get("noise_unit", NOISE_UNIT) != NOISE_UNIT):
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
    if journal.get("runtime_mask_planned"):
        current_mask = mask_state()
        if current_mask["exists"] and not current_mask["is_mask"]:
            raise ValueError("Runtime mask changed outside this attempt; refusing removal")
        if current_mask["is_mask"]:
            command = execute(["systemctl", "unmask", "--runtime", UNIT], cwd=out, log=out / "unmask.log",
                              record=out / "unmask-command.json", timeout=30)
            if command["returncode"] != 0 or mask_state()["exists"]:
                raise ValueError("Owned runtime interference mask could not be removed")
    if journal.get("noise_was_restart_loop"):
        command = execute(["systemctl", "start", "--no-block", NOISE_UNIT], cwd=out, log=out / "start-noise.log",
                          record=out / "start-noise-command.json", timeout=30)
        if command["returncode"] != 0:
            raise ValueError("Original failed restart-loop activation could not be restored")
    if journal["unit_was_active"]:
        command = execute(["systemctl", "start", UNIT], cwd=out, log=out / "start.log",
                          record=out / "start-command.json", timeout=30)
        if command["returncode"] != 0:
            raise ValueError("Interference unit restore failed")
    after = {"settings": settings(root), "unit": unit_state(), "noise_unit": unit_state(NOISE_UNIT),
             "runtime_mask": mask_state(), "drained": drain(root)}
    baseline = json.loads(regular(preparation, "before.json").read_text())
    if (after["settings"] != baseline["settings"]
            or "runtime_mask" in baseline and after["runtime_mask"] != baseline["runtime_mask"]):
        raise ValueError("Restored cgroup settings differ from the original snapshot")
    atomic_json(out / "result.json", {"accepted": True, "journal": identity(journal_path),
                "after": after, **finished(begin)}, exclusive=True)
    return {"accepted": True}
