#!/usr/bin/env python3
"""Stage a separate TCP experiment while preserving the native-path run inputs."""
import argparse
import functools
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from kvcbench.platform.cgroup import drain
from kvcbench.build.bundle import install_package


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            value.update(block)
    return value.hexdigest()


def exclusive(root_of):
    def decorate(function):
        @functools.wraps(function)
        def locked(args):
            with (root_of(args) / "suite.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return function(args)
        return locked
    return decorate


@exclusive(lambda args: args.base.resolve())
def stage(args):
    if args.root.is_symlink():
        raise RuntimeError("TCP root cannot be a symlink")
    base, root, bundle = [p.resolve() for p in [args.base, args.root, args.bundle]]
    if root.exists():
        raise RuntimeError("Use a fresh TCP root; existing experiment inputs are never replaced")
    if root == base or base in root.parents or root in base.parents:
        raise ValueError("TCP and native roots must be separate siblings")
    drain(args.cgroup)
    native = base / "formal-001"
    summary = json.loads((native / "summary.json").read_text())
    if not summary.get("ended") or summary.get("failed") or summary.get("fatal"):
        raise RuntimeError("Original native suite must have ended successfully")
    manifest = json.loads((bundle / "products.json").read_text())
    if len(manifest["products"]) != 3 or {p["name"] for p in manifest["products"]} != {"datasystem", "mooncake", "lmcache"}:
        raise ValueError("Exactly the three requested products are required")
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="." + root.name + "-", dir=root.parent))
    try:
        for path in sorted(bundle.glob("*")):
            if path.is_file() and path.suffix == ".py":
                shutil.copy2(path, temporary / path.name)
        install_package(bundle, temporary)
        for product, target in [("datasystem", temporary / "scripts/datasystem"),
                                ("mooncake", temporary / "mooncake"), ("lmcache", temporary / "lmcache")]:
            target.mkdir(parents=True)
            for path in sorted((bundle / product).glob("*")):
                if path.is_file() and path.suffix in {".py", ".sh", ".json", ".lock", ".md"}:
                    shutil.copy2(path, target / path.name)
                    if path.suffix == ".sh":
                        (target / path.name).chmod(path.stat().st_mode & 0o777 | 0o111)
        links = {"datasystem": "datasystem", "mooncake/source": "mooncake/source",
                 "mooncake/deps": "mooncake/deps", "mooncake/venv": "mooncake/venv",
                 "mooncake/wheels": "mooncake/wheels",
                 "lmcache/source": "lmcache/source", "lmcache/venv311": "lmcache/venv311",
                 }
        for product in ["mooncake", "lmcache"]:
            evidence = product + "/.evidence"
            if (base / evidence).is_dir():
                links[evidence] = evidence
        for destination, original in links.items():
            source = base / original
            if not source.is_dir():
                raise FileNotFoundError("Original built dependency missing: " + str(source))
            (temporary / destination).symlink_to(source, target_is_directory=True)
        # A hard link makes both roots use the same flock without a symlink API.
        os.link(base / "suite.lock", temporary / "suite.lock")
        shutil.copy2(temporary / "lmcache/start_server_tcp.sh", temporary / "lmcache/start_server.sh")
        (temporary / "configs").mkdir(exist_ok=True)
        for product in manifest["products"]:
            name = product["name"]
            for key in ["python", "adapter", "config"]:
                product[key] = product[key].replace(str(base), str(root))
            for key in ["start", "stop"]:
                product[key] = [token.replace(str(base), str(root)) for token in product[key]]
            product["env"] = {key: value.replace(str(base), str(root)) for key, value in product["env"].items()}
            product["env"]["PYTHONDONTWRITEBYTECODE"] = "1"
            if name == "datasystem":
                product["start"] += ["--transport", "tcp"]
            elif name == "lmcache":
                product["adapter"] = str(root / "lmcache/adapter_lmcache_tcp.py")
            else:
                product["env"]["MC_STORE_MEMCPY"] = "0"
                product["env"]["MC_STORE_LOCAL_HOT_CACHE_SIZE"] = "0"
            config = {"transport": "tcp"}
            if name == "datasystem":
                config["endpoints"] = ["127.0.0.1:31531", "127.0.0.1:31532"]
            elif name == "lmcache":
                config["endpoints"] = ["tcp://127.0.0.1:15550", "tcp://127.0.0.1:15551"]
            (temporary / "configs" / (name + ".json")).write_text(json.dumps(config) + "\n")
        (temporary / "products.json").write_text(json.dumps(manifest, indent=2) + "\n")
        builds = temporary / "formal-tcp-001/builds"
        shutil.copytree(native / "builds", builds)
        original_evidence = native / "build-and-script-evidence"
        if original_evidence.is_dir():
            shutil.copytree(original_evidence, temporary / "formal-tcp-001/original-build-evidence")
        artifacts = {}
        for metadata in sorted(builds.glob("*.json")):
            name = metadata.stem
            rows = json.loads(metadata.read_text()).get("artifact_hashes", {})
            pairs = rows.items() if isinstance(rows, dict) else [(row["path"], row["sha256"]) for row in rows]
            for relative, expected in pairs:
                relative = Path(relative)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Native artifact metadata must use a relative path")
                path = base / relative if relative.parts[0] == name else base / name / relative
                if path.name == "datasystem_worker" or path.suffix in {".whl", ".gz"} or ".so" in path.name:
                    observed = digest(path)
                    if observed != expected:
                        raise RuntimeError("Original native artifact changed after its build: " + str(path))
                    artifacts[str(path)] = {"sha256": observed, "bytes": path.stat().st_size}
        inputs = {str(p.relative_to(temporary)): {"sha256": digest(p), "bytes": p.stat().st_size}
                  for p in temporary.rglob("*") if p.is_file() and not p.is_symlink()
                  and not any(parent.is_symlink() for parent in p.parents if parent != temporary.parent)
                  and p.name != "suite.lock"}
        record = {"profile": "tcp", "base": str(base), "root": str(root), "source_builds_reused": True,
                  "build_evidence": str(native / "builds"), "artifact_links": links, "inputs": inputs,
                  "native_inputs_modified": False, "shared_suite_lock_inode": (base / "suite.lock").stat().st_ino,
                  "native_artifacts_revalidated": artifacts,
                  "scope": "Separate runtime/scripts/configs and empty TCP result directory; pinned native binaries reused without rebuild. Native results and scripts are preserved."}
        encoded = json.dumps(record, indent=2) + "\n"
        (temporary / "tcp-stage.json").write_text(encoded)
        (temporary / "formal-tcp-001/tcp-stage.json").write_text(encoded)
        temporary.rename(root)
        return record
    except BaseException:
        shutil.rmtree(temporary)
        raise


@exclusive(lambda args: args.root.resolve(strict=True))
def prepare(args):
    root = args.root.resolve(strict=True)
    drain(args.cgroup)
    environment = args.prepare_environment.resolve()
    if (environment / "environment-before.json").exists() or (environment / "environment-cleanup.json").exists():
        raise FileExistsError("Preserve the original preparation snapshot; use a fresh environment directory")
    with (root / "preparation-started.json").open("x") as stream:
        json.dump({"start_wall_ns": time.time_ns(), "environment": str(environment),
                   "scope": "Marker precedes all environment preparation side effects; retain even if preparation fails."}, stream)
    subprocess.run([sys.executable, str(root / "prepare_env.py"), "--output", str(environment), "--apply"], check=True)
    settings = subprocess.check_output([sys.executable, str(root / "cgroup.py"), "setup"], text=True)
    (root / "cgroup-settings.json").write_text(settings)
    return {"environment": str(environment), "shared_lock_held": True, "settings": json.loads(settings)}


@exclusive(lambda args: args.root.resolve(strict=True))
def retire_unstarted(args):
    root = args.root.resolve(strict=True)
    record = json.loads((root / "tcp-stage.json").read_text())
    if record.get("root") != str(root) or record.get("profile") != "tcp":
        raise RuntimeError("The directory is not this TCP stage")
    drain(args.cgroup)
    if any((root / name).exists() for name in ["run", "runtime", "protocol-gate-001", "orchestration-tcp-smoke", "formal-tcp-001/events.jsonl", "formal-tcp-001/summary.json", "cgroup-settings.json", "preparation-started.json"]):
        raise RuntimeError("This stage has already been prepared or executed; preserve it in place")
    for name, row in record["inputs"].items():
        if digest(root / name) != row["sha256"]:
            raise RuntimeError("Unstarted stage input changed: " + name)
    destination = root.with_name(root.name + "-unstarted-" + str(time.time_ns()))
    root.rename(destination)
    return {"retired_unstarted_stage": str(destination), "source": str(root), "deleted": False,
            "reason": "Preserved before corrected staging; no environment preparation or service execution occurred"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path("/home/worktrees/kvc-fair-20261020"))
    parser.add_argument("--root", type=Path, default=Path("/home/worktrees/kvc-fair-tcp-20261020"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--prepare-environment", type=Path)
    parser.add_argument("--retire-unstarted", action="store_true")
    parser.add_argument("--cgroup", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    args = parser.parse_args()
    if not args.prepare_environment and not args.retire_unstarted and args.bundle is None:
        parser.error("--bundle is required for staging")
    print(json.dumps(retire_unstarted(args) if args.retire_unstarted else prepare(args) if args.prepare_environment else stage(args), indent=2))


if __name__ == "__main__":
    main()
