#!/usr/bin/env python3
"""Observe final environment; --restore starts only the recorded troubleshooting unit."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

UNIT = "setroubleshootd.service"


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def command(argv):
    start_wall, start_mono = time.time_ns(), time.monotonic_ns()
    result = {"argv": argv, "started_utc": utc(), "start_wall_ns": start_wall, "start_mono_ns": start_mono}
    try:
        proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                              env=dict(os.environ, LC_ALL="C", TZ="UTC", GIT_OPTIONAL_LOCKS="0"))
        result.update(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.update(returncode=None, error=str(exc))
    result.update(ended_utc=utc(), end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
    result["duration_s"] = (result["end_mono_ns"] - start_mono) / 1e9
    return result


def empty_cgroups(root):
    required = [root] + [root / name for name in ["server", "client", "monitor"]]
    for group in required:
        for name in ["cgroup.procs", "cgroup.events"]:
            if not (group / name).is_file():
                raise RuntimeError("Cannot prove cgroups empty; missing " + str(group / name))
    tasks = {str(path): path.read_text().split() for path in sorted(root.rglob("cgroup.procs"))}
    populations = {str(path): dict(line.split() for line in path.read_text().splitlines() if line.strip())
                   for path in sorted(root.rglob("cgroup.events"))}
    if any(tasks.values()) or any(values.get("populated") != "0" for values in populations.values()):
        raise RuntimeError("Refusing environment restoration while an experiment cgroup has tasks or is populated: " + json.dumps({"tasks": tasks, "events": populations}))
    return {"observed_utc": utc(), "tasks": tasks, "events": populations, "empty": True}


def ended_suite(path):
    source = path / "summary.json"
    content = source.read_bytes()
    raw = json.loads(content)
    if not raw.get("ended"):
        raise RuntimeError("Suite has not recorded an ended boundary: " + str(source))
    return {"path": str(source), "sha256": hashlib.sha256(content).hexdigest(), "started": raw.get("started"),
            "ended": raw["ended"], "failed": raw.get("failed"), "fatal": raw.get("fatal"),
            "valid": raw.get("valid"), "completed_count": len(raw.get("completed", []))}


def service_state():
    show = command(["systemctl", "show", UNIT, "--no-pager", "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus"])
    active = command(["systemctl", "is-active", UNIT])
    properties = dict(line.split("=", 1) for line in show.get("stdout", "").splitlines() if "=" in line)
    return {"observed_utc": utc(), "show": show, "is_active": active, "properties": properties,
            "active_at_observation": active.get("returncode") == 0,
            "scope": "Point-in-time systemctl output; successful start does not imply the unit remains active indefinitely."}


def shm_entries(root):
    entries = []
    for path in sorted(root.iterdir()):
        try:
            info = path.lstat()
            kind = "symlink" if stat.S_ISLNK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "other"
            row = {"name": path.name, "type": kind, "stat_bytes": info.st_size, "mtime_ns": info.st_mtime_ns}
            if kind == "symlink":
                row["target"] = os.readlink(path)
            entries.append(row)
        except OSError as exc:
            entries.append({"name": path.name, "error": str(exc)})
    return {"observed_utc": utc(), "path": str(root), "entries": entries,
            "scope": "Immediate directory entries and lstat sizes only; no recursive du, content reads, removal or recreation."}


def archive_state(cleanup):
    original = cleanup.get("archive")
    if not original:
        return {"recorded_archive": None, "content_hash_recomputed": False}
    path = Path(original["path"])
    result = {"recorded_archive": original, "path": str(path), "exists": path.exists(), "content_hash_recomputed": False,
              "scope": "Current existence and stat bytes; the historical recorded SHA is retained without rereading archive content."}
    if result["exists"]:
        info = path.stat()
        result.update(bytes=info.st_size, is_file=stat.S_ISREG(info.st_mode), matches_recorded_bytes=info.st_size == original.get("bytes"))
    else:
        result.update(bytes=None, is_file=False, matches_recorded_bytes=False)
    return result


def source_states(root):
    sources = {}
    for name, path in [("datasystem", root / "datasystem"), ("mooncake", root / "mooncake/source"), ("lmcache", root / "lmcache/source")]:
        head = command(["git", "-C", str(path), "rev-parse", "HEAD"])
        status = command(["git", "-C", str(path), "status", "--porcelain"])
        sources[name] = {"path": str(path), "head": head, "status": status,
                         "sha": head.get("stdout", "").strip() if head.get("returncode") == 0 else None,
                         "clean": not status.get("stdout") if status.get("returncode") == 0 else None}
    return sources


def save_new(path, record):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, path)
    temporary.unlink()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def run(args):
    root = args.root.resolve()
    suite = args.suite.resolve() if args.suite else root / "formal-001"
    profile = args.profile_suite.resolve() if args.profile_suite else root / "put-resource-profile-001"
    out = args.out.resolve() if args.out else suite / "environment-restored.json"
    lock = os.open(root / "suite.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        initial = empty_cgroups(args.cgroup_root)
        suites = {"main": ended_suite(suite), "profile": ended_suite(profile)}
        cleanup_path = args.environment_root / "environment-cleanup.json"
        cleanup_bytes = cleanup_path.read_bytes()
        cleanup = json.loads(cleanup_bytes)
        if args.restore and (out.exists() or out.is_symlink()):
            if out.is_symlink():
                raise RuntimeError("Refusing symlinked restoration evidence")
            prior = json.loads(out.read_text())
            expected = {"root": str(root), "suite": str(suite), "profile_suite": str(profile), "unit": UNIT, "restore_requested": True}
            if any(prior.get(key) != value for key, value in expected.items()) or prior.get("cleanup_source", {}).get("sha256") != hashlib.sha256(cleanup_bytes).hexdigest():
                raise RuntimeError("Existing restoration evidence belongs to different inputs; it was not overwritten")
            print(json.dumps({"status": "already_recorded", "path": str(out), "recorded_status": prior.get("status"),
                              "recorded_after": prior.get("after"), "recorded_errors": prior.get("errors"),
                              "service_not_started_again": True}, ensure_ascii=False, indent=2))
            return 1 if prior.get("errors") else 0
        if not out.parent.is_dir():
            raise FileNotFoundError("Output parent must already exist: " + str(out.parent))
        record = {"schema_version": 1, "purpose": "final_environment_restoration", "restore_requested": args.restore,
                  "started_utc": utc(), "start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns(),
                  "root": str(root), "suite": str(suite), "profile_suite": str(profile), "output": str(out),
                  "cleanup_source": {"path": str(cleanup_path), "sha256": hashlib.sha256(cleanup_bytes).hexdigest()},
                  "duration_scope": "Locked environment observations, optional service start and postchecks; excludes preflight parsing/empty checks, includes command waits.",
                  "measurement_summaries": suites, "cgroups_before": initial,
                  "recorded_temporarily_stopped_units": cleanup.get("temporarily_stopped_units", []),
                  "skipped_units": [name for name in cleanup.get("temporarily_stopped_units", []) if name != UNIT],
                  "unit": UNIT, "before": service_state(), "sources": source_states(root)}
        eligible = UNIT in cleanup.get("temporarily_stopped_units", [])
        record["restore_eligible"] = eligible
        record["cgroups_before_action"] = empty_cgroups(args.cgroup_root)
        record["action"] = command(["systemctl", "start", UNIT]) if args.restore and eligible else {
            "executed": False, "reason": "preview_without_restore" if not args.restore else "unit_not_recorded_as_temporarily_stopped"}
        record["after"] = service_state()
        record["shm"] = shm_entries(args.shm_root)
        record["archive"] = archive_state(cleanup)
        errors = []
        if args.restore and eligible and record["action"].get("returncode") != 0:
            errors.append("systemctl start did not return success")
        for name, source in record["sources"].items():
            if source["head"].get("returncode") != 0 or source["status"].get("returncode") != 0:
                errors.append("Source identity observation failed: " + name)
        for stage in ["before", "after"]:
            if record[stage]["show"].get("returncode") != 0 or record[stage]["is_active"].get("returncode") not in [0, 3]:
                errors.append("Service state observation failed: " + stage)
        try:
            record["cgroups_after"] = empty_cgroups(args.cgroup_root)
        except (OSError, ValueError, RuntimeError) as exc:
            record["cgroups_after"] = {"empty": False, "error": str(exc)}
            errors.append("Cgroup empty verification failed after observation/restoration")
        record.update(ended_utc=utc(), end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns(), errors=errors,
                      status="observed_with_errors" if errors else "restore_command_completed" if args.restore and eligible else "observed_without_start",
                      scope="Only the recorded setroubleshootd.service may be started. DBus transient units are not recreated. No source, archive or shared-memory data is deleted or restored from archive.")
        record["duration_s"] = (record["end_mono_ns"] - record["start_mono_ns"]) / 1e9
        if args.restore:
            save_new(out, record)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 1 if errors else 0
    finally:
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/worktrees/kvc-fair-20261020"))
    parser.add_argument("--environment-root", type=Path, required=True)
    parser.add_argument("--suite", type=Path)
    parser.add_argument("--profile-suite", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    parser.add_argument("--shm-root", type=Path, default=Path("/dev/shm"))
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "unit_start_refused_or_evidence_incomplete": True}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
