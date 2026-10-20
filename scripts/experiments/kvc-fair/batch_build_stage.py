#!/usr/bin/env python3
"""Stage fresh pure-KV batch inputs from identified source builds, without legacy suites."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

import build_metadata


NATIVE_TEMPLATE_ROOT = "/home/worktrees/kvc-fair-20261020"


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


def validate_builds(native_root):
    root = build_root(native_root)
    for name in build_metadata.PRODUCTS:
        regular(root, f"build-metadata/{name}.json")
    records = build_metadata.load_metadata(root)
    artifacts, provenance = {}, {"native_root": str(root), "products": {}, "inputs": {}, "artifact_aliases": {}}
    for name, spec in build_metadata.PRODUCTS.items():
        row = records[name]
        metadata = regular(root, f"build-metadata/{name}.json")
        provenance["inputs"][str(metadata.relative_to(root))] = identity(metadata)
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
                                       "metadata": f"build-metadata/{name}.json", "recipe": spec["script"],
                                       "smoke_exit": row.get("smoke_exit"), "legacy_suites_required": False}
    provenance.update(scope="Fresh HEAD/tag/status, recorded successful build identity, recipe bytes and native output hashes/sizes. This does not independently attest compiler execution; fresh batch gate and smoke remain required. No legacy performance or smoke success is synthesized.",
                      native_artifacts=artifacts)
    return records, artifacts, provenance


def tcp_manifest(bundle, root):
    source = regular(bundle, "products.json")
    manifest = json.loads(source.read_text().replace(NATIVE_TEMPLATE_ROOT, str(root)))
    if (not isinstance(manifest.get("products"), list) or len(manifest["products"]) != 3
            or {row.get("name") for row in manifest["products"]} != set(build_metadata.PRODUCTS)):
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


def install_provenance(root, temporary, records, provenance):
    current, _, current_provenance = validate_builds(root)
    if current != records or current_provenance != provenance:
        raise ValueError("Source build identity changed while staging")
    for name in records:
        source = regular(root, f"build-metadata/{name}.json")
        destination = temporary / "builds" / (name + ".json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if identity(destination) != provenance["inputs"][f"build-metadata/{name}.json"]:
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


def open_lock(base):
    path = base / "suite.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("suite.lock must be a regular file")
        stream = os.fdopen(descriptor, "a")
    except BaseException:
        os.close(descriptor)
        raise
    return stream


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--builds-only-native-root", "--native-root", dest="builds_only_native_root", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--mooncake-queue", type=int, default=8192)
    parser.add_argument("--feature-profile", choices=["pure-kv"], default="pure-kv")
    parser.add_argument("--cgroup", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    args = parser.parse_args(argv)
    if not 0 <= args.mooncake_queue <= 65535:
        parser.error("queue capacity must be 0 (native default) or at most 65535")
    args.base = args.builds_only_native_root
    from batch_stage import stage
    record = stage(args)
    print(json.dumps({"root": record["root"], "mode": record["source_build_mode"], "inputs": len(record["inputs"]),
                      "native_artifacts": len(record["build_artifacts"]), "legacy_suites_required": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
