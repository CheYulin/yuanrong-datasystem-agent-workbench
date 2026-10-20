#!/usr/bin/env python3
"""Refresh batch delivery documents while preserving original evidence/source bytes."""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
import zipfile

from kvcbench.platform.delivery_files import digest, inventory, verify_manifest
from kvcbench.platform.artifacts import finalize_artifacts, operation_ledger, version_zip_href


def mutable(name):
    path = Path(name)
    if name in {"reproduce.zip", "operations.jsonl", "SHA256SUMS", "delivery-tools/manifest.json"}:
        return True
    if len(path.parts) == 2 and path.parts[0] == "docs" and path.suffix == ".md":
        return True
    return name.endswith(".gz") and mutable(name[:-3])


def zip_inventory(path):
    records = {}
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Original batch ZIP CRC validation failed")
        for info in archive.infolist():
            relative = Path(info.filename)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if (info.filename in records or relative.is_absolute() or ".." in relative.parts
                    or relative.as_posix() != info.filename.rstrip("/") or not info.filename.startswith("kvc-fair/") or "\\" in info.filename
                    or kind not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError("Unsafe or duplicate original batch ZIP member")
            content = archive.read(info)
            records[info.filename] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
                                      "external_attr": info.external_attr, "internal_attr": info.internal_attr,
                                      "date_time": list(info.date_time), "compress_type": info.compress_type,
                                      "create_system": info.create_system, "extra": info.extra.hex(),
                                      "comment": info.comment.hex()}
    return records


def zip_mutable(name):
    relative = name.removeprefix("kvc-fair/")
    return relative != name and mutable(relative) and relative not in {"reproduce.zip", "SHA256SUMS"}


def source_snapshots(args, out):
    sources = [Path(__file__).resolve(), Path(__file__).resolve().parents[2] / "qa_batch_report.mjs"]
    sources += [Path(path) for path in getattr(args, "delivery_tool", [])]
    records, contents, seen = [], {}, set()
    for source in sources:
        if source.is_symlink() or not source.is_file() or source.suffix not in {".py", ".mjs", ".js", ".sh", ".md"}:
            raise ValueError("Delivery tool snapshots require explicit regular source files")
        source = source.resolve(strict=True)
        if out == source or out in source.parents:
            raise ValueError("Delivery source snapshots must come from outside the output")
        content = source.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        name = "delivery-tools/" + checksum + "-" + source.name
        if name in seen:
            continue
        seen.add(name)
        contents[name] = content
        records.append({"path": name, "sha256": checksum, "bytes": len(content), "source": str(source),
                        "role": "delivery_tool_snapshot", "original_generator_or_measured_runtime": False})
    return records, contents


def new_zip_info(name):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def refresh(args):
    if (Path(args.out) / "render-record.json").exists():
        raise ValueError("Canonical reports are immutable; render a new report attempt")
    out = Path(args.out).resolve(strict=True)
    if not out.is_dir():
        raise ValueError("Batch report output must be an existing directory")
    lock = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return refresh_locked(args, out)
    finally:
        os.close(lock)


def refresh_locked(args, out):
    if (Path(out) / "render-record.json").exists():
        raise ValueError("Canonical reports are immutable; render a new report attempt")
    cutoff = time.time_ns()
    rfc = Path(args.rfc).resolve(strict=True)
    if not rfc.is_dir() or out == rfc or out in rfc.parents or rfc in out.parents:
        raise ValueError("Use a separate existing RFC directory")
    before = verify_manifest(out)
    controlled = {"index.html", "index.html.gz"}
    if not controlled | {"reproduce.zip"} <= before.keys():
        raise ValueError("An existing HTML, matching gzip and reproduction ZIP are required")
    if any(record["mode"] != 0o644 for record in before.values()):
        raise ValueError("All original public artifacts must already be 0644")
    original_html = (out / "index.html").read_bytes()
    if gzip.decompress((out / "index.html.gz").read_bytes()) != original_html:
        raise ValueError("Original HTML gzip does not match its source bytes")
    version_zip_href(original_html.decode("utf-8"), before["reproduce.zip"]["sha256"])
    original_zip = zip_inventory(out / "reproduce.zip")
    frozen = {name: record for name, record in before.items() if not mutable(name) and name not in controlled}
    docs = sorted(rfc.glob("*.md"))
    if not docs or any(source.is_symlink() or not source.is_file() for source in docs):
        raise ValueError("RFC documents must be nonempty regular Markdown inputs")
    replacements = {"docs/" + source.name: source.read_bytes() for source in docs}
    tool_records, tool_contents = source_snapshots(args, out)
    manifest_name = "delivery-tools/manifest.json"
    old_tools = json.loads((out / manifest_name).read_bytes()) if manifest_name in before else {}
    if old_tools and (old_tools.get("schema_version") != 1 or not isinstance(old_tools.get("tools"), list)):
        raise ValueError("Unrecognized existing delivery-tool manifest")
    tools = {}
    for record in old_tools.get("tools", []):
        name = record.get("path", "")
        relative = Path(name)
        if (name in tools or relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 2
                or relative.parts[0] != "delivery-tools" or name not in frozen
                or record.get("sha256") != before[name]["sha256"] or (out / name).stat().st_size != record.get("bytes")
                or not relative.name.startswith(record["sha256"] + "-")):
            raise ValueError("Existing delivery-tool snapshot does not match its immutable bytes")
        tools[name] = record
    for record in tool_records:
        tools.setdefault(record["path"], record)
    tool_manifest = {"schema_version": 1, "cutoff_wall_ns": cutoff,
                     "cutoff_utc": datetime.datetime.fromtimestamp(cutoff / 1e9, datetime.timezone.utc).isoformat(),
                     "scope": "Delivery/QA source snapshots only. Original ZIP generator sources, executed helpers, proofs and measured runtime remain at their original paths unchanged. These snapshots do not assert that any tool ran.",
                     "tools": sorted(tools.values(), key=lambda record: record["path"])}
    replacements[manifest_name] = (json.dumps(tool_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    replacements.update(tool_contents)
    logs = [Path(path).resolve(strict=True) for path in getattr(args, "operation_log", [])]
    if any(out == path or out in path.parents for path in logs):
        raise ValueError("Use original operation logs outside the delivery")
    ledger_snapshot = None
    if logs:
        replacements["operations.jsonl"] = operation_ledger(logs, cutoff, "batch_refresh_delivery")
        ledger_snapshot = json.loads(replacements["operations.jsonl"].splitlines()[0])
    report = {"apply": bool(args.apply), "out": str(out), "original_sha256sums_verified": True,
              "original_zip_sha256": before["reproduce.zip"]["sha256"], "original_zip_members": len(original_zip),
              "original_frozen_files": len(frozen), "controlled_core_files": sorted(controlled),
              "docs": [source.name for source in docs], "delivery_tools": tool_records,
              "ledger_snapshot": ledger_snapshot,
              "scope": "Documents, explicit delivery source snapshots and optional pre-refresh operation ledger only. Original generator/measurement/evidence ZIP members and every other public core byte/mode stay frozen. Only the unique ZIP href and mtime=0 HTML gzip change. No report rendering or remote action."}
    with tempfile.TemporaryDirectory(prefix="." + out.name + ".batch-delivery-", dir=out.parent) as temporary:
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
                shutil.copy2(source, target)
        for name, content in replacements.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name in frozen:
                if target.read_bytes() != content:
                    raise ValueError("Content-addressed delivery source unexpectedly changed")
            else:
                target.write_bytes(content)
                target.chmod(0o644)
        zip_replacements = {"kvc-fair/" + name: content for name, content in replacements.items()}
        pending = dict(zip_replacements)
        with zipfile.ZipFile(out / "reproduce.zip") as old, zipfile.ZipFile(stage / "reproduce.zip", "w", zipfile.ZIP_DEFLATED) as new:
            new.comment = old.comment
            for info in old.infolist():
                content = pending.pop(info.filename, None)
                if content is not None and not zip_mutable(info.filename) and info.filename in original_zip:
                    if hashlib.sha256(content).hexdigest() != original_zip[info.filename]["sha256"]:
                        raise ValueError("A frozen original ZIP source/evidence member would change")
                new.writestr(info, old.read(info) if content is None else content)
            for name, content in sorted(pending.items()):
                new.writestr(new_zip_info(name), content)
        staged_zip = zip_inventory(stage / "reproduce.zip")
        for name, record in original_zip.items():
            if not zip_mutable(name) and staged_zip.get(name) != record:
                raise RuntimeError("Original ZIP source/evidence member was not preserved: " + name)
        if set(staged_zip) != set(original_zip) | set(zip_replacements):
            raise RuntimeError("Unexpected batch ZIP membership after refresh")
        with zipfile.ZipFile(stage / "reproduce.zip") as archive:
            for name, content in zip_replacements.items():
                if archive.read(name) != content:
                    raise RuntimeError("Updated batch document/delivery snapshot ZIP bytes differ")
        expected_html = version_zip_href(original_html.decode("utf-8"), digest(stage / "reproduce.zip")).encode("utf-8")
        (stage / "index.html").write_bytes(expected_html)
        gzip_paths = [stage / "index.html", *(stage / name for name in replacements if name not in frozen)]
        if "reproduce.zip.gz" in before:
            gzip_paths.append(stage / "reproduce.zip")
        chmod_paths = {stage / name for name in before if mutable(name) or name in controlled}
        chmod_paths |= set(gzip_paths) | {Path(str(path) + ".gz") for path in gzip_paths}
        finalize_artifacts(stage, gzip_paths=gzip_paths, chmod_paths=chmod_paths)
        complete = inventory(stage)
        (stage / "SHA256SUMS").write_text("".join(record["sha256"] + "  " + name + "\n"
                                               for name, record in sorted(complete.items()) if name != "SHA256SUMS"))
        staged = verify_manifest(stage)
        expected_controlled = {"index.html": {"sha256": hashlib.sha256(expected_html).hexdigest(), "mode": 0o644},
                               "index.html.gz": {"sha256": hashlib.sha256(gzip.compress(expected_html, mtime=0)).hexdigest(), "mode": 0o644}}
        allowed_new = set(replacements) | {name + ".gz" for name in replacements}
        if any(staged.get(name) != record for name, record in frozen.items()) or any(staged.get(name) != record for name, record in expected_controlled.items()):
            raise RuntimeError("Staged frozen core or exact HTML href/gzip bytes changed")
        if any(name not in before and name not in allowed_new for name in staged):
            raise RuntimeError("Unexpected added public batch artifact")
        if any(record["mode"] != 0o644 for record in staged.values()):
            raise RuntimeError("Every staged public artifact must be 0644")
        if verify_manifest(out) != before:
            raise RuntimeError("Original delivery changed during staging; refresh was not committed")
        updates = [name for name, record in staged.items() if name != "SHA256SUMS" and before.get(name) != record]
        updates.append("SHA256SUMS")
        report.update(zip_sha256=staged["reproduce.zip"]["sha256"], preserved_zip_members=sum(not zip_mutable(name) for name in original_zip),
                      files_updated=updates, controlled_core_changes=[{"path": name, "before": before[name], "after": staged[name]} for name in sorted(controlled)])
        if not args.apply:
            report.update(frozen_core_unchanged=True, original_output_unchanged=True)
            return report
        committed, created_dirs = [], set()
        try:
            for name in updates:
                target = out / name
                created_dirs.update(parent for parent in target.parents if parent != out and out in parent.parents and not parent.exists())
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / name, target)
                committed.append(name)
            after = verify_manifest(out)
            if after != staged:
                raise RuntimeError("Post-refresh exact inventory assertion failed")
        except BaseException:
            for name in reversed(committed):
                if (backup / name).exists():
                    os.replace(backup / name, out / name)
                else:
                    (out / name).unlink()
            for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
    report.update(frozen_core_unchanged=all(after[name] == record for name, record in frozen.items()),
                  sha256sums=digest(out / "SHA256SUMS"), public_file_mode="0644")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rfc", type=Path, required=True)
    parser.add_argument("--operation-log", type=Path, action="append", default=[])
    parser.add_argument("--delivery-tool", type=Path, action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(refresh(args), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"error": str(exc), "original_generator_rebuilt": False}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
