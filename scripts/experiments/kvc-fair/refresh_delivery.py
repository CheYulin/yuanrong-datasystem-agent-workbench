#!/usr/bin/env python3
"""Refresh delivery metadata after QA; preserve every core byte and permission."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import zipfile

from report import finalize_artifacts, operation_ledger, reproduction_archive


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            result.update(block)
    return result.hexdigest()


def inventory(out):
    result = {}
    for path in sorted(out.rglob("*")):
        info = path.lstat()
        if path.is_symlink() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise RuntimeError("Only regular files/directories are allowed in delivery: " + str(path))
        if path.is_file():
            result[path.relative_to(out).as_posix()] = {"sha256": digest(path), "mode": stat.S_IMODE(info.st_mode)}
    return result


def verify_manifest(out):
    manifest = out / "SHA256SUMS"
    if not manifest.is_file() or manifest.is_symlink():
        raise RuntimeError("An existing regular SHA256SUMS is required")
    expected = {}
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        checksum, separator, name = line.partition("  ")
        relative = Path(name)
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", checksum) or relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or name in expected or name == "SHA256SUMS":
            raise RuntimeError("Invalid or duplicate SHA256SUMS entry: " + line)
        expected[name] = checksum
    observed = inventory(out)
    if set(observed) != set(expected) | {"SHA256SUMS"}:
        raise RuntimeError("Delivery file set differs from SHA256SUMS; untracked/missing files must be reviewed first")
    for name, checksum in expected.items():
        if observed[name]["sha256"] != checksum:
            raise RuntimeError("Original artifact checksum mismatch: " + name)
    return observed


def mutable(name):
    path = Path(name)
    if name in {"reproduce.zip", "operations.jsonl", "SHA256SUMS"}:
        return True
    if len(path.parts) == 2 and path.parts[0] == "docs" and path.suffix == ".md":
        return True
    return name.endswith(".gz") and mutable(name[:-3])


def core_inventory(observed):
    return {name: value for name, value in observed.items() if not mutable(name)}


def refresh(args):
    cutoff = time.time_ns()
    out, rfc, scripts = [path.resolve(strict=True) for path in [args.out, args.rfc, args.scripts]]
    if not all(path.is_dir() for path in [out, rfc, scripts]):
        raise ValueError("Output, RFC and scripts must be existing directories")
    before = verify_manifest(out)
    frozen = core_inventory(before)
    if any(value["mode"] != 0o644 for value in frozen.values()):
        raise RuntimeError("Core files must already be public 0644; refresh preserves their permissions")
    docs = sorted(rfc.glob("*.md"))
    if not docs or any(path.is_symlink() or not path.is_file() for path in docs):
        raise ValueError("RFC Markdown inputs must be regular files and nonempty")
    logs = [path.resolve(strict=True) for path in args.operation_log]
    if any(out == path or out in path.parents for path in logs):
        raise ValueError("Use original operation logs outside the delivery; its own ledger is not an input")
    ledger = operation_ledger(logs, cutoff, "refresh_delivery")
    snapshot = json.loads(ledger.splitlines()[0])
    report = {"apply": args.apply, "out": str(out), "original_sha256sums_verified": True,
              "core_files": len(frozen), "core_scope": "All artifacts except docs/*.md, reproduce.zip, operations.jsonl, SHA256SUMS and their own gzip companions; core gzip bytes/header and file permissions are frozen.",
              "docs": [path.name for path in docs], "ledger_snapshot": snapshot,
              "planned_updates": ["docs/*.md", "docs/*.md.gz", "reproduce.zip", "operations.jsonl", "operations.jsonl.gz", "SHA256SUMS"],
              "scope": "No report rendering, benchmark, statistics, raw-tar rebuild, UI edit or remote action. This ledger stops before the refresh itself."}
    if not args.apply:
        return report
    with tempfile.TemporaryDirectory(prefix="." + out.name + ".delivery-", dir=out.parent) as temporary:
        work = Path(temporary)
        stage, backup = work / "stage", work / "backup"
        stage.mkdir(); backup.mkdir()
        for name in before:
            source, target = out / name, stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if mutable(name):
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, saved)
                if name != "SHA256SUMS":
                    shutil.copy2(source, target)
            else:
                os.link(source, target)
        (stage / "docs").mkdir(exist_ok=True)
        for source in docs:
            shutil.copy2(source, stage / "docs" / source.name)
        reproduction_archive(scripts, rfc, stage)
        with zipfile.ZipFile(stage / "reproduce.zip") as zipped:
            if zipped.testzip() is not None or len(zipped.namelist()) != len(set(zipped.namelist())):
                raise RuntimeError("Refreshed reproduction ZIP has invalid or duplicate entries")
            for source in docs:
                if zipped.read("kvc-fair/docs/" + source.name) != (stage / "docs" / source.name).read_bytes():
                    raise RuntimeError("RFC changed during refresh: " + source.name)
        (stage / "operations.jsonl").write_bytes(ledger)
        gzip_paths = [stage / "operations.jsonl", *(stage / "docs" / source.name for source in docs)]
        if (stage / "reproduce.zip.gz").exists():
            gzip_paths.append(stage / "reproduce.zip")
        chmod_paths = {stage / name for name in before if mutable(name)} | set(gzip_paths) | {Path(str(path) + ".gz") for path in gzip_paths}
        finalize_artifacts(stage, gzip_paths=gzip_paths, chmod_paths=chmod_paths)
        staged = verify_manifest(stage)
        if core_inventory(staged) != frozen:
            raise RuntimeError("Staged core bytes or permissions changed; original delivery was not modified")
        if verify_manifest(out) != before:
            raise RuntimeError("Original delivery changed while staging; refresh was not committed")
        updates = [name for name in staged if mutable(name) and name != "SHA256SUMS"] + ["SHA256SUMS"]
        committed = []
        try:
            for name in updates:
                target = out / name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / name, target)
                committed.append(name)
            after = verify_manifest(out)
            if core_inventory(after) != frozen or any(value["mode"] != 0o644 for value in after.values()):
                raise RuntimeError("Post-refresh core or public-file permission assertion failed")
        except BaseException:
            for name in reversed(committed):
                if (backup / name).exists():
                    os.replace(backup / name, out / name)
                else:
                    (out / name).unlink()
            raise
    report.update(core_unchanged=True, public_file_mode="0644", files_updated=updates,
                  sha256sums=digest(out / "SHA256SUMS"), ledger_records=snapshot["included_records"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rfc", type=Path, required=True)
    parser.add_argument("--scripts", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--operation-log", type=Path, action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(refresh(args), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"error": str(exc), "core_rebuild_requested": False}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
