"""Independently revalidate collected runtime inputs."""
import hashlib
import json
from pathlib import Path
import re

from kvcbench.platform.files import digest
from kvcbench.core.source_state import source_state


def verify_datasystem_isolation(gate_path):
    directory = Path(gate_path) / "r0-datasystem"
    configuration = json.loads((directory / "tcp-configuration.json").read_bytes())
    process_file = directory / "processes.json"
    if digest(process_file) != configuration.get("processes_sha256"):
        raise ValueError("DataSystem isolation process snapshot SHA mismatch")
    processes = json.loads(process_file.read_bytes()).get("processes", [])
    workers = configuration.get("workers", [])
    if len(workers) != 2 or {row.get("worker_index") for row in workers} != {0, 1}:
        raise ValueError("Two distinct DataSystem Worker isolation observations are required")
    result, directories = [], []
    for worker in sorted(workers, key=lambda row: row["worker_index"]):
        index = worker["worker_index"]
        source = directory / f"worker-{index}.flags"
        if source.is_symlink() or not source.is_file() or digest(source) != worker.get("sha256"):
            raise ValueError("DataSystem isolation flag bytes differ from the live gate snapshot")
        pairs = [line[2:].split("=", 1) for line in source.read_text().splitlines() if line.startswith("--")]
        flags = dict(pairs)
        if len(flags) != len(pairs):
            raise ValueError("Duplicate DataSystem Worker flags")
        value = flags.get("rocksdb_store_dir", "")
        path, instance = Path(value), Path(worker["path"]).parent
        if not value or not path.is_absolute() or ".." in path.parts or instance not in path.parents:
            raise ValueError("Each Worker requires an explicit RocksDB directory inside its own instance")
        expected = "--rocksdb_store_dir=" + value
        live = worker.get("live_argv", [])
        matches = [row for row in processes if row.get("name") == f"worker{index}"]
        if (len(matches) != 1 or matches[0].get("pid") != worker.get("pid")
                or str(matches[0].get("start_ticks")) != str(worker.get("start_ticks"))
                or [item for item in live if item.startswith("--rocksdb_store_dir=")] != [expected]
                or [item for item in matches[0].get("command", []) if item.startswith("--rocksdb_store_dir=")] != [expected]):
            raise ValueError("DataSystem saved live argv/process identity does not confirm its per-Worker RocksDB directory")
        directories.append(path)
        result.append({"worker_index": index, "rocksdb_store_dir": value, "pid": worker["pid"],
                       "start_ticks": worker["start_ticks"], "flags_sha256": worker["sha256"],
                       "evidence": f"gate/r0-datasystem/worker-{index}.flags"})
    if directories[0] == directories[1] or directories[0] in directories[1].parents or directories[1] in directories[0].parents:
        raise ValueError("DataSystem Worker RocksDB paths must be distinct and nonoverlapping")
    return {"verified": True, "workers": result,
            "scope": "Saved gate flag bytes, captured live argv and process identities confirm distinct per-instance directory paths; this is not a claim of current process liveness or an unrecorded filesystem inode audit."}



def verify_runtime_inputs(suite, runtime_inputs, require=True):
    stage_file = Path(suite) / "batch-stage.json"
    if not stage_file.is_file() or stage_file.is_symlink():
        if require:
            raise ValueError("Frozen batch-stage.json and --runtime-inputs are required")
        return None, []
    stage = json.loads(stage_file.read_text())
    if runtime_inputs is None:
        if require:
            raise ValueError("Frozen --runtime-inputs are required")
        return stage, []
    runtime = Path(runtime_inputs).resolve(strict=True)
    if not runtime.is_dir() or not isinstance(stage.get("inputs"), dict) or not stage["inputs"]:
        raise ValueError("A nonempty frozen runtime input inventory is required")
    records = []
    for name, expected in stage["inputs"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or name in {"", "."}:
            raise ValueError("Unsafe frozen runtime path: " + name)
        source = runtime / relative
        if source.is_symlink() or any(parent.is_symlink() for parent in source.parents if parent != runtime and runtime in parent.parents):
            raise ValueError("Frozen runtime input must not traverse symlinks: " + name)
        if not source.is_file() or runtime not in source.resolve().parents:
            raise ValueError("Missing or unsafe frozen runtime input: " + name)
        checksum, size = expected.get("sha256"), expected.get("bytes")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum) or type(size) is not int or size < 0:
            raise ValueError("Invalid frozen runtime inventory record: " + name)
        if source.stat().st_size != size or digest(source) != checksum:
            raise ValueError("Frozen runtime input SHA256/bytes mismatch: " + name)
        mode = expected.get("mode")
        if mode is not None and (type(mode) is not int or not 0 <= mode <= 0o777 or source.stat().st_mode & 0o777 != mode):
            raise ValueError("Frozen runtime input mode mismatch: " + name)
        records.append({"relative": name, "path": name, "source": str(source), "sha256": checksum, "bytes": size,
                        **({"mode": mode} if mode is not None else {})})
    overridden = set()
    for override in stage.get("runtime_overrides", []):
        name = override.get("path")
        if name in overridden or name not in stage["inputs"]:
            raise ValueError("Unexpected or duplicate frozen runtime override")
        overridden.add(name)
        if any(override[key] != stage["inputs"][name].get(key) for key in ["sha256", "bytes", "mode"] if key in override):
            raise ValueError("Frozen runtime override differs from its final input inventory: " + name)
    return stage, records



def verify_source_build_provenance(stage, runtime_records, builds, *, products):
    if not stage or "source_build_mode" not in stage:
        return None
    PRODUCTS = products
    if stage.get("source_build_mode") != "builds-only-native-root" or stage.get("legacy_suites_required") is not False:
        raise ValueError("Unknown or incomplete source-build stage mode")
    frozen = {row["relative"]: row for row in runtime_records}
    proof = stage.get("builds_provenance", {})
    name = "builds-provenance/validation.json"
    if (not isinstance(proof, dict) or proof.get("schema_version") != 1 or proof.get("path") != name or name not in frozen
            or any(proof.get(key) != frozen[name].get(key) for key in ["sha256", "bytes", "mode"])):
        raise ValueError("Source-build provenance must bind its exact frozen validation file")

    def read(relative):
        record = frozen.get(relative, {})
        path = Path(record.get("source", ""))
        if (not path.is_file() or path.is_symlink() or path.stat().st_size != record.get("bytes")
                or digest(path) != record.get("sha256")):
            raise ValueError("Missing or changed frozen source-build evidence: " + relative)
        return path.read_bytes()

    validation = json.loads(read(name))
    native_root = Path(stage.get("base", ""))
    if (not native_root.is_absolute() or ".." in native_root.parts or validation.get("native_root") != str(native_root)
            or set(validation.get("products", {})) != set(PRODUCTS)
            or not isinstance(validation.get("native_artifacts"), dict)
            or validation.get("native_artifacts") != stage.get("build_artifacts")):
        raise ValueError("Source-build validation root, products or native artifacts differ from the stage")
    expected_inputs = {f"builds/{product}.json" for product in PRODUCTS}
    native_inputs, expected_artifacts = set(), {}
    for product, spec in PRODUCTS.items():
        metadata_name = f"build-metadata/{product}.json"
        recipe_name = spec["script"]
        native_inputs.update([metadata_name, recipe_name])
        copies = ["builds-provenance/inputs/" + relative for relative in [metadata_name, recipe_name]]
        expected_inputs.update(copies)
        content = read(f"builds/{product}.json")
        if content != read(copies[0]):
            raise ValueError("Source-build metadata and its original snapshot differ")
        metadata = json.loads(content)
        for relative, expected in metadata.get("compiler_inputs", {}).items():
            if Path(relative).parts[:2] != ("compiler-inputs", product):
                raise ValueError("Compiler provenance escaped its product namespace")
            copied = "builds-provenance/inputs/" + relative
            if (relative not in validation["inputs"] or validation["inputs"][relative] != expected
                    or hashlib.sha256(read(copied)).hexdigest() != expected["sha256"]):
                raise ValueError("Frozen compiler input differs from its build record")
            native_inputs.add(relative)
            expected_inputs.add(copied)
        tracked = metadata.get("source_tracked_status_porcelain", "" if metadata.get("source_worktree_clean") is True else None)
        state = source_state(product, metadata.get("source_status_porcelain"), tracked)
        current = validation["products"][product]
        current_state = source_state(product, current.get("source_status_porcelain"), current.get("source_tracked_status_porcelain"))
        if (metadata.get("product") != product or metadata.get("tag") != spec["tag"]
                or any(metadata.get(key) != spec["sha"] for key in ["sha", "pinned_sha", "tag_sha"])
                or metadata.get("official_source") != spec["official_source"] or metadata.get("build_exit") != 0
                or metadata.get("identity_valid") is not True or metadata.get("validation_errors") != []
                or not state["source_worktree_policy_valid"] or not current_state["source_worktree_policy_valid"]
                or any(current.get(key) != value for key, value in current_state.items())
                or current.get("tag") != spec["tag"] or current.get("head") != spec["sha"] or current.get("tag_sha") != spec["sha"]
                or current.get("metadata") != metadata_name or current.get("recipe") != recipe_name
                or current.get("legacy_suites_required") is not False or current.get("smoke_exit") != metadata.get("smoke_exit")
                or metadata.get("source_directory") != str(native_root / spec["source"])
                or metadata.get("python_executable") != str(native_root / spec["python"])
                or not str(metadata.get("python", "")).startswith("Python 3.11.")
                or metadata.get("build_recipe") != recipe_name
                or hashlib.sha256(read(copies[1])).hexdigest() != metadata.get("build_recipe_sha256")):
            raise ValueError("Source-build metadata, pin, clean policy, recipe or Python identity is incomplete")
        started, finished = metadata.get("build_started_wall_ns"), metadata.get("build_finished_wall_ns")
        if type(started) is not int or type(finished) is not int or not 0 <= started <= finished:
            raise ValueError("Source-build command timestamps are missing or reversed")
        matches = [row for row in builds if row.get("product") == product]
        if len(matches) != 1 or {key: value for key, value in matches[0].items() if key != "evidence"} != metadata:
            raise ValueError("Public build record differs from its frozen source-build metadata")
        hashes, sizes = metadata.get("artifact_hashes", {}), metadata.get("artifact_sizes", {})
        if not hashes or set(hashes) != set(sizes) or any(not any(token in Path(path).name for path in hashes) for token in spec["native_required"]):
            raise ValueError("Source-build native artifact set is incomplete")
        for relative, checksum in hashes.items():
            path = Path(relative)
            actual = validation["native_artifacts"].get(str(native_root / path), {})
            if (path.is_absolute() or ".." in path.parts or path.as_posix() != relative or not path.parts or path.parts[0] != product
                    or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
                    or type(sizes[relative]) is not int or sizes[relative] <= 0
                    or actual.get("sha256") != checksum or actual.get("bytes") != sizes[relative]
                    or type(actual.get("mode")) is not int or not 0 <= actual["mode"] <= 0o777):
                raise ValueError("Source-build artifact identity differs from the freshly staged validation inventory")
            expected_artifacts[str(native_root / path)] = actual
    if (len(builds) != 3 or set(validation.get("inputs", {})) != native_inputs
            or validation["native_artifacts"] != expected_artifacts or set(proof.get("inputs", {})) != expected_inputs):
        raise ValueError("Source-build provenance must contain exactly three metadata/recipe pairs and their build records")
    for relative in expected_inputs:
        if (relative not in frozen or any(proof["inputs"][relative].get(key) != frozen[relative].get(key) for key in ["sha256", "bytes", "mode"])):
            raise ValueError("Source-build provenance inventory differs from the frozen inputs")
    for relative in native_inputs:
        frozen_copy = frozen["builds-provenance/inputs/" + relative]
        if any(validation["inputs"][relative].get(key) != frozen_copy.get(key) for key in ["sha256", "bytes", "mode"]):
            raise ValueError("Source-build original metadata/recipe validation differs from its preserved snapshot")
    return {"verified": True, "mode": stage["source_build_mode"], "legacy_suites_required": False,
            "evidence": "measured-runtime/" + name, "sha256": proof["sha256"], "bytes": proof["bytes"],
            "products": 3, "native_artifacts": len(expected_artifacts),
            "legacy_smoke_exit": {row["product"]: row.get("smoke_exit") for row in builds},
            "scope": "Archived stage-time HEAD/tag/clean-policy, recipe and native artifact validation; no independent compiler execution attestation. Same-stage batch gate, smoke, TCP proof and full matrix remain required. Legacy baseline suites and default-capacity improvement are not inferred."}
