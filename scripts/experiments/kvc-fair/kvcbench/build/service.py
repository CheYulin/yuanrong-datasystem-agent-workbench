"""Fresh source builds with real per-product records and persistent acceptance."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys

from kvcbench.build import catalog, metadata
from kvcbench.build.bundle import install_package
from kvcbench.core.steps import Check, identifier
from kvcbench.platform.cgroup import drain
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, digest, read_json, reference, real_directory
from kvcbench.platform.locks import open_lock
from kvcbench.platform.receipts import Workflow, verify_commit


REQUIRED = {"schema_version", "root", "cache_root", "python_root", "cc", "cxx", "jobs",
            "cgroup", "cpus", "numa", "memory_bytes", "timeout_s"}


def validate_spec(spec):
    if set(spec) != REQUIRED or spec.get("schema_version") != 1:
        raise ValueError("Build specification fields/schema differ: " + repr(set(spec) ^ REQUIRED))
    for key in ["root", "cache_root", "python_root", "cc", "cxx", "cgroup"]:
        if not isinstance(spec[key], str) or not Path(spec[key]).is_absolute() or ".." in Path(spec[key]).parts:
            raise ValueError("Explicit absolute build path required: " + key)
    if type(spec["jobs"]) is not int or not 1 <= spec["jobs"] <= 64:
        raise ValueError("Build jobs must be an integer in 1..64")
    if (not isinstance(spec["cpus"], list) or any(type(x) is not int or x < 0 for x in spec["cpus"])
            or len(set(spec["cpus"])) != len(spec["cpus"]) or len(spec["cpus"]) != spec["jobs"]):
        raise ValueError("Build cpus must explicitly match the jobs budget")
    if type(spec["numa"]) is not int or spec["numa"] < 0:
        raise ValueError("Explicit NUMA node required")
    if type(spec["memory_bytes"]) is not int or spec["memory_bytes"] < 1 << 30:
        raise ValueError("Build memory budget must be at least 1 GiB")
    if type(spec["timeout_s"]) is not int or spec["timeout_s"] <= 0:
        raise ValueError("Positive command timeout required")
    identifier(Path(spec["root"]).name)
    return spec


class BuildResources:
    def __init__(self, spec):
        self.spec = spec
        self.root = Path(spec["cgroup"])
        if self.root.parent != Path("/sys/fs/cgroup") or not self.root.name.startswith("kvcbuild"):
            raise ValueError("Build cgroup must be a fresh kvcbuild domain directly under /sys/fs/cgroup")
        self.group = self.root / "build"
        self.owned = False

    def create(self):
        self.root.mkdir()
        self.owned = True
        (self.root / "cgroup.subtree_control").write_text("+cpu +cpuset +memory +io")
        (self.root / "memory.max").write_text(str(self.spec["memory_bytes"]))
        (self.root / "memory.swap.max").write_text("0")
        self.group.mkdir()
        for name, value in {"cpuset.mems": str(self.spec["numa"]),
                            "cpuset.cpus": ",".join(map(str, self.spec["cpus"])),
                            "cpu.max": f"{self.spec['jobs'] * 100000} 100000",
                            "memory.max": str(self.spec["memory_bytes"]), "memory.swap.max": "0"}.items():
            (self.group / name).write_text(value)
        observed = self.snapshot()
        effective = set()
        for part in observed["cpuset.cpus.effective"].split(','):
            bounds = part.split('-')
            effective.update(range(int(bounds[0]), int(bounds[-1]) + 1))
        if (effective != set(self.spec["cpus"]) or observed["cpu.max"] != f"{self.spec['jobs'] * 100000} 100000"
                or observed["memory.max"] != str(self.spec["memory_bytes"]) or observed["memory.swap.max"] != '0'):
            raise ValueError("Effective build resource limits differ from the frozen specification")
        return observed

    def snapshot(self):
        return {key: (self.group / key).read_text().strip() if (self.group / key).exists() else None
                for key in ["cpu.max", "cpuset.cpus.effective", "cpuset.mems.effective", "memory.max",
                            "memory.swap.max", "cpu.stat", "memory.events", "memory.peak", "io.stat", "cgroup.procs"]}

    def close(self):
        if not self.owned:
            return {"removed": False, "owned": False, "error": "Build domain was not created by this controller"}
        if not self.group.exists():
            if (self.root / "cgroup.procs").read_text().strip():
                raise RuntimeError("Partially initialized build domain is populated")
            self.root.rmdir()
            return {"removed": True, "owned": True, "partial_initialization": True}
        observed = drain(self.root, roles=["build"])
        stats = self.snapshot()
        self.group.rmdir()
        self.root.rmdir()
        return {"drained": observed, "resources": stats, "removed": True}


def prepare(spec, tool):
    validate_spec(spec)
    root = Path(spec["root"])
    if root.exists() or root.is_symlink():
        raise FileExistsError("Source build root must be fresh")
    for key in ["cc", "cxx"]:
        if not Path(spec[key]).is_file() or not os.access(spec[key], os.X_OK):
            raise ValueError("Compiler unavailable: " + key)
    python = real_directory(spec["python_root"]) / "bin/python3.11"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Shared CPython 3.11 toolchain unavailable")
    root.mkdir(parents=True)
    install_package(tool, root)
    for product, pin in metadata.PRODUCTS.items():
        source = Path(tool) / pin["recipe_source"]
        target = root / pin["script"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        for name in ["requirements.lock", "requirements-installed.lock", "os-packages.lock"]:
            lock = source.with_name(name)
            if lock.is_file():
                destination = root / product / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(lock, destination)
    atomic_json(root / "inputs/build-spec.json", spec, exclusive=True)
    return root


def build(spec, *, tool=None, resources_factory=BuildResources, command_runner=execute):
    tool = Path(tool or Path(__file__).resolve().parents[2])
    root = prepare(spec, tool)
    workflow = Workflow.create(root / "workflow", root.name, "source-build-kvc-20261020", root / "inputs/build-spec.json")
    fixed_inputs = [root / "configs/versions/kvc_20261020.json"]
    fixed_inputs += sorted(path for path in (root / "kvcbench").rglob("*")
                           if path.is_file() and "__pycache__" not in path.parts)
    plan = workflow.begin("W01", inputs=fixed_inputs)
    plan.require_ready()
    plan_commit = plan.finish(execution="completed", returncode=0, scope="fixed source-build specification",
        checks=[Check("three_pinned_products", 3, len(metadata.PRODUCTS), len(metadata.PRODUCTS) == 3)],
        artifacts=[reference(root, "inputs/build-spec.json"), reference(root, "configs/versions/kvc_20261020.json")],
        validator_id="build.spec.v1", validator_sources=[Path(__file__)])
    resources = resources_factory(spec)
    results = {}
    cleanup = None
    fatal = None
    with open_lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            initial = resources.create()
            atomic_json(root / "build-resources.json", initial, exclusive=True)
            env = dict(os.environ, TASK_ROOT=str(root), MOONCAKE_ROOT=str(root / "mooncake"),
                       LMCACHE_ROOT=str(root / "lmcache"), CACHE_ROOT=spec["cache_root"], DOWNLOADS=spec["cache_root"],
                       PYTHON_ROOT=spec["python_root"], CC=spec["cc"], CXX=spec["cxx"],
                       BUILD_JOBS=str(spec["jobs"]), MAX_JOBS=str(spec["jobs"]), PYTHONPATH=str(root),
                       PYTHONDONTWRITEBYTECODE="1")
            env["PATH"] = str(Path(spec["cc"]).parent) + ":" + env.get("PATH", "")
            for product in ["mooncake", "datasystem", "lmcache"]:
                attempt = workflow.begin("W02." + product, upstream=[plan_commit], inputs=[root / metadata.PRODUCTS[product]["script"]])
                attempt.require_ready()
                folder = root / "commands" / product
                before = resources.snapshot()
                command = command_runner(["bash", str(root / metadata.PRODUCTS[product]["script"])],
                    cwd=root, env=env, timeout=spec["timeout_s"], group=resources.group,
                    log=folder / "build.log", record=folder / "build-command.json")
                meta = command_runner([sys.executable, "-m", "kvcbench.build.metadata", "record", "--root", str(root),
                    "--product", product, "--started-wall-ns", str(command["start_wall_ns"]),
                    "--finished-wall-ns", str(command["end_wall_ns"]), "--build-exit", str(command["returncode"] if command["returncode"] is not None else -1),
                    "--compiler", spec["cxx"]], cwd=root, env=env, timeout=120, group=resources.group,
                    log=folder / "metadata.log", record=folder / "metadata-command.json")
                drain(resources.root, roles=["build"])
                row_path = root / "build-metadata" / (product + ".json")
                row = read_json(row_path) if row_path.is_file() else {}
                valid = command["returncode"] == 0 and command["error"] is None and meta["returncode"] == 0 and row.get("identity_valid") is True
                artifact_records = {}
                verification_error = None
                if valid:
                    try:
                        _, artifact_records, proof = catalog.validate_builds(root, products=[product])
                        atomic_json(folder / "artifact-verification.json", proof, exclusive=True)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        verification_error, valid = str(exc), False
                atomic_json(folder / "resources.json", {"before": before, "after": resources.snapshot(),
                    "peak_scope": "cumulative build-domain peak, not isolated product peak"}, exclusive=True)
                payloads = [reference(root, p.relative_to(root)) for p in sorted(folder.glob("*")) if p.is_file()]
                if row_path.is_file():
                    payloads.append(reference(root, row_path.relative_to(root)))
                payloads.extend(reference(root, Path(name).relative_to(root)) for name in artifact_records)
                commit = attempt.finish(execution="completed" if valid else "failed", returncode=0 if valid else 1,
                    scope="actual source recipe command and recorded native build identity",
                    checks=[Check("recipe_exit", 0, command["returncode"], command["returncode"] == 0),
                            Check("metadata_identity", True, row.get("identity_valid"), row.get("identity_valid") is True),
                            Check("native_artifacts", "exact pinned native outputs", verification_error or len(artifact_records),
                                  bool(artifact_records) and verification_error is None)],
                    artifacts=payloads, validator_id="build.product.v1", validator_sources=[Path(__file__), Path(metadata.__file__)],
                    details={"product": product, "reused": False, "executed_build": True})
                results[product] = {"valid": valid, "commit": str(commit), "build_exit": command["returncode"]}
            if all(row["valid"] for row in results.values()):
                records, artifacts, provenance = catalog.validate_builds(root)
                atomic_json(root / "build-catalog.json", {"schema_version": 1, "records": records,
                    "artifacts": artifacts, "provenance": provenance, "reused": False}, exclusive=True)
        except Exception as exc:
            fatal = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            try:
                cleanup = resources.close()
            except (OSError, ValueError, RuntimeError) as exc:
                cleanup = {"removed": False, "error": str(exc)}
            atomic_json(root / "build-cleanup.json", cleanup, exclusive=True)
    result = {"products": results, "fatal": fatal, "valid": fatal is None and len(results) == 3 and all(r["valid"] for r in results.values())
              and (root / "build-catalog.json").is_file() and cleanup.get("removed") is True,
              "cleanup": cleanup, "root": str(root), "workflow": str(workflow.root)}
    atomic_json(root / "build-result.json", result, exclusive=True)
    aggregate = workflow.begin("W02", upstream=[r["commit"] for r in results.values()],
                               purpose="normal" if result["valid"] else "diagnostic")
    aggregate.finish(execution="completed" if result["valid"] else "failed", returncode=0 if result["valid"] else 1,
        scope="all source-build products, native catalog and build-domain cleanup",
        checks=[Check("three_products", 3, len(results), len(results) == 3),
                Check("native_build_catalog", True, (root / "build-catalog.json").is_file(), (root / "build-catalog.json").is_file()),
                Check("resource_cleanup", True, cleanup.get("removed"), cleanup.get("removed") is True),
                Check("all_product_builds", True, result["valid"], result["valid"])],
        artifacts=[reference(root, "build-result.json"), reference(root, "build-cleanup.json")],
        validator_id="build.aggregate.v1", validator_sources=[Path(__file__), Path(catalog.__file__)])
    return result
