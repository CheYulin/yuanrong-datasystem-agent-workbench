#!/usr/bin/env python3
"""Preserve a stopped suite session before a manual run_suite.py resume."""
import argparse
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import sys
import uuid

METADATA = ["summary.json", "status.json", "parameters.json", "products.json", "plan.json",
            "script-hashes.json", "events.jsonl", "monitor.log", "environment.json",
            "interference-audit.json", "operation-timings.jsonl"]
TREES = ["resources", "cells", "builds"]
LM_START_FIX = ("9eba24b220019fbf82cd00f278b81ef92e2a7d902908784d6b84456e6cecbc2e",
                "e6af114cac8b531179b65e6b3797b12d67aae529632cf134d5fe69c35fa1f765")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path, limit=None):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        remaining = limit
        while remaining is None or remaining:
            chunk = stream.read(8 << 20 if remaining is None else min(8 << 20, remaining))
            if not chunk:
                break
            value.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    if remaining not in (None, 0):
        raise RuntimeError("Evidence shorter than recorded prefix: " + str(path))
    return value.hexdigest()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fsync_dir(path.parent)


def publish_noreplace(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        numbers = {"x86_64": 316, "aarch64": 276}
        if platform.machine() not in numbers:
            raise RuntimeError("Atomic no-replace directory publication requires Linux renameat2")
        libc.syscall.restype = ctypes.c_long
        result = libc.syscall(numbers[platform.machine()], -100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(destination))
    fsync_dir(destination.parent)


def invalidation_plan(suite, cells, reason):
    selected = set(cells)
    if not selected:
        return []
    plan = json.loads((suite / "plan.json").read_text())
    known = {f"r{row['repeat']}-{row['product']}-s{row['size']}-c{row['concurrency']}" for row in plan}
    if selected - known:
        raise RuntimeError("Invalidation cell is absent from the preserved plan: " + ", ".join(sorted(selected - known)))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    actions = []
    for cell in sorted(selected):
        if not re.fullmatch(r"r\d+-[A-Za-z0-9_-]+-s\d+-c\d+", cell):
            raise RuntimeError("Unsafe invalidation cell: " + cell)
        found = False
        for operation in ["put", "get", "exist_hit", "exist_miss"]:
            source = suite / "cells" / cell / (operation + ".json")
            if not source.exists():
                continue
            if source.is_symlink() or not source.is_file():
                raise RuntimeError("Invalidation requires a regular original result: " + str(source))
            target = source.with_name(operation + ".superseded.interference." + stamp + ".json")
            if target.exists() or target.is_symlink():
                raise FileExistsError("Invalidation target already exists: " + str(target))
            actions.append({"cell": cell, "operation": operation, "reason_kind": "interference_exclusion", "reason": reason,
                            "source": str(source.relative_to(suite)), "target": str(target.relative_to(suite)),
                            "original_sha256": digest(source), "bytes": source.stat().st_size})
            found = True
        if not found:
            raise RuntimeError("Invalidation cell has no current result JSON to preserve: " + cell)
    return actions


def apply_invalidation(history, metadata):
    actions = metadata.get("invalidation_plan", [])
    if not actions:
        return None
    suite = Path(metadata["suite"])
    transactions = history / "transactions"
    transactions.mkdir(exist_ok=True)
    path = transactions / (metadata["session_id"] + ".json")
    record = json.loads(path.read_text()) if path.exists() else {
        "schema_version": 1, "session_id": metadata["session_id"], "created_utc": now(),
        "state": "prepared", "plan": actions, "completed_actions": [],
        "scope": "Operator-selected collection interference exclusions; original JSON valid fields are unchanged. This is not an API failure."}
    if record["plan"] != actions:
        raise RuntimeError("Invalidation transaction differs from immutable archived plan")
    atomic_json(path, record)
    for action in actions:
        for name in ["source", "target"]:
            relative = Path(action[name])
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "cells":
                raise RuntimeError("Unsafe invalidation transaction path")
        source, target = suite / action["source"], suite / action["target"]
        if source.parent != target.parent or ".superseded.interference." not in target.name:
            raise RuntimeError("Invalidation may only rename within the original cell")
        if source.exists() and target.exists():
            raise RuntimeError("Both original and superseded result exist; refusing ambiguous recovery")
        if target.exists():
            if target.is_symlink() or digest(target) != action["original_sha256"]:
                raise RuntimeError("Superseded result hash changed: " + str(target))
        else:
            if source.is_symlink() or not source.is_file() or digest(source) != action["original_sha256"]:
                raise RuntimeError("Original result changed or missing; refusing invalidation: " + str(source))
            publish_noreplace(source, target)
        if action["source"] not in record["completed_actions"]:
            record["completed_actions"].append(action["source"])
        record["updated_utc"] = now()
        atomic_json(path, record)
    record.update(state="complete", completed_utc=now())
    atomic_json(path, record)
    return str(path)


def idle_cgroups(root):
    required = [root / "cgroup.procs"] + [root / group / "cgroup.procs" for group in ["server", "client", "monitor"]]
    if any(not path.is_file() for path in required):
        raise RuntimeError("Cannot prove cgroups idle: required cgroup.procs is missing")
    records = {}
    for path in sorted(set(required + list(root.rglob("cgroup.procs")))):
        pids = path.read_text().split()
        records[str(path)] = pids
        if pids:
            raise RuntimeError("Refusing archive while cgroup has tasks: " + str(path) + ": " + " ".join(pids))
    for path in root.rglob("cgroup.events"):
        values = dict(line.split() for line in path.read_text().splitlines() if line.strip())
        if values.get("populated") == "1":
            raise RuntimeError("Refusing archive while cgroup subtree is populated: " + str(path))
    return records


def inventory(suite):
    roots = [suite / name for name in METADATA + TREES if (suite / name).exists() or (suite / name).is_symlink()]
    result = {}
    for selected in roots:
        paths = [selected] + sorted(selected.rglob("*")) if selected.is_dir() and not selected.is_symlink() else [selected]
        for path in paths:
            relative = str(path.relative_to(suite))
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[relative] = {"type": "symlink", "target": os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = {"type": "directory"}
            elif stat.S_ISREG(info.st_mode):
                result[relative] = {"type": "file", "bytes": info.st_size, "sha256": digest(path)}
            else:
                raise RuntimeError("Refusing special evidence file: " + str(path))
    return result


def validate_archive(path, expected_manifest_sha=None):
    manifest_path = path / "manifest.json"
    if expected_manifest_sha and digest(manifest_path) != expected_manifest_sha:
        raise RuntimeError("Archived manifest hash changed: " + str(path))
    manifest = json.loads(manifest_path.read_text())
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Unsafe archive manifest path: " + name)
        item = path / relative
        info = item.lstat()
        if expected["type"] == "file":
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected["bytes"] or digest(item) != expected["sha256"]:
                raise RuntimeError("Archived file hash changed: " + str(item))
        elif expected["type"] == "symlink":
            if not stat.S_ISLNK(info.st_mode) or os.readlink(item) != expected["target"]:
                raise RuntimeError("Archived symlink changed: " + str(item))
        elif not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Archived directory changed: " + str(item))
    return json.loads((path / "session.json").read_text()), digest(manifest_path)


def existing_sessions(history):
    indexed = {}
    index_path = history / "index.json"
    if index_path.exists():
        indexed = {row["session_id"]: row for row in json.loads(index_path.read_text())["sessions"]}
    sessions = []
    for path in sorted(history.glob("session-*")):
        if not re.fullmatch(r"session-\d{4,}", path.name) or path.is_symlink() or not path.is_dir():
            raise RuntimeError("Unexpected history session path: " + str(path))
        metadata, manifest_sha = validate_archive(path, indexed.get(path.name, {}).get("manifest_sha256"))
        sessions.append({**metadata, "manifest_sha256": manifest_sha})
    if set(indexed) - {row["session_id"] for row in sessions}:
        raise RuntimeError("History index refers to missing archives")
    sessions.sort(key=lambda row: int(row["session_id"].split("-")[-1]))
    return sessions


def seconds(start, end):
    if not start or not end:
        return None
    first, last = [dt.datetime.fromisoformat(value.replace("Z", "+00:00")) for value in [start, end]]
    if first.tzinfo is None or last.tzinfo is None:
        return None
    return (last - first).total_seconds()


def aggregate(sessions):
    rows = [{"session_id": row["session_id"], "summary": row.get("original_summary"),
             "archived_utc": row.get("archived_utc"), "events_range": row["events_range"],
             "manifest_sha256": row.get("manifest_sha256"), "current_unarchived": row.get("current_unarchived", False)} for row in sessions]
    summaries = [row.get("original_summary") or {} for row in sessions]
    durations = [seconds(row.get("started"), row.get("ended")) for row in summaries]
    first = summaries[0].get("started") if summaries else None
    last = summaries[-1].get("ended") if summaries else None
    failures = [{"session_id": row["session_id"], **failure}
                for row, summary in zip(sessions, summaries) for failure in summary.get("failed", [])]
    return {"schema_version": 1, "generated_utc": now(), "sessions": rows,
            "earliest_start_utc": first, "latest_end_utc": last,
            "first_original_start_utc": first, "latest_original_end_utc": last,
            "wall_span_including_gaps_s": seconds(first, last),
            "sum_session_wall_s": sum(durations) if durations and all(value is not None for value in durations) else None,
            "recorded_session_wall_s": sum(value for value in durations if value is not None),
            "historical_failure_count": len(failures), "historical_failed_attempts": failures,
            "scope": "Original session boundaries retained; wall span includes operator gaps, while summed session wall excludes gaps. Missing original ends remain null; archive time is not substituted."}


def resume_command(parameters, root, suite):
    recorded_root = Path(parameters["root"])
    if not recorded_root.is_absolute():
        recorded_root = root / recorded_root
    recorded_output = Path(parameters["output"])
    if not recorded_output.is_absolute():
        recorded_output = root / recorded_output
    if recorded_root.resolve() != root or recorded_output.resolve() != suite:
        raise RuntimeError("Recorded root/output do not match the requested archive target")
    manifest = Path(parameters["manifest"])
    if not manifest.is_absolute():
        manifest = root / manifest
    if not manifest.is_file() or not (root / "run_suite.py").is_file():
        raise RuntimeError("Original manifest or run_suite.py is unavailable; no resume command inferred")
    command = ["python3", str(root / "run_suite.py"), "--root", str(root), "--manifest", str(manifest), "--output", str(suite)]
    for key in ["duration", "warmup", "repeats"]:
        command += ["--" + key, str(parameters[key])]
    for key in ["sizes", "concurrencies"]:
        command += ["--" + key, *map(str, parameters[key])]
    if parameters.get("products"):
        command += ["--products", *parameters["products"]]
    if parameters.get("smoke"):
        command += ["--smoke"]
    return command


def check_inputs(parameters, root, suite, approved_changes):
    recorded = json.loads((suite / "script-hashes.json").read_text())
    required = {"benchmark.py", "cgroup.py", "monitor.py", "run_suite.py"}
    if not required.issubset(recorded):
        raise RuntimeError("Initial script-hashes lacks required benchmark/cgroup/monitor/driver baseline")
    baseline = {}
    for name, expected in recorded.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Unsafe initial script-hash path: " + name)
        path = root / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise RuntimeError("Original recorded driver/helper changed; refusing legacy resume: " + name)
        baseline[name] = expected
    manifest = Path(parameters["manifest"])
    manifest = manifest if manifest.is_absolute() else root / manifest
    selected = json.loads(manifest.read_text())["products"]
    if parameters.get("products"):
        selected = [product for product in selected if product["name"] in parameters["products"]]
    if selected != json.loads((suite / "products.json").read_text()):
        raise RuntimeError("Current selected manifest differs from the preserved original product configuration")
    paths = {manifest}
    for product in selected:
        for key in ["adapter", "config"]:
            path = Path(product[key])
            paths.add(path if path.is_absolute() else root / path)
        for value in product.get("start", []) + product.get("stop", []):
            path = Path(value)
            if path.is_absolute() and path.is_file() and path.suffix in [".py", ".sh", ".json"]:
                paths.add(path)
        parent = Path(product["adapter"]).parent
        parent = parent if parent.is_absolute() else root / parent
        paths.update(parent.glob("*.py")); paths.update(parent.glob("*.sh"))
    for value in approved_changes:
        relative, separator, reason = value.partition("=")
        if not separator or not reason.strip() or relative != "lmcache/start_server.sh":
            raise RuntimeError("Approved change must explicitly name lmcache/start_server.sh=OPERATOR_REASON")
        paths.add(root / relative)
    fingerprints = {}
    for path in sorted(paths):
        if not path.is_file():
            raise RuntimeError("Current adapter/runtime/manifest/config input unavailable: " + str(path))
        fingerprints[str(path)] = {"sha256": digest(path), "bytes": path.stat().st_size}
    approvals = [{"path": value.partition("=")[0], "operator_reason": value.partition("=")[2],
                  "sha256": fingerprints[str(root / value.partition("=")[0])]["sha256"]}
                 for value in approved_changes]
    approved = {row["path"] for row in approvals}
    build_baselines = {}
    for product in selected:
        build_path = suite / "builds" / (product["name"] + ".json")
        if not build_path.is_file() or build_path.is_symlink():
            raise RuntimeError("Original product build summary unavailable: " + str(build_path))
        build = json.loads(build_path.read_text())
        for field in ["artifact_hashes", "script_hashes"]:
            values = build.get(field, {})
            entries = values.items() if isinstance(values, dict) else ((row["path"], row["sha256"]) for row in values)
            for name, expected in entries:
                relative = Path(name) if Path(name).parts and Path(name).parts[0] == product["name"] else Path(product["name"]) / name
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise RuntimeError("Unsafe product build hash path: " + name)
                path = root / relative
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError("Product build evidence input unavailable: " + str(path))
                current = digest(path)
                changed = current != expected
                allowed = str(relative) in approved and (expected, current) == LM_START_FIX
                if changed and not allowed:
                    raise RuntimeError("Product build baseline changed without the exact authorized cwd fix: " + str(relative))
                build_baselines[str(relative)] = {"expected_sha256": expected, "current_sha256": current,
                                                 "status": "operator_approved_cwd_fix" if changed else "unchanged",
                                                 "evidence": str(build_path) + "#/" + field}
    return {"initial_recorded_script_hashes_verified": baseline, "current_input_hashes": fingerprints,
            "product_build_baselines": build_baselines, "operator_approved_changes": approvals,
            "scope": "Original top-level recorded paths and product build-summary artifact/script hashes are verified; new helper files do not invalidate the original path set. Only the explicitly approved LMCache start_server.sh cwd-fix hash pair may differ. Current manifest/config hashes are additionally recorded; no unrecorded initial hash is invented."}


def prepare(root, suite, cgroup_root, session=None, apply=False, finalize=False, approved_changes=(),
            invalidate_cells=(), invalidation_reason="Operator-selected cell overlaps collection activity; retain original bytes and rerun the complete cell", recover_session=None):
    if recover_session and (session is not None or finalize or invalidate_cells) or finalize and invalidate_cells:
        raise ValueError("Recovery/finalization cannot be combined with a new archive session or invalidation plan")
    root, suite, cgroup_root = [Path(value).resolve() for value in [root, suite, cgroup_root]]
    if not root.is_dir() or not suite.is_dir():
        raise RuntimeError("Root and suite must already exist")
    lock_path = root / "suite.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Suite lock is held; refusing archive while run_suite may be active") from None
        cgroups = idle_cgroups(cgroup_root)
        parameters_path = suite / "parameters.json"
        if parameters_path.is_symlink():
            raise RuntimeError("Refusing symlinked suite parameters")
        parameters = json.loads(parameters_path.read_text())
        command = resume_command(parameters, root, suite)
        inputs = check_inputs(parameters, root, suite, approved_changes)
        history = suite / "history"
        if history.is_symlink():
            raise RuntimeError("Refusing symlinked history directory")
        sessions = existing_sessions(history) if history.exists() else []
        if apply and sessions:
            atomic_json(history / "index.json", {"schema_version": 1, "updated_utc": now(), "sessions": sessions})
        if recover_session:
            identity = f"session-{int(recover_session.removeprefix('session-')):04d}"
            metadata = next((row for row in sessions if row["session_id"] == identity), None)
            if metadata is None or metadata["suite"] != str(suite):
                raise RuntimeError("No matching committed session for invalidation recovery")
            transaction = apply_invalidation(history, metadata) if apply else str(history / "transactions" / (identity + ".json"))
            return {"apply": apply, "recovered_session": identity, "invalidation_manifest": transaction,
                    "plan": metadata.get("invalidation_plan", []), "resume_command": "cd " + shlex.quote(str(root)) + " && " + shlex.join(command)}
        for archived in sessions:
            if archived.get("invalidation_plan"):
                transaction = history / "transactions" / (archived["session_id"] + ".json")
                if not transaction.exists() or json.loads(transaction.read_text()).get("state") != "complete":
                    raise RuntimeError("Pending invalidation; use --recover-session " + archived["session_id"] + " --apply")
        if finalize:
            current = json.loads((suite / "summary.json").read_text())
            if not current.get("ended"):
                raise RuntimeError("Finalization requires the last session's original ended boundary")
            event_path = suite / "events.jsonl"
            end_byte = event_path.stat().st_size
            begin_byte = sessions[-1]["events_range"]["end_byte"] if sessions else 0
            archived_summary = history / sessions[-1]["session_id"] / "summary.json" if sessions else None
            same_summary = archived_summary is not None and archived_summary.exists() and (suite / "summary.json").read_bytes() == archived_summary.read_bytes()
            if not same_summary or end_byte > begin_byte:
                if sessions and digest(event_path, begin_byte) != sessions[-1]["events_prefix_sha256"]:
                    raise RuntimeError("Current events no longer preserve the archived append prefix")
                number = int(sessions[-1]["session_id"].split("-")[-1]) + 1 if sessions else 1
                sessions.append({"session_id": f"session-{number:04d}-current", "original_summary": current,
                                 "events_range": {"start_byte": begin_byte, "end_byte": end_byte}, "current_unarchived": True})
            result = aggregate(sessions)
            if apply:
                atomic_json(suite / "session-summary.json", result)
            return {"apply": apply, "finalize": True, "aggregate": str(suite / "session-summary.json"),
                    "summary_not_overwritten": True, "result": result}
        number = int(session.removeprefix("session-")) if session is not None else (int(sessions[-1]["session_id"].split("-")[-1]) + 1 if sessions else 1)
        if number < 1:
            raise RuntimeError("Session number must be positive")
        identity = f"session-{number:04d}"
        destination = history / identity
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Refusing to overwrite existing session archive: " + str(destination))
        if sessions and number <= int(sessions[-1]["session_id"].split("-")[-1]):
            raise RuntimeError("Session numbers must increase")
        before = inventory(suite)
        exclusions = invalidation_plan(suite, invalidate_cells, invalidation_reason)
        fingerprint = hashlib.sha256(json.dumps({name: value for name, value in before.items() if name in METADATA}, sort_keys=True).encode()).hexdigest()
        if any(row["source_fingerprint"] == fingerprint for row in sessions):
            raise RuntimeError("This unchanged source session is already archived; no duplicate session created")
        events_path = suite / "events.jsonl"
        if not events_path.is_file() or events_path.is_symlink():
            raise RuntimeError("A regular events.jsonl is required to preserve session append boundaries")
        event_bytes = events_path.stat().st_size
        if event_bytes:
            with events_path.open("rb") as stream:
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    raise RuntimeError("events.jsonl has a non-newline tail; preserve/repair it explicitly before append resume")
        previous_bytes = sessions[-1]["events_range"]["end_byte"] if sessions else 0
        if sessions and digest(events_path, previous_bytes) != sessions[-1]["events_prefix_sha256"]:
            raise RuntimeError("Current events.jsonl no longer preserves the archived append prefix")
        if event_bytes <= previous_bytes and sessions:
            raise RuntimeError("No new session events after the last archive")
        total_bytes = sum(row.get("bytes", 0) for row in before.values())
        free = shutil.disk_usage(suite).free
        if free < total_bytes + (64 << 20):
            raise RuntimeError("Insufficient space to copy and preserve all selected evidence")
        report = {"session_id": identity, "archive": str(destination), "apply": apply,
                  "first_session_archive": str(history / sessions[0]["session_id"]) if sessions else str(destination),
                  "current_session_archive": str(destination),
                  "copy_bytes": total_bytes, "cgroups_checked_empty": cgroups,
                  "events_range": {"start_byte": previous_bytes, "end_byte": event_bytes},
                  "input_validation": inputs,
                  "invalidation_plan": exclusions,
                  "resume_argv": command, "resume_command": "cd " + shlex.quote(str(root)) + " && " + shlex.join(command),
                  "scope": "No services are started/stopped. All metadata, resources and cells are copied; samples.jsonl and events.jsonl remain available for source append."}
        if not apply:
            return report
        history.mkdir(exist_ok=True)
        stage = history / ("." + identity + ".partial-" + uuid.uuid4().hex)
        stage.mkdir()
        for name, item in before.items():
            source, target = suite / name, stage / name
            if item["type"] == "directory":
                target.mkdir(parents=True, exist_ok=True)
            elif item["type"] == "symlink":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(item["target"])
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                with target.open("rb") as stream:
                    os.fsync(stream.fileno())
                if digest(target) != item["sha256"]:
                    raise RuntimeError("Copied evidence hash mismatch: " + name)
        if inventory(suite) != before:
            raise RuntimeError("Source evidence changed while archiving; partial archive retained without publication")
        idle_cgroups(cgroup_root)
        summary_error = None
        try:
            original_summary = json.loads((suite / "summary.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            original_summary, summary_error = None, str(exc)
        metadata = {"schema_version": 1, "session_id": identity, "archived_utc": now(),
                    "suite": str(suite), "root": str(root), "source_fingerprint": fingerprint,
                    "events_range": report["events_range"], "events_prefix_sha256": digest(events_path),
                    "original_summary": original_summary, "summary_parse_error": summary_error,
                    "cgroups_verified_empty": cgroups, "resume_argv": command,
                    "input_validation": inputs,
                    "invalidation_plan": exclusions,
                    "missing_metadata": [name for name in METADATA if name not in before],
                    "samples_behavior": "Full source snapshot copied; source samples.jsonl remains append-only, while native monitor logs may be overwritten by the next run."}
        atomic_json(stage / "session.json", metadata)
        files = {**before, "session.json": {"type": "file", "bytes": (stage / "session.json").stat().st_size, "sha256": digest(stage / "session.json")}}
        atomic_json(stage / "manifest.json", {"schema_version": 1, "session_id": identity, "files": files})
        for directory in sorted([p for p in stage.rglob("*") if p.is_dir() and not p.is_symlink()], reverse=True):
            fsync_dir(directory)
        fsync_dir(stage)
        publish_noreplace(stage, destination)
        metadata["manifest_sha256"] = digest(destination / "manifest.json")
        sessions.append(metadata)
        atomic_json(history / "index.json", {"schema_version": 1, "updated_utc": now(), "sessions": sessions})
        atomic_json(suite / "session-summary.json", aggregate(sessions))
        try:
            transaction = apply_invalidation(history, metadata)
        except Exception as exc:
            raise RuntimeError("Archive committed; invalidation incomplete. Recover with --recover-session " + identity + " --apply: " + str(exc)) from exc
        report.update(manifest_sha256=metadata["manifest_sha256"], completed=True,
                      invalidation_manifest=transaction,
                      next_step="Inspect the archived manifest, then manually execute resume_command. This tool never launches the suite.")
        return report
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    parser.add_argument("--session")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--approved-change", action="append", default=[])
    parser.add_argument("--invalidate-cell", action="append", default=[])
    parser.add_argument("--invalidation-reason", default="Operator-selected cell overlaps collection activity; retain original bytes and rerun the complete cell")
    parser.add_argument("--recover-session")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.root, args.suite, args.cgroup_root, args.session, args.apply, args.finalize, args.approved_change,
                                 args.invalidate_cell, args.invalidation_reason, args.recover_session), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "suite": str(args.suite), "archive_not_overwritten": True}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
