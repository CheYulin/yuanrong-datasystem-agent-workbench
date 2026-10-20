#!/usr/bin/env python3
"""Repair only the batch page's shared-reader header collision using a new CSS asset."""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

from refresh_delivery import digest, inventory, verify_manifest


CSS_RULE = b"\n#site-nav .site-header.yche-legacy-links{position:static;inset:auto;z-index:auto;min-height:0;padding:0;box-shadow:none;background:transparent}\n"
OLD_LINK = b'<link rel="stylesheet" href="batch-report.css">'


def record(content):
    return {"sha256": hashlib.sha256(content).hexdigest(), "mode": 0o644}


def validate_stage(stage, expected):
    if inventory(stage) != expected:
        raise RuntimeError("Staged repair differs from its exact planned bytes or modes")


def repair(args):
    started = time.monotonic_ns()
    if not re.fullmatch(r"[a-f0-9]{64}", args.expected_manifest):
        raise ValueError("Expected manifest must be a lowercase SHA256")
    source_out = Path(args.out)
    if source_out.is_symlink():
        raise ValueError("Report root cannot be a symbolic link")
    out = source_out.resolve(strict=True)
    lock = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = repair_locked(args, out)
    finally:
        os.close(lock)
    result["duration_s"] = (time.monotonic_ns() - started) / 1e9
    return result


def repair_locked(args, out):
    if digest(out / "SHA256SUMS") != args.expected_manifest:
        raise RuntimeError("Manifest CAS mismatch; no repair was staged")
    before = verify_manifest(out)
    controlled = {"index.html", "index.html.gz", "SHA256SUMS"}
    if not controlled | {"batch-report.css", "batch-report.js", "reproduce.zip"} <= before.keys():
        raise ValueError("The original complete batch delivery is required")
    if any(item["mode"] != 0o644 for item in before.values()):
        raise ValueError("Every original public file must be 0644")
    originals = {name: (out / name).read_bytes() for name in controlled}
    html = originals["index.html"]
    html.decode("utf-8")
    if gzip.decompress(originals["index.html.gz"]) != html:
        raise ValueError("Original HTML gzip does not match the HTML bytes")
    if html.count(OLD_LINK) != 1:
        raise ValueError("Expected exactly one original stylesheet link")
    css = (out / "batch-report.css").read_bytes()
    if CSS_RULE.strip() in css:
        raise ValueError("Original CSS already contains the navigation repair")
    candidate = css + CSS_RULE
    asset = "delivery-ui/" + hashlib.sha256(candidate).hexdigest() + "-batch-report.css"
    replacement = b'<link rel="stylesheet" href="' + asset.encode("ascii") + b'">'
    new_html = html.replace(OLD_LINK, replacement, 1)
    history = "delivery-ui/navigation-repair/" + args.expected_manifest
    additions = {asset: candidate, asset + ".gz": gzip.compress(candidate, mtime=0)}
    additions.update({history + "/" + name: content for name, content in originals.items()})
    helper = Path(__file__).read_bytes()
    additions[history + "/executed-helper.py"] = helper
    receipt = {"schema_version": 1, "kind": "batch_mobile_navigation_delivery_repair",
               "prepared_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "original_manifest_sha256": args.expected_manifest,
               "original_html_sha256": before["index.html"]["sha256"],
               "repaired_html_sha256": record(new_html)["sha256"],
               "stylesheet": {"original": "batch-report.css", "new": asset,
                              "original_sha256": before["batch-report.css"]["sha256"],
                              "new_sha256": record(candidate)["sha256"], "bytes": len(candidate)},
               "exact_change": "Replace only the unique exact original stylesheet href. CSS appends only the scoped shared-reader legacy-header reset.",
               "history": history, "helper_sha256": hashlib.sha256(helper).hexdigest(),
               "frozen_files": len(before) - len(controlled),
               "frozen_scope": "All original files except index.html, index.html.gz and SHA256SUMS remain unchanged, including original CSS/JS, ZIP, data, proofs, raw evidence and permissions.",
               "scope": "Delivery integration repair only. This receipt describes the planned CAS transaction; successful completion is separately emitted by the command. No experiment, report regeneration, source ZIP rewrite or remote action."}
    additions[history + "/repair-receipt.json"] = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if any(name in before or (out / name).exists() for name in additions):
        raise ValueError("Repair history or content-addressed destination already exists")
    replacements = {**additions, "index.html": new_html,
                    "index.html.gz": gzip.compress(new_html, mtime=0)}
    expected = {name: item for name, item in before.items() if name != "SHA256SUMS"}
    expected.update({name: record(content) for name, content in replacements.items()})
    manifest = "".join(item["sha256"] + "  " + name + "\n" for name, item in sorted(expected.items())).encode("utf-8")
    replacements["SHA256SUMS"] = manifest
    expected["SHA256SUMS"] = record(manifest)
    result = {"apply": bool(args.apply), "out": str(out), "original_manifest_sha256": args.expected_manifest,
              "manifest_sha256": record(manifest)["sha256"], "asset": asset,
              "asset_sha256": record(candidate)["sha256"], "history": history,
              "controlled_changes": [{"path": name, "before": before[name], "after": expected[name]}
                                     for name in sorted(controlled)],
              "new_files": sorted(additions), "frozen_files": len(before) - len(controlled),
              "frozen_original_files_unchanged": True, "original_zip_sha256": before["reproduce.zip"]["sha256"],
              "scope": receipt["scope"]}
    with tempfile.TemporaryDirectory(prefix="." + out.name + ".navigation-repair-", dir=out.parent) as temporary:
        stage, backup = Path(temporary) / "stage", Path(temporary) / "backup"
        stage.mkdir(); backup.mkdir()
        for name, content in replacements.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o644)
        for name in controlled:
            shutil.copy2(out / name, backup / name)
        validate_stage(stage, {name: record(content) for name, content in replacements.items()})
        if verify_manifest(out) != before or digest(out / "SHA256SUMS") != args.expected_manifest:
            raise RuntimeError("Original delivery changed during staging; CAS repair was not committed")
        if not args.apply:
            result["original_output_unchanged"] = True
            return result
        committed, created_dirs = [], set()
        updates = [*sorted(additions), "index.html", "index.html.gz", "SHA256SUMS"]
        try:
            for name in updates:
                target = out / name
                created_dirs.update(parent for parent in target.parents if parent != out and out in parent.parents and not parent.exists())
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / name, target)
                committed.append(name)
            if verify_manifest(out) != expected:
                raise RuntimeError("Post-repair exact inventory validation failed")
        except BaseException:
            for name in reversed(committed):
                target = out / name
                if name in controlled:
                    os.replace(backup / name, target)
                else:
                    target.unlink()
            for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
    result["applied"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(repair(args), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "original_zip_rebuilt": False}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
