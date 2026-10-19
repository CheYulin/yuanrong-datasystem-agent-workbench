#!/usr/bin/env python3
"""Preserve a failed protocol gate and correct only diagnostic helpers before measurement."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from profile_put import drain

ALLOWED = {"tcp_stage.py", "tcp_protocol_gate.py", "tcp_probe_ds_sizes.py"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Protocol archive cannot contain symlinks")
        if path.is_file():
            result[str(path.relative_to(directory))] = {"sha256": digest(path), "bytes": path.stat().st_size}
    return result


def run(args):
    root, bundle = args.root.resolve(strict=True), args.bundle.resolve(strict=True)
    suite = root / "formal-tcp-001"
    protocol = root / "protocol-gate-001"
    started_wall, started_mono = time.time_ns(), time.monotonic_ns()
    with (root / "suite.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard = drain(args.cgroup)
        if any((suite / name).exists() for name in ["events.jsonl", "summary.json", "resume-provenance.json", "cells"]):
            raise RuntimeError("Formal measurement has already been prepared or started; preflight correction is refused")
        summary = json.loads((protocol / "summary.json").read_text())
        if not summary.get("end_wall_ns") or summary.get("valid") is not False:
            raise RuntimeError("Only a terminal failed protocol gate can be preserved for retry")
        stage_path = suite / "tcp-stage.json"
        stage_hash = digest(stage_path)
        stage = json.loads(stage_path.read_text())
        audit_path = suite / "preflight-changes.json"
        audit = json.loads(audit_path.read_text()) if audit_path.exists() else {
            "schema_version": 1, "purpose": "tcp_protocol_preflight_correction", "root": str(root),
            "stage_sha256": stage_hash, "revisions": []}
        if audit.get("root") != str(root) or audit.get("stage_sha256") != stage_hash:
            raise RuntimeError("Preflight audit belongs to different immutable stage evidence")
        expected = {name: row["sha256"] for name, row in stage["inputs"].items()}
        for revision in audit["revisions"]:
            archived = Path(revision["protocol_archive"])
            if archived.is_absolute() or ".." in archived.parts or inventory(suite / archived) != revision["protocol_files"]:
                raise RuntimeError("Saved protocol archive changed")
            for change in revision["changes"]:
                name = change["relative_path"]
                if name not in ALLOWED or expected[name] != change["before_sha256"]:
                    raise RuntimeError("Invalid existing preflight hash chain")
                for key in ["before", "after"]:
                    if digest(suite / change[key + "_file"]) != change[key + "_sha256"]:
                        raise RuntimeError("Saved preflight source snapshot changed")
                expected[name] = change["after_sha256"]
        for name, checksum in expected.items():
            if digest(root / name) != checksum:
                raise RuntimeError("Unapproved staged input change: " + name)
        number = len(audit["revisions"]) + 1
        label = "attempt-" + str(number).zfill(4)
        history = suite / "preflight-history" / label
        destination = suite / "protocol-history" / label
        if history.exists() or destination.exists():
            raise FileExistsError("Existing preflight archive; preserve and inspect it")
        files = inventory(protocol)
        changes = []
        committed = []
        try:
            for name in sorted(ALLOWED):
                source = bundle / name
                if not source.is_file() or source.is_symlink():
                    raise FileNotFoundError("Missing corrected helper: " + str(source))
                after = digest(source)
                if after == expected[name]:
                    continue
                for directory, path in [("before", root / name), ("after", source)]:
                    target = history / directory / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                changes.append({"relative_path": name, "before_sha256": expected[name], "after_sha256": after,
                                "before_file": str((history / "before" / name).relative_to(suite)),
                                "after_file": str((history / "after" / name).relative_to(suite))})
            if not changes:
                raise RuntimeError("No reviewed helper corrections to apply")
            revision = {"number": number, "start_wall_ns": started_wall, "formal_started": False,
                        "guard": guard, "protocol_archive": str(destination.relative_to(suite)),
                        "protocol_files": files, "changes": changes,
                        "reason": "The pinned Python SDK omits the assumed transportType field. Use observed TCP payload and actual GET materialization; preserve the failed assertion and harden preflight guards. No performance driver, native product, adapter or workload/config changes."}
            (history / "operator").mkdir(parents=True, exist_ok=True)
            shutil.copy2(__file__, history / "operator" / Path(__file__).name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            protocol.rename(destination)
            for change in changes:
                name = change["relative_path"]
                pending = root / (name + ".preflight-pending")
                shutil.copy2(history / "after" / name, pending)
                committed.append(name)
                os.replace(pending, root / name)
            revision.update(end_wall_ns=time.time_ns(), duration_s=(time.monotonic_ns()-started_mono)/1e9)
            audit["revisions"].append(revision)
            write_json(audit_path, audit)
        except BaseException as exc:
            # The audit replacement is the commit point; never roll it back by
            # restoring helpers after a successful metadata commit.
            if audit_path.exists() and json.loads(audit_path.read_text()) == audit and audit["revisions"] and audit["revisions"][-1].get("number") == number:
                raise
            for name in committed:
                shutil.copy2(history / "before" / name, root / name)
            if destination.exists() and not protocol.exists():
                destination.rename(protocol)
            failed = suite / "failed-preflight-history" / (label + "-" + str(time.time_ns()))
            failed.parent.mkdir(parents=True, exist_ok=True)
            if history.exists():
                history.rename(failed)
            else:
                failed.mkdir()
            for path in [audit_path.with_name(audit_path.name + ".pending"), *(root / (name + ".preflight-pending") for name in ALLOWED)]:
                if path.exists():
                    shutil.move(str(path), str(failed / path.name))
            (failed / "failure.json").write_text(json.dumps({"error": repr(exc), "rolled_back": True, "end_wall_ns": time.time_ns()}))
            raise
        return {"updated_helpers": changes, "protocol_preserved": str(destination), "audit": str(audit_path),
                "stage_unchanged": digest(stage_path) == stage_hash, "duration_s": revision["duration_s"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--cgroup", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    print(json.dumps(run(parser.parse_args()), indent=2))


if __name__ == "__main__":
    main()
