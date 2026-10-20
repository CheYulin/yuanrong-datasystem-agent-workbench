#!/usr/bin/env python3
"""Make fresh batch inputs, reusing verified pinned builds through read-only links."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from profile_put import digest, drain


def stage(args):
    base, root, bundle = args.base.resolve(), args.root.absolute(), args.bundle.resolve()
    if root.exists() or root.is_symlink() or base in root.parents or root in base.parents:
        raise ValueError("Use a fresh sibling batch root")
    with (base / "suite.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        drain(args.cgroup)
        summary = json.loads((base / "formal-tcp-001/summary.json").read_text())
        old_plan = json.loads((base / "formal-tcp-001/plan.json").read_text())
        expected = {f"r{row['repeat']}-{row['product']}-s{row['size']}-c{row['concurrency']}" for row in old_plan}
        completed = summary.get("completed", [])
        if (not summary.get("ended") or summary.get("fatal") or summary.get("failed")
                or summary.get("valid") is False or len(expected) != 81 or len(old_plan) != 81
                or len(completed) != 81 or set(completed) != expected):
            raise RuntimeError("Pinned single-key TCP baseline must be terminal and successful")
        prior = json.loads((base / "tcp-stage.json").read_text())
        artifacts = prior["native_artifacts_revalidated"]
        for path, record in artifacts.items():
            if digest(Path(path)) != record["sha256"]:
                raise RuntimeError("Pinned build artifact changed: " + path)
        temporary = Path(tempfile.mkdtemp(prefix="." + root.name + "-", dir=root.parent))
        try:
            for path in bundle.glob("*.py"):
                shutil.copy2(path, temporary / path.name)
            for product, destination in [("datasystem", "scripts/datasystem"), ("mooncake", "mooncake"), ("lmcache", "lmcache")]:
                target = temporary / destination
                target.mkdir(parents=True)
                for path in (base / destination).iterdir():
                    if path.is_file() and not path.is_symlink() and path.suffix in {".py", ".sh", ".json", ".lock", ".md"}:
                        shutil.copy2(path, target / path.name)
                        if path.suffix == ".sh":
                            (target / path.name).chmod(path.stat().st_mode & 0o777 | 0o111)
            for name in ["datasystem", "mooncake/source", "mooncake/deps", "mooncake/venv", "mooncake/wheels", "lmcache/source", "lmcache/venv311"]:
                (temporary / name).symlink_to((base / name).resolve(strict=True), target_is_directory=True)
            os.link(base / "suite.lock", temporary / "suite.lock")
            (temporary / "configs").mkdir()
            manifest = json.loads((base / "products.json").read_text().replace(str(base), str(root)))
            for product in manifest["products"]:
                config = json.loads((base / "configs" / (product["name"] + ".json")).read_text())
                config.update(scalar_adapter=product["adapter"], value_size=1024,
                              preload_batch=1 if product["name"] == "lmcache" else 256,
                              get_check_batch=1 if product["name"] == "lmcache" else 4096)
                product["adapter"] = str(root / ("batch_" + product["name"] + ".py"))
                (temporary / "configs" / (product["name"] + ".json")).write_text(json.dumps(config, indent=2) + "\n")
            (temporary / "products.json").write_text(json.dumps(manifest, indent=2) + "\n")
            shutil.copytree(base / "formal-tcp-001/builds", temporary / "builds")
            inputs = {str(p.relative_to(temporary)): {"sha256": digest(p), "bytes": p.stat().st_size}
                      for p in temporary.rglob("*") if p.is_file() and not p.is_symlink()
                      and not any(parent.is_symlink() for parent in p.parents if parent != temporary.parent)
                      and p.name != "suite.lock"}
            record = {"profile": "native-batch-tcp", "created_wall_ns": time.time_ns(), "root": str(root),
                      "base": str(base), "builds_reused": True, "build_artifacts": artifacts,
                      "shared_suite_lock_inode": (base / "suite.lock").stat().st_ino,
                      "inputs": inputs, "old_experiment_inputs_modified": False}
            (temporary / "batch-stage.json").write_text(json.dumps(record, indent=2) + "\n")
            temporary.rename(root)
            return record
        except BaseException:
            shutil.rmtree(temporary)
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, default=Path("/home/worktrees/kvc-fair-tcp-20261020"))
    p.add_argument("--root", type=Path, default=Path("/home/worktrees/kvc-batch-tcp-20261020"))
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--cgroup", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    args = p.parse_args()
    record = stage(args)
    print(json.dumps({"root": record["root"], "inputs": len(record["inputs"]), "build_artifacts": len(record["build_artifacts"])}))


if __name__ == "__main__":
    main()
