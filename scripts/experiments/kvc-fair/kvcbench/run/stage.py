#!/usr/bin/env python3
"""Make fresh batch inputs, reusing verified pinned builds through read-only links."""
import fcntl
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from kvcbench.platform.files import digest
from kvcbench.platform.cgroup import drain
from kvcbench.platform.files import regular, identity, real_directory
from kvcbench.core.spec import PRODUCT_NAMES
from kvcbench.platform.locks import open_lock, open_path


NATIVE_TEMPLATE_ROOT = "/home/worktrees/kvc-fair-20261020"


def tcp_manifest(bundle, root):
    source = regular(bundle, "products.json")
    manifest = json.loads(source.read_text().replace(NATIVE_TEMPLATE_ROOT, str(root)))
    if (not isinstance(manifest.get("products"), list) or len(manifest["products"]) != 3
            or {row.get("name") for row in manifest["products"]} != set(PRODUCT_NAMES)):
        raise ValueError("The canonical manifest must name the three pinned products exactly once")
    for product in manifest["products"]:
        name = product["name"]
        if any(not str(product[key]).startswith(str(root) + "/") for key in ["python", "adapter", "config"]):
            raise ValueError("Manifest executable/adapter/config escaped the fresh stage")
        product["env"]["PYTHONDONTWRITEBYTECODE"] = "1"
        if name == "datasystem":
            if "--transport" in product["start"]:
                raise ValueError("The native manifest template already contains a transport override")
            product["start"] += ["--transport", "tcp"]
        elif name == "lmcache":
            product["adapter"] = str(root / "lmcache/adapter_lmcache_tcp.py")
        else:
            product["env"].update(MC_STORE_MEMCPY="0", MC_STORE_LOCAL_HOT_CACHE_SIZE="0")
    return manifest, identity(source)




def copy_datasystem_runtime(bundle, temporary):
    source = bundle / "datasystem/runtime.py"
    if source.is_symlink() or not source.is_file():
        raise ValueError("An explicit DataSystem runtime with per-worker RocksDB isolation is required")
    target = temporary / "scripts/datasystem/runtime.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": "scripts/datasystem/runtime.py", "sha256": digest(target), "mode": target.stat().st_mode & 0o777,
            "scope": "Deployment script replacement only; native build bytes unchanged. Each worker must use its own fresh RocksDB path."}


def stage(args, *, verify_builds=None, snapshot_builds=None, install_inputs=None, drain_cgroups=drain):
    builds_only = getattr(args, "builds_only_native_root", None)
    base, root, bundle = (builds_only or args.base).resolve(), args.root.resolve(strict=False), args.bundle.resolve()
    if root.exists() or args.root.is_symlink() or base in root.parents or root in base.parents:
        raise ValueError("Use a fresh sibling batch root")
    if builds_only:
        if getattr(args, "feature_profile", None) != "pure-kv":
            raise ValueError("The builds-only branch requires the explicit pure-kv profile")
        if verify_builds is None or snapshot_builds is None:
            raise ValueError("Explicit validated build providers are required")
        real_directory(builds_only)
        lock_stream = open_lock(base)
    else:
        lock_stream = (base / "suite.lock").open("a")
    with ExitStack() as stack:
        lock = stack.enter_context(lock_stream)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        resource_path = Path(getattr(args, "resource_lock", None) or base / "suite.lock").absolute()
        resource_lock = stack.enter_context(open_path(resource_path))
        resource_stat, build_stat = os.fstat(resource_lock.fileno()), os.fstat(lock.fileno())
        if (resource_stat.st_dev, resource_stat.st_ino) != (build_stat.st_dev, build_stat.st_ino):
            fcntl.flock(resource_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        drain_cgroups(args.cgroup)
        if builds_only:
            records, artifacts, provenance = verify_builds(builds_only)
        else:
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
            if install_inputs is not None:
                install_inputs(bundle, temporary)
            elif (bundle / "kvcbench").exists():
                raise ValueError("Explicit frozen code input installer is required")
            for product, destination in [("datasystem", "scripts/datasystem"), ("mooncake", "mooncake"), ("lmcache", "lmcache")]:
                target = temporary / destination
                target.mkdir(parents=True)
                for path in (bundle / product if builds_only else base / destination).iterdir():
                    if path.is_file() and not path.is_symlink() and path.suffix in {".py", ".sh", ".json", ".lock", ".md"}:
                        shutil.copy2(path, target / path.name)
                        if path.suffix == ".sh":
                            (target / path.name).chmod(path.stat().st_mode & 0o777 | 0o111)
            deployment_runtime = copy_datasystem_runtime(bundle, temporary)
            feature_profile = getattr(args, "feature_profile", "baseline")
            overrides = [deployment_runtime]
            if feature_profile == "pure-kv":
                for name, target_name in [("mooncake/launch_master.sh", "mooncake/launch_master.sh"),
                                          ("lmcache/start_server_tcp.sh", "lmcache/start_server.sh")]:
                    canonical = bundle / "kvcbench/run/products" / name
                    source, target = canonical if canonical.is_file() else bundle / name, temporary / target_name
                    if not source.is_file() or source.is_symlink():
                        raise ValueError("Pure-KV runtime override is missing: " + name)
                    shutil.copy2(source, target)
                    target.chmod(target.stat().st_mode & 0o777 | 0o111)
                    overrides.append({"path": target_name, "sha256": digest(target), "mode": target.stat().st_mode & 0o777,
                                      "scope": "Explicit source-pinned pure-memory feature profile; native build unchanged."})
            for name in ["datasystem", "mooncake/source", "mooncake/deps", "mooncake/venv", "mooncake/wheels", "lmcache/source", "lmcache/venv311"]:
                (temporary / name).symlink_to((base / name).resolve(strict=True), target_is_directory=True)
            os.link(resource_path, temporary / "suite.lock", follow_symlinks=False)
            linked = (temporary / "suite.lock").stat()
            held = os.fstat(resource_lock.fileno())
            if (linked.st_dev, linked.st_ino) != (held.st_dev, held.st_ino):
                raise RuntimeError("The shared suite lock changed while staging")
            (temporary / "configs").mkdir(exist_ok=True)
            if builds_only:
                manifest, manifest_template = tcp_manifest(bundle, root)
            else:
                manifest = json.loads((base / "products.json").read_text().replace(str(base), str(root)))
            for product in manifest["products"]:
                if feature_profile == "pure-kv":
                    product["env"]["KVC_FEATURE_PROFILE"] = feature_profile
                    if product["name"] == "datasystem":
                        product["start"] += ["--profile", feature_profile]
                        product["env"].update(DATASYSTEM_LOG_MONITOR_ENABLE="false", DATASYSTEM_PERF_TRACE_LOG="false",
                                              DATASYSTEM_LOG_V="0", DATASYSTEM_MIN_LOG_LEVEL="2")
                    elif product["name"] == "mooncake":
                        product["env"].update(MC_STORE_CLIENT_METRIC="0", MC_STORE_CLIENT_METRIC_INTERVAL="0", MC_TE_METRIC="0",
                                              MC_RPC_PROTOCOL="tcp")
                    else:
                        product["env"]["LMCACHE_COORDINATOR_EVENT_REPORTING"] = "false"
                if builds_only:
                    config = {"transport": "tcp"}
                    if product["name"] == "datasystem":
                        config["endpoints"] = ["127.0.0.1:31531", "127.0.0.1:31532"]
                    elif product["name"] == "lmcache":
                        config["endpoints"] = ["tcp://127.0.0.1:15550", "tcp://127.0.0.1:15551"]
                else:
                    config = json.loads((base / "configs" / (product["name"] + ".json")).read_text())
                config.update(scalar_adapter=product["adapter"], value_size=1024,
                              preload_batch=1 if product["name"] == "lmcache" else 256,
                              get_check_batch=1 if product["name"] == "lmcache" else 4096)
                product["adapter"] = str(root / ("batch_" + product["name"] + ".py"))
                if product["name"] == "mooncake" and getattr(args, "mooncake_queue", 0):
                    for name in ["MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER", "MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER"]:
                        product["env"][name] = str(args.mooncake_queue)
                if product["name"] == "mooncake":
                    product["env"]["MC_TCP_LANES_PER_PEER"] = "4"
                    product["env"]["MC_TCP_ADMISSION_TIMEOUT_MS"] = "1000"
                (temporary / "configs" / (product["name"] + ".json")).write_text(json.dumps(config, indent=2) + "\n")
            (temporary / "products.json").write_text(json.dumps(manifest, indent=2) + "\n")
            if builds_only:
                builds_provenance = snapshot_builds(base, temporary, records, provenance)
            else:
                shutil.copytree(base / "formal-tcp-001/builds", temporary / "builds")
            inputs = {str(p.relative_to(temporary)): {"sha256": digest(p), "bytes": p.stat().st_size, "mode": p.stat().st_mode & 0o777}
                      for p in temporary.rglob("*") if p.is_file() and not p.is_symlink()
                      and not any(parent.is_symlink() for parent in p.parents if parent != temporary.parent)
                      and p.name != "suite.lock"}
            record = {"profile": "native-batch-tcp", "created_wall_ns": time.time_ns(), "root": str(root),
                      "base": str(base), "builds_reused": True, "build_artifacts": artifacts,
                      "datasystem_metadata_backend": "coordinator",
                      "shared_suite_lock_inode": held.st_ino, "shared_suite_lock_device": held.st_dev,
                      "resource_lock_path": str(resource_path), "cgroup": str(args.cgroup),
                      "mooncake_queue_capacity": getattr(args, "mooncake_queue", 0),
                      "datasystem_runtime_override": deployment_runtime,
                      "feature_profile": feature_profile, "runtime_overrides": overrides,
                      "baseline_performance_reused": False,
                      "baseline_isolation_limitation": "Earlier DataSystem deployments used a shared default RocksDB directory; reuse is limited to verified native build artifacts, not performance baselines.",
                      "inputs": inputs, "old_experiment_inputs_modified": False}
            if builds_only:
                record.update(source_build_mode="builds-only-native-root", legacy_suites_required=False,
                              builds_provenance=builds_provenance, manifest_template=manifest_template,
                              baseline_isolation_limitation="Legacy performance suites were not required or synthesized. Only identified fixed source build artifacts are reused; this stage still requires its own native batch gate, smoke and TCP verification.")
            (temporary / "batch-stage.json").write_text(json.dumps(record, indent=2) + "\n")
            temporary.rename(root)
            return record
        except BaseException:
            shutil.rmtree(temporary)
            raise
