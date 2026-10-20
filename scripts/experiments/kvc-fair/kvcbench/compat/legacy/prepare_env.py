#!/usr/bin/env python3
"""Preserve inactive tmpfs experiment reports, then free RAM for this experiment."""

import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in {None, ""}:
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[3]))

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def holders(targets):
    prefixes = [str(p) + "/" for p in targets]
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            mapped = (proc / "maps").read_text(errors="replace")
            fds = []
            for fd in (proc / "fd").iterdir():
                try:
                    fds.append(os.readlink(fd))
                except OSError:
                    pass
            if any(prefix in mapped or any(s.startswith(prefix) for s in fds) for prefix in prefixes):
                found.append(int(proc.name))
        except (OSError, PermissionError):
            pass
    return found


def snapshot():
    return {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "meminfo": Path("/proc/meminfo").read_text(),
            "shm": shutil.disk_usage("/dev/shm")._asdict()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    names = re.compile(r"ds-(?:8f6e8b-run01-(?:internal|profile|single-breakdown)|ba465-[123]-[124]|current-[123]-[124]|independent-[123]-[124]|final-1-4)")
    targets = [p for p in Path("/dev/shm").iterdir() if names.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]
    record = {"before": snapshot(), "targets": [str(p) for p in targets], "holders": holders(targets)}
    (args.output / "environment-before.json").write_text(json.dumps(record, indent=2))
    if not args.apply:
        print(json.dumps(record, indent=2)); return
    if record["holders"]:
        sys.exit("Refusing archive: open files or mappings in candidate directories")
    archive = args.output / "preserved-tmpfs-reports.tar.gz"
    if targets:
        if archive.exists():
            sys.exit("Archive already exists; inspect prior state before rerun")
        partial = archive.with_suffix(".gz.partial")
        with partial.open("wb") as out:
            subprocess.run(["tar", "--sparse", "-C", "/dev/shm", "-I", "gzip -1", "-cf", "-", "--"] + [p.name for p in targets], stdout=out, check=True)
        subprocess.run(["gzip", "-t", str(partial)], check=True)
        with (args.output / "preserved-files.txt").open("w") as listing:
            subprocess.run(["tar", "-tzf", str(partial)], stdout=listing, check=True)
        partial.rename(archive)
        digest = hashlib.sha256()
        with archive.open("rb") as src:
            for block in iter(lambda: src.read(8 * 1024 * 1024), b""):
                digest.update(block)
        record["archive"] = {"path": str(archive), "sha256": digest.hexdigest(), "bytes": archive.stat().st_size}
        if holders(targets):
            sys.exit("Archive saved; new open mappings found, source retained")
        for target in targets:
            shutil.rmtree(target)
    units = ["setroubleshootd.service", "dbus-:1.1-org.fedoraproject.SetroubleshootPrivileged@8.service"]
    record["temporarily_stopped_units"] = []
    for unit in units:
        if subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0:
            subprocess.run(["systemctl", "stop", unit], check=True)
            record["temporarily_stopped_units"].append(unit)
    record["after"] = snapshot()
    (args.output / "environment-cleanup.json").write_text(json.dumps(record, indent=2))
    print(json.dumps({"targets": len(targets), "archive": record.get("archive"), "before_shm": record["before"]["shm"], "after_shm": record["after"]["shm"]}, indent=2))


if __name__ == "__main__":
    main()
