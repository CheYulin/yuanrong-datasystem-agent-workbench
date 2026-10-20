# Historical 2026-10-20 summary collector; not called by the source-build service.
#!/usr/bin/env python3
"""Collect build identity, artifact checksums and correctness-smoke status."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as data:
        for chunk in iter(lambda: data.read(8 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument("--task-root", type=Path, default=Path("/home/worktrees/kvc-fair-20261020"))
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
source = args.task_root / "datasystem"
evidence = source / ".evidence" / "build"
artifacts = sorted((source / "output").glob("*.whl")) + sorted((source / "output").glob("*.tar.gz"))
worker = source / "output/datasystem/service/datasystem_worker"
if worker.is_file():
    artifacts.append(worker)
def optional_text(name):
    path = evidence / name
    return path.read_text().strip() if path.exists() else None
result = {
    "product": "datasystem",
    "tag": "v0.9.2.rc44",
    "sha": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
    "python": subprocess.check_output([str(source / ".venv/bin/python"), "--version"], text=True).strip(),
    "compiler": subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0],
    "etcd_version": subprocess.check_output(["/usr/local/bin/etcd", "--version"], text=True).strip(),
    "etcd_sha256": digest(Path("/usr/local/bin/etcd")),
    "build_started_utc": optional_text("started-at.txt"),
    "build_finished_utc": optional_text("finished-at.txt"),
    "build_exit": int(optional_text("exit-code")) if optional_text("exit-code") is not None else None,
    "smoke_exit": int(optional_text("smoke-exit-code")) if optional_text("smoke-exit-code") is not None else None,
    "artifact_hashes": {str(path.relative_to(source)): digest(path) for path in artifacts},
    "build_scope_notes": [
        "Official v0.9.2 series latest queried tag is RC44; no final v0.9.2 tag was published at query time.",
        "Native source Release CMake, -X off -M off -A off -P shared CPython3.11.15 -t off -j8.",
        "Task-private copy of existing dependency cache; native CMake validates dependency build hashes.",
        "Parallel grpc prebuild lacked absl loader path; native main configure automatically retried successfully without source edits.",
        "Two workers, 4GiB each, no L2 payload persistence or spill; etcd/control/metadata/log IO included in service accounting.",
        "No product UT/ST, fault recovery or native kvtest performance run is claimed by correctness smoke."
    ],
}
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({k: result[k] for k in ["product", "tag", "sha", "python", "build_exit", "smoke_exit"]}))
