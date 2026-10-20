#!/usr/bin/env python3
"""Compose completed correctness evidence without rerunning or rewriting its sources."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import time


BATCHES = [1, 16, 256, 4096]
SHAS = {"datasystem": "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6",
        "mooncake": "719735896c86b56fabec6cf3e825fb2ea640597a",
        "lmcache": "3e11e10812b448142c6cdb177a3058652bd826fa"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def record(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": Path(path).stat().st_size}


def read_json(path):
    if Path(path).is_symlink():
        raise ValueError("JSON evidence cannot be a symlink")
    return json.loads(Path(path).read_text())


def products(value):
    rows = value["products"] if isinstance(value, dict) else value
    if not isinstance(rows, list) or len(rows) != 3 or {row.get("name") for row in rows} != set(SHAS):
        raise ValueError("Exactly three unique product entries are required")
    return rows


def inventory(directory):
    rows = {}
    for path in sorted(directory.rglob("*")):
        name = str(path.relative_to(directory))
        if path.is_symlink():
            rows[name] = {"kind": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            rows[name] = {"kind": "file", **record(path)}
    return rows


def one_step(raw, count, name):
    rows = [step for step in raw["steps"] if step.get("batch_keys") == count and step.get("name") == name]
    if len(rows) != 1 or rows[0].get("status") != "passed":
        raise ValueError(f"Missing or failed step B={count}: {name}")
    return rows[0]


def component(raw, product, index, mode, manifest):
    if (raw.get("valid") is not True or raw.get("adapter") != manifest["adapter"]
            or raw.get("client_index") != index or raw.get("value_size") != 1024
            or raw.get("batch_sizes") != BATCHES or raw.get("failed_steps") != []):
        raise ValueError("Component identity, parameters, or valid status differs from the frozen inputs")
    cases, steps = raw.get("cases"), raw.get("steps")
    if (not isinstance(cases, list) or [case.get("batch_keys") for case in cases] != BATCHES
            or not all(case.get("valid") is True for case in cases)
            or not isinstance(steps, list) or not steps
            or any(step.get("status") not in {"passed", "not_applicable"} for step in steps)):
        raise ValueError("Component cases or step outcomes are incomplete")
    metadata = raw.get("metadata", {})
    expected = "insert_or_update" if product == "datasystem" or mode == "upsert" else "success_without_rewrite"
    if (metadata.get("source_sha") != SHAS[product]
            or metadata.get("native_semantics", {}).get("write_existing_key") != expected):
        raise ValueError("Pinned source or existing-key write semantics differs")
    for name in ["initialize_adapter", "inspect_metadata", "close_adapter"]:
        rows = [step for step in steps if step.get("name") == name]
        if len(rows) != 1 or rows[0].get("status") != "passed":
            raise ValueError("Component initialization/metadata/cleanup evidence incomplete")
    for case in cases:
        count = case["batch_keys"]
        if case.get("expected_existing_key_semantics") != expected:
            raise ValueError("Per-batch existing-key semantics is inconsistent")
        scalar_only = product == "lmcache" and count > 1
        if case.get("put_native_supported") is not (not scalar_only) or case.get("get_native_supported") is not (not scalar_only):
            raise ValueError("Native data batch capability was changed")
        for name in ["fresh_keys_initially_absent", "exists_full_hit", "exists_all_miss", "exists_after_existing_write"]:
            if one_step(raw, count, name).get("result", {}).get("exact_vector") is not True:
                raise ValueError("EXIST vector check did not pass exactly")
        if count > 1:
            for name in ["exists_mixed_sparse", "exists_mixed_reverse"]:
                if one_step(raw, count, name).get("result", {}).get("exact_vector") is not True:
                    raise ValueError("Sparse EXIST vector check did not pass exactly")
        names = (["setup_scalar_puts", "get_scalar_mapping_spotchecks", "write_existing_scalar_control",
                  "verify_existing_write_scalar_spotchecks"] if scalar_only else
                 ["put_initial_native_batch", "get_full_exact", "get_reverse_exact", "get_subset_order_exact",
                  "write_existing_native_control", "verify_existing_write_full_exact"])
        for name in names:
            step = one_step(raw, count, name)
            if "write_existing" in name and "verify" not in name and step.get("expected_existing_key_semantics") != expected:
                raise ValueError("Existing-write control requested different semantics")
            if name.startswith("get_") or name.startswith("verify_"):
                result = step.get("result", {})
                checks = result.get("checks") if scalar_only else [result]
                if not checks or not all(check.get("exact_bytes") is True and check.get("expected_values_sha256") == check.get("observed_values_sha256") for check in checks):
                    raise ValueError("Full bytes/order verification did not pass")
    if not raw.get("end_wall_ns") or raw.get("duration_s") is None:
        raise ValueError("Component execution timing is incomplete")


def compose(root, source_gate, diagnostic, out):
    root, source_gate, diagnostic, out = map(lambda path: Path(path).absolute(), [root, source_gate, diagnostic, out])
    if out.exists() or out.is_symlink() or source_gate == diagnostic or out in source_gate.parents or out in diagnostic.parents:
        raise ValueError("Use a fresh independent composed gate directory")
    start_wall, start_mono, started = time.time_ns(), time.monotonic_ns(), utc()
    with (root / "suite.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stage = read_json(root / "batch-stage.json")
        if stage.get("root") != str(root):
            raise ValueError("Stage root differs from requested frozen root")
        verified = {}
        for name, expected in stage["inputs"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe stage input path")
            actual = record(root / relative)
            if actual["sha256"] != expected["sha256"] or actual["bytes"] != expected["bytes"]:
                raise ValueError("Frozen stage input changed: " + name)
            verified[name] = actual
        for path, expected in stage.get("build_artifacts", {}).items():
            if record(path)["sha256"] != expected["sha256"]:
                raise ValueError("Pinned native artifact changed: " + path)
        if (root / "batch-stage.json").read_bytes() != (source_gate / "batch-stage.json").read_bytes():
            raise ValueError("Source gate stage bytes differ from the frozen root")
        manifest = products(read_json(root / "products.json"))
        if products(read_json(source_gate / "products.json")) != manifest:
            raise ValueError("Source gate product manifest differs from current frozen inputs")
        by_name = {row["name"]: row for row in manifest}
        original, diagnosis = read_json(source_gate / "summary.json"), read_json(diagnostic / "summary.json")
        if not original.get("ended") or "r0-datasystem" not in original.get("completed", []):
            raise ValueError("Original DS component group did not complete")
        if (diagnosis.get("kind") != "untraced_correctness_diagnostic" or diagnosis.get("valid") is not True
                or diagnosis.get("failed") or not diagnosis.get("ended")
                or {row.get("name") for row in diagnosis.get("products", []) if row.get("valid") is True} != {"mooncake", "lmcache"}
                or len(diagnosis.get("products", [])) != 2):
            raise ValueError("Independent untraced diagnostic is incomplete or failed")
        script_sha = diagnosis.get("diagnostic_script_sha256", "")
        if len(script_sha) != 64 or any(char not in "0123456789abcdef" for char in script_sha):
            raise ValueError("Diagnostic execution script SHA is missing")
        expected_commands = {(name, index, mode) for name in ["mooncake", "lmcache"] for index in [0, 1]
                             for mode in (["insert"] if name == "lmcache" else ["insert", "upsert"])}
        seen = set()
        for command in diagnosis.get("commands", []):
            argv = command.get("argv", [])
            if command.get("returncode") != 0 or any(Path(token).name == "strace" for token in argv):
                raise ValueError("Untraced diagnostic command failed or contains strace")
            if not any(Path(token).name == "batch_gate.py" for token in argv):
                continue
            def option(name):
                if argv.count(name) != 1:
                    raise ValueError("Ambiguous diagnostic gate option: " + name)
                return argv[argv.index(name) + 1]
            names = [name for name, row in by_name.items() if row["adapter"] == option("--adapter")]
            if len(names) != 1 or option("--config") != by_name[names[0]]["config"]:
                raise ValueError("Diagnostic adapter/config differs from the frozen manifest")
            identity = (names[0], int(option("--client-index")), option("--write-mode"))
            if identity not in expected_commands or identity in seen:
                raise ValueError("Unexpected or duplicate diagnostic gate command")
            if Path(option("--out")) != diagnostic / identity[0] / f"client-{identity[1]}-{identity[2]}.json":
                raise ValueError("Diagnostic command output does not bind the selected component")
            seen.add(identity)
        if seen != expected_commands:
            raise ValueError("Missing diagnostic endpoint/mode execution")
        selections, selected_inventories = [], {}
        for name in SHAS:
            directory = source_gate / "r0-datasystem" if name == "datasystem" else diagnostic / name
            for index in [0, 1]:
                for mode in ["insert"] if name == "lmcache" else ["insert", "upsert"]:
                    path = directory / f"client-{index}-{mode}.json"
                    raw = read_json(path)
                    component(raw, name, index, mode, by_name[name])
                    selections.append({"product": name, "client_index": index, "write_mode": mode,
                                       "source_path": str(path), **record(path),
                                       "execution_start_wall_ns": raw["start_wall_ns"], "execution_end_wall_ns": raw["end_wall_ns"],
                                       "execution_duration_s": raw["duration_s"], "traced": name == "datasystem"})
            selected_inventories[name] = inventory(directory)
        history = {"original_gate": {"path": str(source_gate), "summary": original, **record(source_gate / "summary.json")},
                   "untraced_diagnostic": {"path": str(diagnostic), "summary": diagnosis, **record(diagnostic / "summary.json"),
                                           "diagnostic_script_sha256": script_sha}}
        out.mkdir(parents=True, exist_ok=False)
        try:
            for name in SHAS:
                directory = source_gate / "r0-datasystem" if name == "datasystem" else diagnostic / name
                destination = out / ("r0-" + name)
                shutil.copytree(directory, destination, symlinks=True)
                if inventory(destination) != selected_inventories[name] or inventory(directory) != selected_inventories[name]:
                    raise ValueError("Selected component bytes changed during composition: " + name)
            for filename in ["products.json", "batch-stage.json"]:
                shutil.copy2(source_gate / filename, out / filename)
            helper_copy = out / "provenance/batch_compose_gate.py"
            helper_copy.parent.mkdir()
            shutil.copyfile(__file__, helper_copy)
            helper_identity = record(__file__)
            if record(helper_copy) != helper_identity:
                raise ValueError("Composer helper changed while its source snapshot was copied")
            (out / "composition-provenance.json").write_text(json.dumps({"sources": history, "selections": selections,
                 "selected_inventories": selected_inventories, "verified_frozen_inputs": verified,
                 "composer_script": {"path": str(Path(__file__).absolute()), "collected_copy": str(helper_copy),
                                     **helper_identity}}, indent=2) + "\n")
            summary = {"profile": "native-batch-tcp", "action": "composed_correctness_gate", "benchmark_eligible": False,
                       "started": started, "start_wall_ns": start_wall, "start_mono_ns": start_mono,
                       "completed": ["r0-" + name for name in SHAS], "failed": [], "valid": True,
                       "ended": utc(), "end_wall_ns": time.time_ns(), "end_mono_ns": time.monotonic_ns(),
                       "sources": history, "component_executions": selections,
                       "scope": "Evidence composition only; no service or SDK operation rerun. Original failed traced gate remains unchanged. Mooncake/LMCache correctness here is untraced, not a TCP syscall or performance proof; original failure cause is not established."}
            summary["duration_s"] = (summary["end_mono_ns"] - start_mono) / 1e9
            (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            return summary
        except Exception as error:
            (out / "composition-failed.json").write_text(json.dumps({"valid": False, "error": str(error), "sources": history}, indent=2) + "\n")
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["root", "source-gate", "diagnostic", "out"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    result = compose(args.root, args.source_gate, args.diagnostic, args.out)
    print(json.dumps({"valid": result["valid"], "action": result["action"], "duration_s": result["duration_s"], "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
