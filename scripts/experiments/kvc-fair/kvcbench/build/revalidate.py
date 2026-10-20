"""Recheck completed source builds without changing original metadata or replaying recipes."""
import argparse
import fcntl
from pathlib import Path
from types import SimpleNamespace
import time

from kvcbench.build import catalog, metadata
from kvcbench.platform.files import atomic_json
from kvcbench.platform.locks import open_lock


def revalidate(root, output):
    root = catalog.build_root(root)
    with open_lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return revalidate_locked(root, output)


def revalidate_locked(root, output):
    output = Path(output).absolute()
    relative = output.relative_to(root)
    if not relative.parts or relative.parts[0] != "revalidation":
        raise ValueError("Fresh revalidation output must be inside the revalidation namespace")
    if any(path.is_symlink() for path in [output, *output.parents]):
        raise ValueError("Revalidation output cannot contain symlinks")
    terminal = catalog.regular(root, "build-result.json")
    result = metadata.read_json(terminal)
    cleanup = result.get("cleanup", {})
    if (result.get("fatal") is not None or cleanup.get("removed") is not True
            or cleanup.get("drained", {}).get("populated") != []):
        raise ValueError("Completed and drained source-build attempt is required")
    inputs = {"build-result.json": catalog.identity(terminal)}
    originals, commands = {}, {}
    for name, spec in metadata.PRODUCTS.items():
        old_path = f"build-metadata/{name}.json"
        command_path = f"commands/{name}/build-command.json"
        originals[name] = metadata.read_json(catalog.regular(root, old_path))
        commands[name] = metadata.read_json(catalog.regular(root, command_path))
        old, command = originals[name], commands[name]
        if (command.get("returncode") != 0 or command.get("error") is not None
                or command.get("argv") != ["bash", str(root / spec["script"])]
                or command.get("cwd") != str(root)
                or old.get("build_exit") != 0
                or old.get("build_started_wall_ns") != command.get("start_wall_ns")
                or old.get("build_finished_wall_ns") != command.get("end_wall_ns")):
            raise ValueError("Successful original command and matching build interval required: " + name)
        log_path = f"commands/{name}/build.log"
        if command.get("log") != str(root / log_path):
            raise ValueError("Original build log location differs from command record")
        for path in [old_path, command_path, log_path]:
            inputs[path] = catalog.identity(catalog.regular(root, path))
    output.mkdir(parents=True, exist_ok=False)
    started = time.time_ns()
    package = Path(__file__).resolve().parents[1]
    validator_paths = [Path(__file__), Path(catalog.__file__), Path(metadata.__file__),
                       package / "build/toolchain.py", package / "core/source_state.py",
                       package.parent / "configs/versions/kvc_20261020.json"]
    validator = {str(path): catalog.identity(path) for path in validator_paths}
    paths = {}
    try:
        for name in metadata.PRODUCTS:
            destination = output / (name + ".json")
            old = originals[name]
            code = metadata.record(SimpleNamespace(root=root, product=name, source=None, python=None,
                compiler=None, started_wall_ns=old["build_started_wall_ns"],
                finished_wall_ns=old["build_finished_wall_ns"], build_exit=0, output=destination))
            current = metadata.read_json(destination)
            if code != 0 or any(current[key] != old[key] for key in ["artifact_hashes", "artifact_sizes"]):
                raise ValueError("Revalidation failed or original build outputs changed: " + name)
            paths[name] = str(destination.relative_to(root))
        records, artifacts, provenance = catalog.validate_builds(root, record_paths=paths)
        for path, expected in inputs.items():
            if catalog.identity(catalog.regular(root, path)) != expected:
                raise ValueError("Original build evidence changed during revalidation")
        atomic_json(output / "build-catalog.json", {"schema_version": 1, "records": records,
                    "artifacts": artifacts, "provenance": provenance}, exclusive=True)
        outcome = {"accepted": True, "catalog": "build-catalog.json"}
    except Exception as error:
        atomic_json(output / "result.json", {"accepted": False, "error": str(error),
                    "compiled_in_this_attempt": False, "inputs": inputs, "validator": validator,
                    "started_wall_ns": started, "finished_wall_ns": time.time_ns()}, exclusive=True)
        raise
    outcome.update(compiled_in_this_attempt=False, purpose="validation-only reuse of original source-build outputs",
                   inputs=inputs, validator=validator, started_wall_ns=started, finished_wall_ns=time.time_ns())
    atomic_json(output / "result.json", outcome, exclusive=True)
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    revalidate(args.root, args.out)


if __name__ == "__main__":
    main()
