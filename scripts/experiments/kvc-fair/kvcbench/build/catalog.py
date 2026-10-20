"""Verify pinned native build records and exact artifact sets independently of old runs."""
import os
import json
import shutil
from pathlib import Path, PurePosixPath
import stat

from kvcbench.build import metadata as build_metadata


def regular(root, relative):
    relative = PurePosixPath(relative)
    if relative.is_absolute() or ".." in relative.parts or str(relative) in {"", "."}:
        raise ValueError("Unsafe build evidence path")
    path = root / str(relative)
    if any(part.is_symlink() for part in [path, *path.parents]) or not path.is_file():
        raise ValueError("Regular in-root build evidence is required: " + str(path))
    return path


def identity(path):
    return {"sha256": build_metadata.digest(path), "bytes": path.stat().st_size,
            "mode": stat.S_IMODE(path.stat().st_mode)}


def build_root(native_root):
    supplied = Path(native_root).absolute()
    if any(part.is_symlink() for part in [supplied, *supplied.parents]) or not supplied.is_dir():
        raise ValueError("Native build root must be a real directory without symlink ancestors")
    return supplied.resolve(strict=True)


def validate_builds(native_root, products=None, record_paths=None):
    root = build_root(native_root)
    names = list(build_metadata.PRODUCTS) if products is None else list(products)
    if not names or len(set(names)) != len(names) or any(name not in build_metadata.PRODUCTS for name in names):
        raise ValueError("Unknown or duplicate product selection")
    if record_paths is not None and set(record_paths) != set(names):
        raise ValueError("Explicit metadata selection must identify every selected product")
    paths = {name: str(regular(root, record_paths[name] if record_paths is not None
                              else f"build-metadata/{name}.json").relative_to(root)) for name in names}
    if len(set(paths.values())) != len(names):
        raise ValueError("Products must select distinct metadata files")
    records = {name: build_metadata.read_json(root / paths[name]) for name in names}
    artifacts, provenance = {}, {"native_root": str(root), "products": {}, "inputs": {}, "artifact_aliases": {},
                                 "metadata_paths": paths}
    for name in names:
        spec = build_metadata.PRODUCTS[name]
        row = records[name]
        if (row.get("product") != name or row.get("tag") != spec["tag"] or row.get("sha") != spec["sha"]
                or row.get("build_exit") != 0 or row.get("identity_valid") is not True):
            raise ValueError("Successful pinned build identity is required: " + name)
        metadata = regular(root, paths[name])
        provenance["inputs"][str(metadata.relative_to(root))] = identity(metadata)
        if row.get('artifact_adoption'):
            from kvcbench.build.adoption import verify
            adoption = verify(root, row)
            provenance['inputs'][adoption['original_metadata']] = adoption['original_identity']
        for relative, expected in row.get("compiler_inputs", {}).items():
            if Path(relative).parts[:2] != ("compiler-inputs", name):
                raise ValueError("Compiler input escaped its product namespace")
            path = regular(root, relative)
            if identity(path) != expected:
                raise ValueError("Recorded compiler input changed")
            provenance["inputs"][relative] = expected
        source = root / spec["source"]
        if (not source.is_dir() or any(part.is_symlink() for part in [source, *source.parents])
                or row.get("source_directory") != str(source)):
            raise ValueError("Build source directory differs from its fixed in-root product layout: " + name)
        head = build_metadata.command_text(["git", "-C", str(source), "rev-parse", "HEAD"])
        tag = build_metadata.command_text(["git", "-C", str(source), "rev-parse", spec["tag"] + "^{commit}"])
        state = build_metadata.source_state(name, build_metadata.git_status(source),
                                           build_metadata.git_status(source, tracked_only=True))
        if head != spec["sha"] or tag != spec["sha"] or not state["source_worktree_policy_valid"]:
            raise ValueError("Actual source HEAD/tag/clean policy differs from the pinned build: " + name)
        if (row.get("pinned_sha") != spec["sha"] or row.get("tag_sha") != spec["sha"]
                or row.get("official_source") != spec["official_source"] or row.get("validation_errors") != []
                or row.get("python_executable") != str(root / spec["python"])
                or not isinstance(row.get("python"), str) or not row["python"].startswith("Python 3.11.")
                or not (root / spec["python"]).is_file() or not os.access(root / spec["python"], os.X_OK)):
            raise ValueError("Native build metadata identity or Python 3.11 layout is incomplete: " + name)
        started, finished = row.get("build_started_wall_ns"), row.get("build_finished_wall_ns")
        if type(started) is not int or type(finished) is not int or not 0 <= started <= finished:
            raise ValueError("Actual build command timestamps are required: " + name)
        if row.get("build_recipe") != spec["script"]:
            raise ValueError("Build recipe is not the pinned product recipe: " + name)
        recipe = regular(root, spec["script"])
        recipe_id = identity(recipe)
        if recipe_id["sha256"] != row.get("build_recipe_sha256"):
            raise ValueError("Build recipe changed after metadata recording: " + name)
        provenance["inputs"][spec["script"]] = recipe_id
        hashes, sizes = row.get("artifact_hashes"), row.get("artifact_sizes")
        if not isinstance(hashes, dict) or not hashes or not isinstance(sizes, dict) or set(hashes) != set(sizes):
            raise ValueError("Artifact hashes and sizes must identify the same nonempty build outputs: " + name)
        selected, aliases = set(), {}
        for pattern in spec["artifacts"]:
            for path in root.glob(pattern):
                if path.is_symlink():
                    try:
                        target = path.resolve(strict=True)
                    except (OSError, RuntimeError) as error:
                        raise ValueError("Broken or cyclic native artifact alias") from error
                    if (any(parent.is_symlink() for parent in path.parents) or root not in target.parents
                            or not target.is_file()):
                        raise ValueError("Native artifact alias escaped its real build root")
                    aliases[str(path)] = {"target": str(target), "link_text": os.readlink(path)}
                elif path.is_file():
                    selected.add(str(regular(root, path.relative_to(root)).relative_to(root)))
        if set(hashes) != selected:
            raise ValueError("Build outputs differ from the exact recorded product artifact set: " + name)
        if any(not any(required in Path(path).name for path in hashes) for required in spec["native_required"]):
            raise ValueError("Required native outputs are absent: " + name)
        for path, alias in aliases.items():
            if str(Path(alias["target"]).relative_to(root)) not in selected:
                raise ValueError("Native alias does not resolve to this product's recorded regular output")
            provenance["artifact_aliases"][path] = alias
        for relative, expected in hashes.items():
            path = regular(root, relative)
            observed = identity(path)
            if (type(sizes[relative]) is not int or sizes[relative] <= 0
                    or observed["sha256"] != expected or observed["bytes"] != sizes[relative]):
                raise ValueError("Native build artifact changed after metadata recording: " + relative)
            if str(path) in artifacts:
                raise ValueError("Two products claim the same native artifact")
            artifacts[str(path)] = observed
        provenance["products"][name] = {"tag": spec["tag"], "head": head, "tag_sha": tag, **state,
                                       "metadata": paths[name], "recipe": spec["script"],
                                       "smoke_exit": row.get("smoke_exit"), "legacy_suites_required": False}
    provenance.update(scope="Fresh HEAD/tag/status, recorded successful build identity, recipe bytes and native output hashes/sizes. This does not independently attest compiler execution; fresh batch gate and smoke remain required. No legacy performance or smoke success is synthesized.",
                      native_artifacts=artifacts)
    return records, artifacts, provenance


def install_provenance(root, temporary, records, provenance):
    current, _, current_provenance = validate_builds(root, products=list(records),
                                                   record_paths=provenance.get("metadata_paths"))
    if current != records or current_provenance != provenance:
        raise ValueError("Source build identity changed while staging")
    for name in records:
        metadata_name = provenance.get("metadata_paths", {}).get(name, f"build-metadata/{name}.json")
        source = regular(root, metadata_name)
        destination = temporary / "builds" / (name + ".json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if identity(destination) != provenance["inputs"][metadata_name]:
            raise ValueError("Copied build metadata differs from its validated source")
    for relative, expected in provenance["inputs"].items():
        source = regular(root, relative)
        if identity(source) != expected:
            raise ValueError("Build metadata or recipe changed during staging: " + relative)
        destination = temporary / "builds-provenance/inputs" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if identity(destination) != expected:
            raise ValueError("Copied build evidence differs from its validated source: " + relative)
    with (temporary / "builds-provenance/validation.json").open("x") as stream:
        json.dump(provenance, stream, indent=2)
        stream.write("\n")
    validation = temporary / "builds-provenance/validation.json"
    paths = [Path("builds") / (name + ".json") for name in records]
    paths += [Path("builds-provenance/inputs") / relative for relative in provenance["inputs"]]
    return {"schema_version": 1, "path": "builds-provenance/validation.json", **identity(validation),
            "inputs": {path.as_posix(): identity(temporary / path) for path in paths}}


def validate_saved_catalog(path, native_root):
    saved = build_metadata.read_json(path)
    provenance = saved.get("provenance", {})
    records, artifacts, current = validate_builds(native_root, record_paths=provenance.get("metadata_paths"))
    if (saved.get("schema_version") != 1 or saved.get("records") != records
            or saved.get("artifacts") != artifacts or provenance != current):
        raise ValueError("Saved build catalog differs from current selected build evidence")
    return records, artifacts, current
