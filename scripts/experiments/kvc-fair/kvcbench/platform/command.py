"""Timed argv execution for owned process groups; no shell or implicit retry."""
import os
from pathlib import Path
import signal
import subprocess

from kvcbench.core.timing import started, finished
from kvcbench.platform.cgroup import placement
from kvcbench.platform.files import atomic_json


def stop_owned(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=5)
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def execute(argv, *, cwd, log, record, env=None, timeout=None, group=None):
    log, record = Path(log), Path(record)
    if log.exists() or record.exists():
        raise FileExistsError("Command logs/records must be fresh")
    log.parent.mkdir(parents=True, exist_ok=True)
    begin = started()
    result = {"argv": [str(v) for v in argv], "cwd": str(cwd), "returncode": None,
              "error": None, "log": str(log), "cgroup": str(group) if group else None}
    process = None
    try:
        with log.open("xb") as stream:
            process = subprocess.Popen(result["argv"], cwd=cwd, env=env, stdout=stream,
                stderr=subprocess.STDOUT, start_new_session=True,
                preexec_fn=placement(Path(group)) if group else None)
            result["pid"] = process.pid
            result["returncode"] = process.wait(timeout=timeout)
    except BaseException as exc:
        if process is not None:
            stop_owned(process)
            result["returncode"] = process.returncode
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if not isinstance(exc, Exception):
            raise
    finally:
        result.update(finished(begin))
        atomic_json(record, result, exclusive=True)
    return result
