#!/usr/bin/env python3
"""Refresh delivery metadata after QA; preserve every core byte and permission."""
import argparse
import gzip
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

from kvcbench.platform.artifacts import finalize_artifacts, operation_ledger, version_zip_href
from kvcbench.report.package import reproduction_archive
from kvcbench.platform.delivery_files import digest, inventory, verify_manifest


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
    if (Path(args.out) / "render-record.json").exists():
        raise ValueError("Canonical reports are immutable; render a new report attempt")
    cutoff = time.time_ns()
    out, rfc, scripts = [path.resolve(strict=True) for path in [args.out, args.rfc, args.scripts]]
    if not all(path.is_dir() for path in [out, rfc, scripts]):
        raise ValueError("Output, RFC and scripts must be existing directories")
    before = verify_manifest(out)
    frozen = core_inventory(before)
    version_link = getattr(args, "version_download_link", False)
    controlled = {"index.html", "index.html.gz"} if version_link else set()
    if controlled and not controlled <= before.keys():
        raise RuntimeError("Versioned ZIP download requires existing index.html and index.html.gz")
    if any(value["mode"] != 0o644 for value in frozen.values()):
        raise RuntimeError("Core files must already be public 0644; refresh preserves their permissions")
    original_html = (out / "index.html").read_bytes() if version_link else None
    if version_link:
        version_zip_href(original_html.decode("utf-8"), before["reproduce.zip"]["sha256"])
    expected_core = dict(frozen)
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
    if version_link:
        report.update(version_download_link=True, frozen_core_files=len(frozen) - len(controlled),
                      controlled_core_files=sorted(controlled),
                      core_scope="Only the unique reviewed ZIP download href and its mtime=0 gzip may change; every other core byte and permission is frozen.",
                      scope="No report rendering, benchmark, statistics, raw-tar rebuild or remote action. Only the unique ZIP download href is versioned; this ledger stops before the refresh itself.")
        report["planned_updates"] += sorted(controlled)
    if not args.apply:
        return report
    with tempfile.TemporaryDirectory(prefix="." + out.name + ".delivery-", dir=out.parent) as temporary:
        work = Path(temporary)
        stage, backup = work / "stage", work / "backup"
        stage.mkdir(); backup.mkdir()
        for name in before:
            source, target = out / name, stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if mutable(name) or name in controlled:
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
        if version_link:
            expected_html = version_zip_href(original_html.decode("utf-8"), digest(stage / "reproduce.zip")).encode("utf-8")
            (stage / "index.html").write_bytes(expected_html)
            gzip_paths.append(stage / "index.html")
            expected_core["index.html"] = {"sha256": hashlib.sha256(expected_html).hexdigest(), "mode": before["index.html"]["mode"]}
            expected_core["index.html.gz"] = {"sha256": hashlib.sha256(gzip.compress(expected_html, mtime=0)).hexdigest(), "mode": before["index.html.gz"]["mode"]}
        chmod_paths = {stage / name for name in before if mutable(name) or name in controlled} | set(gzip_paths) | {Path(str(path) + ".gz") for path in gzip_paths}
        finalize_artifacts(stage, gzip_paths=gzip_paths, chmod_paths=chmod_paths)
        staged = verify_manifest(stage)
        if core_inventory(staged) != expected_core:
            raise RuntimeError("Staged core bytes or permissions changed; original delivery was not modified")
        if verify_manifest(out) != before:
            raise RuntimeError("Original delivery changed while staging; refresh was not committed")
        updates = [name for name in staged if (mutable(name) or name in controlled) and name != "SHA256SUMS"] + ["SHA256SUMS"]
        committed = []
        try:
            for name in updates:
                target = out / name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / name, target)
                committed.append(name)
            after = verify_manifest(out)
            if core_inventory(after) != expected_core or any(value["mode"] != 0o644 for value in after.values()):
                raise RuntimeError("Post-refresh core or public-file permission assertion failed")
        except BaseException:
            for name in reversed(committed):
                if (backup / name).exists():
                    os.replace(backup / name, out / name)
                else:
                    (out / name).unlink()
            raise
    report.update(core_unchanged=core_inventory(after) == frozen, public_file_mode="0644", files_updated=updates,
                  sha256sums=digest(out / "SHA256SUMS"), ledger_records=snapshot["included_records"])
    if version_link:
        report.update(frozen_core_unchanged=all(after[name] == value for name, value in frozen.items() if name not in controlled),
                      controlled_core_changes=[{"path": name, "before": before[name], "after": after[name], "changed": before[name] != after[name]} for name in sorted(controlled)])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rfc", type=Path, required=True)
    parser.add_argument("--scripts", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--operation-log", type=Path, action="append", required=True)
    parser.add_argument("--version-download-link", action="store_true", help="Allow only the reviewed ZIP href SHA256 query and its index.html.gz to change")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(refresh(args), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"error": str(exc), "core_rebuild_requested": False}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
