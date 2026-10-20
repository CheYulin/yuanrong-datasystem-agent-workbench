#!/usr/bin/env python3
"""Record fresh reproduction builds without replacing original experiment evidence.

record measures the caller's complete build-command wall interval, including
downloads, binary dependencies, configure, native compilation, installation and
verification. It is never reported as isolated compiler time. DataSystem's own
C++ worker/SDK, Mooncake's own C++ wheel and LMCache's three common C++ modules
come from the pinned source recipes. ETCD, OS packages and Python dependency
wheels can be binaries; LMCache's CPU PyTorch is a binary dependency, not a source
build of PyTorch. Metadata checks source identity and artifact bytes; it does not
independently prove how an artifact was compiled.

No command builds software, starts services, changes cgroups or runs benchmarks.
smoke validates an already-finished run_suite.py --smoke run for all products.
copy refuses to replace differing suite/builds files, preserving original runs.
"""
import argparse
import datetime
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


PRODUCTS = json.loads((Path(__file__).resolve().parents[2] / "configs/versions/kvc_20261020.json").read_text())["products"]


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            result.update(chunk)
    return result.hexdigest()


def read_json(path):
    with Path(path).open() as stream:
        return json.load(stream)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".tmp-", delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def command_text(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=15,
                                   env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).strip()


def git_status(source, tracked_only=False):
    command = ["git", "-C", str(source), "status", "--porcelain=v1",
               "--untracked-files=no" if tracked_only else "--untracked-files=normal"]
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=15,
                                   env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).rstrip("\n")


def source_state(product, status, tracked_status):
    lines = status.splitlines() if isinstance(status, str) else []
    untracked = [line for line in lines if line.startswith("?? ")]
    allowed = [line for line in untracked if product == "datasystem" and line == "?? .venv/"]
    unexpected = [line for line in untracked if line not in allowed]
    tracked_clean = tracked_status == "" and not any(not line.startswith("?? ") for line in lines)
    return {"source_worktree_clean": status == "", "source_status_porcelain": status,
            "source_tracked_worktree_clean": tracked_clean, "source_tracked_status_porcelain": tracked_status,
            "source_untracked_status_porcelain": "\n".join(untracked) if isinstance(status, str) else None,
            "source_allowed_untracked": allowed, "source_unexpected_untracked": unexpected,
            "source_worktree_policy_valid": isinstance(status, str) and tracked_clean and not unexpected,
            "source_worktree_policy": "Tracked source must be clean; only DataSystem's recipe-created untracked .venv/ directory is allowed. Full worktree cleanliness remains separately recorded."}


def utc_ns(value):
    whole, ns = divmod(value, 1_000_000_000)
    return datetime.datetime.fromtimestamp(whole, datetime.timezone.utc).replace(microsecond=ns // 1000).isoformat()


def compiler_probe(root, source, override=None):
    if override:
        compiler, evidence = override, "explicit --compiler"
    else:
        candidates = sorted((source / "build").glob("**/CMakeCache.txt"))
        configured = []
        for cache in candidates:
            for line in cache.read_text(errors="replace").splitlines():
                if line.startswith(("CMAKE_CXX_COMPILER:FILEPATH=", "CMAKE_CXX_COMPILER:STRING=")):
                    configured.append((line.split("=", 1)[1], str(cache.relative_to(root))))
        if configured:
            compiler, evidence = configured[0]
        else:
            compiler = os.environ.get("CXX", "g++")
            evidence = "current CXX/default compiler probe; no saved CMake compiler identity"
    return {"path": shutil.which(compiler) or compiler, "version": command_text([compiler, "--version"]).splitlines()[0], "evidence": evidence}


def record(args):
    if args.started_wall_ns < 0 or args.finished_wall_ns < args.started_wall_ns:
        raise ValueError("build wall timestamps must be nonnegative and ordered")
    root = args.root.resolve()
    spec = PRODUCTS[args.product]
    source = (args.source or root / spec["source"]).resolve()
    python = str(args.python or root / spec["python"])
    errors = []

    def observe(label, call):
        try:
            return call()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"{label}: {exc}")
            return None

    head = observe("git HEAD", lambda: command_text(["git", "-C", str(source), "rev-parse", "HEAD"]))
    tag_sha = observe("git tag", lambda: command_text(["git", "-C", str(source), "rev-parse", spec["tag"] + "^{commit}"]))
    status = observe("git status", lambda: git_status(source))
    tracked_status = observe("git tracked status", lambda: git_status(source, tracked_only=True))
    state = source_state(args.product, status, tracked_status)
    artifacts = sorted({path for pattern in spec["artifacts"] for path in root.glob(pattern) if path.is_file() and not path.is_symlink()})
    missing = [name for name in spec["native_required"] if not any(name in p.name for p in artifacts)]
    if head != spec["sha"] or tag_sha != spec["sha"]:
        errors.append("actual HEAD/tag does not match the experiment's official pinned SHA")
    if not state["source_tracked_worktree_clean"]:
        errors.append("tracked source working tree is not confirmed clean")
    if not state["source_worktree_policy_valid"]:
        errors.append("source status is unavailable or contains unapproved untracked paths: " + ", ".join(state["source_unexpected_untracked"]))
    if missing:
        errors.append("required native artifacts missing: " + ", ".join(missing))
    compiler = observe("compiler", lambda: compiler_probe(root, source, args.compiler))
    metadata = {
        "schema_version": 1, "product": args.product, "tag": spec["tag"], "pinned_sha": spec["sha"],
        "sha": head, "tag_sha": tag_sha, "official_source": spec["official_source"],
        "source_directory": str(source), **state,
        "python_executable": python, "python": observe("Python", lambda: command_text([python, "--version"])),
        "compiler": compiler["version"] if compiler else None, "compiler_probe": compiler,
        "build_started_wall_ns": args.started_wall_ns, "build_finished_wall_ns": args.finished_wall_ns,
        "build_started_utc": utc_ns(args.started_wall_ns), "build_finished_utc": utc_ns(args.finished_wall_ns),
        "build_wall_seconds": (args.finished_wall_ns - args.started_wall_ns) / 1e9,
        "timing_scope": "complete caller build-command wall interval including dependencies/downloads/configure/native compilation/install/verification; not isolated compiler time",
        "build_exit": args.build_exit, "smoke_exit": None,
        "build_recipe": spec["script"], "build_recipe_sha256": digest(root / spec["script"]) if (root / spec["script"]).is_file() else None,
        "artifact_hashes": {str(p.relative_to(root)): digest(p) for p in artifacts},
        "artifact_sizes": {str(p.relative_to(root)): p.stat().st_size for p in artifacts},
        "build_scope_notes": [spec["scope"], "Metadata checks pin/cleanliness/artifact identity; it does not independently prove compilation provenance or product correctness."],
    }
    if args.product == "lmcache":
        metadata["binary_dependency_torch_version"] = observe("PyTorch package metadata", lambda: command_text([python, "-c", "from importlib.metadata import version; print(version('torch'))"]))
    metadata["validation_errors"] = errors
    metadata["identity_valid"] = not errors
    output = args.output or root / "build-metadata" / (args.product + ".json")
    write_json(output, metadata)
    print(json.dumps({"output": str(output), "product": args.product, "identity_valid": not errors, "build_exit": args.build_exit}))
    return 1 if args.build_exit == 0 and errors else 0


def load_metadata(root):
    records = {}
    for name, spec in PRODUCTS.items():
        path = root / "build-metadata" / (name + ".json")
        row = read_json(path)
        if row.get("product") != name or row.get("tag") != spec["tag"] or row.get("sha") != spec["sha"]:
            raise ValueError("build metadata pin mismatch: " + str(path))
        tracked_status = row.get("source_tracked_status_porcelain", "" if row.get("source_worktree_clean") is True else None)
        state = source_state(name, row.get("source_status_porcelain"), tracked_status)
        if row.get("build_exit") != 0 or row.get("identity_valid") is not True or not state["source_worktree_policy_valid"] or not row.get("artifact_hashes"):
            raise ValueError("build metadata is not a successful identified build: " + str(path))
        records[name] = row
    return records


def validate_smoke(suite):
    summary = read_json(suite / "summary.json")
    params = read_json(suite / "parameters.json")
    products = read_json(suite / "products.json")
    if not summary.get("ended") or summary.get("failed") != [] or summary.get("fatal"):
        raise ValueError("smoke suite is unfinished, failed or fatal")
    if params.get("smoke") is not True:
        raise ValueError("suite must explicitly record run_suite.py --smoke")
    names = [p["name"] for p in products]
    if sorted(names) != sorted(PRODUCTS):
        raise ValueError("smoke suite must include each of the three products exactly once")
    sizes, conc = params["sizes"], params["concurrencies"]
    repeats = params["repeats"]
    if not sizes or not conc or repeats < 1 or min(sizes + conc) < 1 or len(set(sizes)) != len(sizes) or len(set(conc)) != len(conc):
        raise ValueError("invalid smoke matrix dimensions")
    expected = {(r, n, s, c) for r, n, s, c in itertools.product(range(repeats), PRODUCTS, sizes, conc)}
    plan = read_json(suite / "plan.json")
    planned = [(p["repeat"], p["product"], p["size"], p["concurrency"]) for p in plan]
    if len(planned) != len(expected) or set(planned) != expected:
        raise ValueError("smoke plan does not match all expected product/cell combinations")
    ids = {f"r{r}-{n}-s{s}-c{c}" for r, n, s, c in expected}
    completed = summary.get("completed", [])
    if len(completed) != len(ids) or set(completed) != ids:
        raise ValueError("smoke summary is missing cells or contains duplicates/unexpected cells")
    hashes = {name: digest(suite / name) for name in ("summary.json", "parameters.json", "products.json", "plan.json")}
    counts = {name: {"cells": 0, "results": 0, "operations": {}} for name in PRODUCTS}
    for repeat, name, size, concurrency in sorted(expected):
        cell = f"r{repeat}-{name}-s{size}-c{concurrency}"
        counts[name]["cells"] += 1
        operations = ["put", "get"] + (["exist_hit", "exist_miss"] if size == min(sizes) else [])
        for op in operations:
            relative = f"cells/{cell}/{op}.json"
            result = read_json(suite / relative)
            p = result.get("parameters", {})
            if any(p.get(k) != v for k, v in (("op", op), ("size", size), ("concurrency", concurrency))):
                raise ValueError("smoke result parameter mismatch: " + relative)
            success = result.get("success", 0)
            clients = result.get("clients", [])
            if result.get("valid") is not True or result.get("fatal") or result.get("failures") != 0 or success <= 0:
                raise ValueError("smoke result is invalid: " + relative)
            if len(clients) != concurrency or sum(c["success"] for c in clients) != success or sum(result.get("histogram", {}).values()) != success:
                raise ValueError("smoke result counters/histogram mismatch: " + relative)
            hashes[relative] = digest(suite / relative)
            counts[name]["results"] += 1
            counts[name]["operations"][op] = counts[name]["operations"].get(op, 0) + 1
    return {"suite": str(suite), "ended": summary["ended"], "expected_cells": len(expected), "expected_results": sum(c["results"] for c in counts.values()), "products": counts, "evidence_sha256": hashes}


def smoke(args):
    root, suite = args.root.resolve(), args.suite.resolve()
    records = load_metadata(root)
    evidence = validate_smoke(suite)
    for name, row in records.items():
        row.update(smoke_exit=0, smoke_case_counts=evidence["products"][name], smoke_evidence=evidence,
                   smoke_scope="finished common orchestration smoke only; not UT/ST, fault recovery, capacity or performance evidence")
    for name, row in records.items():
        write_json(root / "build-metadata" / (name + ".json"), row)
    print(json.dumps({"smoke_exit": 0, "cells": evidence["expected_cells"], "results": evidence["expected_results"]}))
    return 0


def copy(args):
    root, suite = args.root.resolve(), args.suite.resolve()
    records = load_metadata(root)
    destination = args.output or suite / "builds"
    for name, row in records.items():
        if row.get("smoke_exit") != 0 or not row.get("smoke_evidence") or not row.get("smoke_case_counts"):
            raise ValueError("validated all-product smoke metadata is required: " + name)
        target = destination / (name + ".json")
        if target.exists() and read_json(target) != row:
            raise FileExistsError("refusing to replace existing original build evidence: " + str(target))
    for name, row in records.items():
        target = destination / (name + ".json")
        if not target.exists():
            write_json(target, row)
    print(json.dumps({"copied": sorted(records), "output": str(destination)}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    build = commands.add_parser("record", help="record an already-executed complete build command")
    build.add_argument("--root", type=Path, required=True)
    build.add_argument("--product", choices=PRODUCTS, required=True)
    build.add_argument("--started-wall-ns", type=int, required=True)
    build.add_argument("--finished-wall-ns", type=int, required=True)
    build.add_argument("--build-exit", type=int, required=True)
    build.add_argument("--output", type=Path, help="JSON file override; useful for read-only source/artifact inspection")
    build.add_argument("--source", type=Path, help="read-only source directory override")
    build.add_argument("--python", type=Path, help="Python executable override")
    build.add_argument("--compiler", help="compiler executable override")
    build.set_defaults(func=record)
    check = commands.add_parser("smoke", help="validate all recorded products against a finished smoke suite")
    check.add_argument("--root", type=Path, required=True)
    check.add_argument("--suite", type=Path, required=True)
    check.set_defaults(func=smoke)
    transfer = commands.add_parser("copy", help="copy only validated metadata; never overwrite differing original evidence")
    transfer.add_argument("--root", type=Path, required=True)
    transfer.add_argument("--suite", type=Path, required=True)
    transfer.add_argument("--output", type=Path, help="destination directory override")
    transfer.set_defaults(func=copy)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"build_metadata: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
