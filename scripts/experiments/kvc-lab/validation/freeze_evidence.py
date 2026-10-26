#!/usr/bin/env python3
"""Freeze a closed study004 job's minimal evidence over one read-only SSH tar stream.

python3 validation/freeze_evidence.py --run terminal-live-driver.json \
  --target tiantiyun-80c128g --out /new/local/frozen-job

The remote code reads /etc/kvc-lab/runner.json and only /home/cache/kvc-lab/jobs/ID.
No compression, process control, directory scan, SDK import or remote writes occur.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import time

SOURCE = "61c5c2e3151b227d5cc8bc86688164e622027ce953f67ef555ca54c4e044779e"
BASE = {"complete.json", "request.json", "build-verification.json", "coverage.json", "artifact-manifest.json", "commands.json"}
DETAIL = {"qualification.json", "one-hop.json", "resource-limits.json", "effective-config.json", "coordinator-config.json", "master-config.json"}
MAX_FILE = 16 << 20
MAX_TOTAL = 512 << 20


def canonical(name):
    if not isinstance(name, str) or "\\" in name or not name or Path(name).is_absolute() or ".." in Path(name).parts or str(Path(name)) != name or name == ".":
        raise ValueError("Noncanonical evidence member")
    return name


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def selection(evidence):
    job = evidence["job"]
    complete = job.get("completion")
    if not re.fullmatch(r"[a-f0-9]{32}", job["id"]) or not isinstance(complete, dict) or complete.get("job_id") != job["id"] or job.get("status") != complete.get("status") or complete.get("status") not in {"succeeded", "completed_with_failures", "failed", "cancelled", "interrupted"}:
        raise ValueError("An authoritative closed matching job is required")
    if (job.get("provenance") or {}).get("source_sha256") != SOURCE:
        raise ValueError("Only the frozen study004 source identity is accepted")
    records = {}
    for record in complete["artifacts"]:
        name = canonical(record["name"])
        if name in records:
            raise ValueError("Duplicate manifest member")
        records[name] = record
    names = set(BASE)
    deployments = set()
    for row in complete["results"]:
        name = canonical(row["raw_artifact"])
        names.add(name)
        deployments.add(str(Path(name).parent.parent))
    for name, record in records.items():
        source = canonical(record.get("source", name))
        if Path(source).name in DETAIL or re.fullmatch(r"adapter-[0-9]+\.json", Path(source).name) or source.endswith("/resources.jsonl"):
            names.add(name)
    for deployment in deployments:
        for suffix in ["qualification.json", "resource-limits.json"]:
            if deployment + "/" + suffix not in names:
                raise ValueError("Deployment evidence is missing: " + deployment + "/" + suffix)
        if not any(record.get("source", name) == deployment + "/resources.jsonl" for name, record in records.items() if name in names):
            raise ValueError("Deployment resource samples are missing")
    for name in names - {"complete.json"}:
        record = records.get(name)
        if record is None or type(record.get("size")) is not int or not 0 <= record["size"] <= MAX_FILE or not isinstance(record.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]):
            raise ValueError("Selected member lacks a bounded hash identity: " + name)
    if sum(records[name]["size"] for name in names if name != "complete.json") > MAX_TOTAL:
        raise ValueError("Selected evidence exceeds 512MiB")
    return job, {name: records.get(name) for name in sorted(names)}


REMOTE = r'''
import hashlib,json,os,pathlib,stat,sys,tarfile
os.sched_setaffinity(0,{38,39})
r=json.load(sys.stdin)
c=json.loads(pathlib.Path('/etc/kvc-lab/runner.json').read_text())
assert c['state_root']=='/home/cache/kvc-lab'
root=pathlib.Path(c['state_root'])/'jobs'/r['job_id']
assert not any(p.is_symlink() for p in [root,*root.parents])
complete=json.loads((root/'complete.json').read_text())
assert complete['job_id']==r['job_id'] and complete['status']==r['status'] and complete['results']==r['results']
manifest={x['name']:x for x in complete['artifacts']}
paths=[]
for name,expected in r['files'].items():
 p=root/name
 assert not any(x.is_symlink() for x in [p,*p.parents]) and stat.S_ISREG(p.stat().st_mode)
 assert p.stat().st_size <= 16*1024*1024
 if expected is not None:
  assert name in manifest and expected==manifest[name]
  h=hashlib.sha256()
  with p.open('rb') as source:
   for block in iter(lambda:source.read(1048576),b''):h.update(block)
  assert p.stat().st_size==expected['size'] and h.hexdigest()==expected['sha256']
 paths.append((name,p))
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
 for name,p in paths:archive.add(p,arcname=name,recursive=False)
'''


def extract(archive, out, selected):
    seen, total, identities = set(), 0, []
    with tarfile.open(archive, mode="r:") as source:
        for member in source:
            name = canonical(member.name)
            if name not in selected or name in seen or not member.isfile() or member.issym() or member.islnk() or not 0 <= member.size <= MAX_FILE:
                raise ValueError("Unexpected, linked, duplicate or oversized tar member: " + name)
            total += member.size
            if total > MAX_TOTAL:
                raise ValueError("Archive exceeds the evidence budget")
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as target, source.extractfile(member) as data:
                while True:
                    block = data.read(1 << 20)
                    if not block:
                        break
                    target.write(block)
            identity = {"name": name, "size": path.stat().st_size, "sha256": digest(path)}
            expected = selected[name]
            if identity["size"] != member.size or expected is not None and any(identity[key] != expected[key] for key in ["size", "sha256"]):
                raise ValueError("Transferred evidence SHA/size mismatch: " + name)
            seen.add(name)
            identities.append(identity)
    if seen != set(selected):
        raise ValueError("Transferred member set differs from the exact requested allowlist")
    return identities


def main():
    began = time.monotonic_ns()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.target):
        parser.error("An existing SSH alias, not options or a command, is required")
    evidence = json.loads(Path(args.run).read_text())
    job, selected = selection(evidence)
    out = Path(args.out).absolute()
    if any(parent.is_symlink() for parent in [out, *out.parents]):
        raise ValueError("Output paths must not contain symlinks")
    out.mkdir(parents=True, exist_ok=False)
    archive = out / "transfer.tar"
    request = {"job_id": job["id"], "status": job["completion"]["status"], "files": selected,
               "results": [{key: value for key, value in row.items() if key != "raw_url"} for row in job["completion"]["results"]]}
    record = {"schema_version": 1, "job_id": job["id"], "source_sha256": SOURCE, "target_alias": args.target,
              "started_wall_ns": time.time_ns(), "input_evidence_sha256": digest(Path(args.run)), "script_sha256": digest(Path(__file__)),
              "remote_config": "/etc/kvc-lab/runner.json", "remote_read_cpus": [38, 39], "selected_names": sorted(selected), "valid": False}
    try:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.target, "python3 -B -c " + shlex.quote(REMOTE)]
        transfer_started = time.monotonic_ns()
        with archive.open("xb") as output, (out / "transfer.stderr.log").open("xb") as errors:
            process = subprocess.run(argv, input=json.dumps(request).encode(), stdout=output, stderr=errors, timeout=600)
        record["transfer_duration_s"] = (time.monotonic_ns() - transfer_started) / 1e9
        if process.returncode:
            raise RuntimeError("Read-only SSH collection failed; inspect preserved transfer.stderr.log")
        extraction_started = time.monotonic_ns()
        identities = extract(archive, out, selected)
        record["extraction_duration_s"] = (time.monotonic_ns() - extraction_started) / 1e9
        complete = json.loads((out / "complete.json").read_text())
        if complete.get("job_id") != job["id"] or complete.get("results") != request["results"] or complete.get("status") != request["status"]:
            raise ValueError("Transferred terminal completion differs from browser evidence")
        record.update(valid=True, files=identities, archive_sha256=digest(archive), archive_bytes=archive.stat().st_size)
    except BaseException as error:
        record["error"] = str(error)
        raise
    finally:
        record["duration_s"] = (time.monotonic_ns() - began) / 1e9
        (out / "freeze-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"out": str(out), "valid": True, "files": len(identities), "bytes": sum(value["size"] for value in identities)}))


if __name__ == "__main__":
    main()
