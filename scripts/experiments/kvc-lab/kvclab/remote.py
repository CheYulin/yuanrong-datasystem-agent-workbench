"""Fixed SSH JSON protocol; experiment requests never choose commands or paths."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import stat
import traceback

from .remote_support import atomic, job_id, member, read, ticks, validate_request


def artifact_names(job):
    names = {"events.jsonl", "complete.json", "request.json", "results.json", "commands.json"}
    path = job / "events.jsonl"
    if path.is_file() and not path.is_symlink():
        with path.open() as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(value, dict):
                    continue
                if value.get("kind") == "step":
                    name = value.get("artifact")
                    if isinstance(name, str):
                        names.add(name)
                if value.get("kind") == "result":
                    body = value.get("result")
                    name = body.get("raw_artifact") if isinstance(body, dict) else None
                    if isinstance(name, str):
                        names.add(name)
    return names


def artifact_path(settings, selected, name, *, allowed=None):
    job_id(selected)
    if not isinstance(name, str):
        raise ValueError("An artifact name is required")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or str(relative) != name or name in {"", "."}:
        raise ValueError("Artifact name must be a canonical relative path")
    job = Path(settings["state_root"]) / "jobs" / selected
    if any(parent.is_symlink() for parent in [job, *job.parents]):
        raise ValueError("Symlink job paths are forbidden")
    if name not in (artifact_names(job) if allowed is None else allowed):
        raise ValueError("Artifact is outside the event-bound allowlist")
    path = job / relative
    if any(parent.is_symlink() for parent in [path, *path.parents]) or not path.is_file():
        raise ValueError("Artifact must be an existing non-symlink regular file")
    return path


def artifact(settings, selected, name):
    path = artifact_path(settings, selected, name)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16 << 20:
            raise ValueError("Artifact must be a regular file no larger than 16MiB")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read((16 << 20) + 1)
        if len(data) > 16 << 20:
            raise ValueError("Artifact exceeds 16MiB")
    finally:
        os.close(descriptor)
    return {"kind": "artifact", "name": name, "encoding": "base64", "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data), "data": base64.b64encode(data).decode("ascii")}


def artifact_index(settings, selected):
    job_id(selected)
    job = Path(settings["state_root"]) / "jobs" / selected
    result = []
    names = artifact_names(job)
    for name in sorted(names):
        try:
            path = artifact_path(settings, selected, name, allowed=names)
        except ValueError:
            continue
        size = path.stat().st_size
        result.append({"id": hashlib.sha256(name.encode()).hexdigest()[:24], "name": name, "size": size,
                       "downloadable": size <= 16 << 20})
    return result


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def settings_from(path):
    settings = read(path)
    required = {"native_root", "build_catalog", "fair_root", "state_root"}
    if not isinstance(settings, dict) or not required <= set(settings):
        raise ValueError("Private runner configuration is incomplete")
    for name in required | ({"cgroup_root"} & set(settings)):
        value = settings[name]
        p = Path(value)
        if not p.is_absolute() or str(p) != value or ".." in p.parts or any(parent.is_symlink() for parent in [p, *p.parents]):
            raise ValueError("Private paths must be canonical, absolute and non-symlink: " + name)
    cgroup = Path(settings.get("cgroup_root", "/sys/fs/cgroup/kvc-lab"))
    if cgroup.parent != Path("/sys/fs/cgroup") or not cgroup.name.startswith("kvc-lab"):
        raise ValueError("A private kvc-lab cgroup prefix is required")
    return settings


def state(settings, requested=None):
    root = Path(settings["state_root"])
    active = read(root / "active-execution.json", {})
    selected = requested or active.get("job_id")
    if selected is not None:
        job_id(selected)
    same = selected == active.get("job_id")
    running = bool(same and not active.get("terminal") and active.get("pid") and ticks(active["pid"]) == active.get("start_ticks"))
    job = root / "jobs" / selected if selected else None
    complete = read(job / "complete.json") if job else None
    if complete is not None:
        running = False
    deployment = read(root / "deployment.json", {})
    if deployment:
        alive = [row["name"] for row in deployment.get("processes", [])
                 if ticks(row["pid"]) == row["start_ticks"] and member(row["pid"], row["group"])]
        expected = [row["name"] for row in deployment.get("processes", [])
                    if row["name"] in {"coordinator", "master"} or row["name"].startswith(("worker-", "peer-"))]
        deployment["observed_alive_processes"] = alive
        deployment["active"] = bool(deployment.get("active") and expected and all(name in alive for name in expected))
    recent = []
    if job and (job / "events.jsonl").exists():
        # Never load an unbounded event log into the status request.
        with (job / "events.jsonl").open("rb") as stream:
            stream.seek(0, 2)
            length = stream.tell()
            stream.seek(max(0, length - 65536))
            lines = stream.read().splitlines()
        for line in lines[-30:]:
            try:
                recent.append(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                pass
    return {"kind": "state", "running": running, "job_id": selected,
            "pid": active.get("pid") if same else None, "start_ticks": active.get("start_ticks") if same else None,
            "complete": complete, "deployment": deployment, "recent_events": recent,
            "artifacts": artifact_index(settings, selected) if selected else [],
            "interrupted": bool(job and job.exists() and not running and complete is None)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--action", required=True, choices=["execute", "state", "stop", "artifact"])
    args = parser.parse_args(argv)
    try:
        settings = settings_from(args.config)
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072:
            raise ValueError("Request exceeds 128KiB")
        request = json.loads(raw or b"{}")
        if not isinstance(request, dict):
            raise ValueError("JSON object required")
        if args.action == "artifact":
            if set(request) != {"job_id", "name"}:
                raise ValueError("Only job_id and the event-bound artifact name are accepted")
            emit(artifact(settings, request["job_id"], request["name"]))
            return 0
        if args.action == "state":
            emit(state(settings, request.get("job_id")))
            return 0
        root = Path(settings["state_root"])
        root.mkdir(parents=True, exist_ok=True)
        if args.action == "stop":
            selected = job_id(request.get("job_id"))
            current = state(settings, selected)
            pending = root / "jobs" / selected
            requested = pending.is_dir() and current["complete"] is None
            if requested:
                atomic(root / "jobs" / selected / "cancel.json", {"job_id": selected, "requested_by": "fixed-ssh-control"})
            emit({"kind": "complete", "status": "succeeded", "job_id": selected,
                  "cancel_requested": requested, "running": current["running"],
                  "message": "The owning execution observes cancellation before its next command or within 200ms while waiting"})
            return 0
        validate_request(request, settings)
        selected = request["job_id"]
        with (root / "execution.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another owned experiment is currently running")
            job = root / "jobs" / selected
            if job.exists():
                complete = read(job / "complete.json")
                if complete is not None:
                    emit(complete)
                    return 0
                raise RuntimeError("Job ID already exists without completion; writes will not be replayed")
            from .remote_runtime import Engine
            engine = Engine(settings, request, emit)
            active = {"job_id": selected, "pid": os.getpid(), "start_ticks": ticks(os.getpid())}
            atomic(root / "active-execution.json", active)
            try:
                result = engine.execute()
                emit(result)
            finally:
                if read(root / "active-execution.json") == active:
                    atomic(root / "active-execution.json", {**active, "terminal": True})
            return 0 if result["status"] != "failed" else 1
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        emit({"kind": "complete", "status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
