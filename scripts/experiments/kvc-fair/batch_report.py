#!/usr/bin/env python3
"""Render a separate, offline-capable native batch TCP report from actual evidence."""
import argparse
import bisect
import datetime
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import tarfile
import time
import zipfile

from batch_analyze import analyze, export
from report import finalize_artifacts, operation_ledger, reproduction_archive, version_zip_href


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            checksum.update(block)
    return checksum.hexdigest()


def copy_file(source, out, relative):
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Expected regular evidence file: " + str(source))
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": relative, "sha256": digest(target), "bytes": target.stat().st_size}


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
        records.append({"relative": name, "path": name, "source": str(source), "sha256": checksum, "bytes": size})
    return stage, records


def verify_gate_evidence(gate_path, stage, products):
    from batch_compose_gate import component
    gate_path = Path(gate_path).resolve(strict=True)
    summary_file = gate_path / "summary.json"
    gate = json.loads(summary_file.read_text())
    if gate.get("valid") is not True or not gate.get("ended") or gate.get("failed") or gate.get("fatal"):
        raise ValueError("The batch correctness gate has not completed successfully")
    if json.loads((gate_path / "batch-stage.json").read_text()) != stage:
        raise ValueError("The gate does not belong to the same frozen stage as the measured matrix")
    manifest = products.get("products", []) if isinstance(products, dict) else products
    gate_products = json.loads((gate_path / "products.json").read_text())
    gate_products = gate_products.get("products", []) if isinstance(gate_products, dict) else gate_products
    if gate_products != manifest:
        raise ValueError("The gate product manifest differs from the measured matrix")
    by_name = {row["name"]: row for row in manifest}
    remote_root = Path(stage["root"])
    wanted = {(product, index, mode) for product in ["datasystem", "mooncake", "lmcache"] for index in [0, 1]
              for mode in (["insert"] if product == "lmcache" else ["insert", "upsert"])}
    if gate.get("action") == "gate":
        if set(gate.get("completed", [])) != {"r0-" + name for name in by_name} or len(gate["completed"]) != 3:
            raise ValueError("Direct gate did not complete each product group")
        events = [json.loads(line) for line in (gate_path / "events.jsonl").read_text().splitlines() if line.strip()]
        components = []
        for product, index, mode in sorted(wanted):
            source = gate_path / ("r0-" + product) / f"client-{index}-{mode}.json"
            if source.is_symlink() or not source.is_file():
                raise ValueError("Missing direct gate client/mode component")
            raw = json.loads(source.read_text())
            component(raw, product, index, mode, by_name[product])
            remote = str(remote_root / gate_path.name / source.relative_to(gate_path))
            matches = [event for event in events if event.get("event") == "command" and event.get("returncode") == 0
                       and any(Path(token).name == "batch_gate.py" for token in event.get("argv", []))
                       and "--out" in event["argv"] and event["argv"][event["argv"].index("--out") + 1] == remote]
            if len(matches) != 1 or not any(Path(token).name == "strace" for token in matches[0]["argv"]):
                raise ValueError("Direct gate component lacks its unique successful traced command")
            argv = matches[0]["argv"]
            expected_options = {"--adapter": by_name[product]["adapter"], "--config": by_name[product]["config"],
                                "--client-index": str(index), "--write-mode": mode, "--out": remote,
                                "--run-id": f"{gate_path.name}-r0-{product}-c{index}-{mode}"}
            if any(argv.count(option) != 1 or argv[argv.index(option) + 1:argv.index(option) + 2] != [value] for option, value in expected_options.items()):
                raise ValueError("Direct gate command options do not match the exact frozen component identity")
            traces = list((source.parent / f"wire-client-{index}-{mode}").glob("strace.*"))
            if not traces or not any(path.is_file() and not path.is_symlink() and path.stat().st_size for path in traces):
                raise ValueError("Direct gate component is missing its retained syscall traces")
            components.append({"product": product, "client_index": index, "write_mode": mode,
                               "local_source": str(source), "sha256": digest(source), "bytes": source.stat().st_size,
                               "traced": True, "execution_start_wall_ns": raw.get("start_wall_ns"),
                               "execution_end_wall_ns": raw.get("end_wall_ns"), "execution_duration_s": raw.get("duration_s")})
        return gate, {"passed": True, "components": components, "histories": [], "composition_is_execution": False,
                      "gate_kind": "direct_traced_correctness_execution",
                      "scope": "Ten actual client/mode executions with full bytes/order/existing-write correctness revalidated; raw syscall traces retained. No per-operation TCP byte count is inferred."}
    if gate.get("action") != "composed_correctness_gate" or gate.get("benchmark_eligible") is not False:
        raise ValueError("Expected direct or explicitly composed batch correctness evidence")
    histories = []
    for name, record in gate.get("sources", {}).items():
        directory = gate_path.parent / Path(record["path"]).name
        source = directory / "summary.json"
        if source.is_symlink() or not source.is_file() or digest(source) != record["sha256"] or source.stat().st_size != record["bytes"] or json.loads(source.read_text()) != record["summary"]:
            raise ValueError("Gate history summary does not match its recorded exact bytes: " + name)
        histories.append({"name": name, "directory": str(directory), "summary": record["summary"], "sha256": record["sha256"], "bytes": record["bytes"]})
    if {row["name"] for row in histories} != {"original_gate", "untraced_diagnostic"}:
        raise ValueError("Both the original failed gate and independent diagnostic must be preserved")
    seen, components = set(), []
    for row in gate.get("component_executions", []):
        key = row["product"], row["client_index"], row["write_mode"]
        if key not in wanted or key in seen or row.get("traced") is not (row["product"] == "datasystem"):
            raise ValueError("Unexpected or duplicate composed gate component")
        relative = Path(row["source_path"]).relative_to(remote_root)
        source = gate_path.parent / relative
        if source.is_symlink() or not source.is_file() or digest(source) != row["sha256"] or source.stat().st_size != row["bytes"]:
            raise ValueError("Composed gate component does not match its source bytes")
        raw = json.loads(source.read_text())
        component(raw, *key, by_name[key[0]])
        seen.add(key)
        components.append({**row, "local_source": str(source)})
    if seen != wanted:
        raise ValueError("The composed gate does not cover all ten required client/mode executions")
    return gate, {"passed": True, "components": components, "histories": histories,
                  "composition_is_execution": False, "gate_kind": "composed_correctness_sources", "scope": "Source bytes and full correctness/order/existing-write steps revalidated. Mooncake/LMCache components are untraced; no batch TCP byte count or performance proof is inferred. Original failure cause remains unestablished."}


def compact_payload(analysis):
    payload = {key: value for key, value in analysis.items() if key not in {"samples", "rows"}}
    pool, indices, rows = [], {}, []
    for original in analysis["rows"]:
        row = {key: value for key, value in original.items() if key != "client_metadata"}
        row["client_metadata_ids"] = []
        for metadata in original.get("client_metadata", []):
            key = json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if key not in indices:
                indices[key] = len(pool)
                pool.append(metadata)
            row["client_metadata_ids"].append(indices[key])
        rows.append(row)
    payload.update(rows=rows, metadata_pool=pool)
    samples = analysis["samples"]
    times = [sample["mono_ns"] for sample in samples]
    kept = set()
    for row in rows:
        if row["valid"] and type(row.get("start_mono_ns")) is int and type(row.get("end_mono_ns")) is int:
            kept.update(range(bisect.bisect_left(times, row["start_mono_ns"]), bisect.bisect_right(times, row["end_mono_ns"])))
    fields = ["memory", "anon", "shmem", "cpu_usec_per_s", "read_bytes_per_s", "write_bytes_per_s"]
    payload["samples"] = [{**{key: samples[index][key] for key in ["mono_ns", "prev_mono_ns", "wall_ms", "sampling_ms"] if key in samples[index]},
                           **{role: {key: samples[index][role].get(key) for key in fields} for role in ["server", "client"]}}
                          for index in sorted(kept)]
    payload["sample_embedding_scope"] = "Every actual sample inside a valid SDK window, with only chart fields; full native samples remain in raw evidence. Metadata is deduplicated without changing its client references."
    return payload


def verify_tcp_evidence(path, gate_path, scripts):
    from batch_gate import payload
    path, gate_path, scripts = Path(path), Path(gate_path).resolve(strict=True), Path(scripts)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected an immutable regular batch TCP verification JSON")
    raw = json.loads(path.read_bytes())
    if (raw.get("schema_version") != 2 or raw.get("kind") != "datasystem_native_batch_tcp_any_key_full_payload_audit"
            or raw.get("valid") is not True or raw.get("fatal") or Path(raw.get("suite", "")).resolve() != gate_path):
        raise ValueError("A successful same-gate native batch TCP payload audit is required")
    if not isinstance(raw.get("inputs"), dict) or not raw["inputs"]:
        raise ValueError("Batch TCP audit must bind its original gate and trace input bytes")
    for name, expected in raw["inputs"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or name in {"", "."}:
            raise ValueError("Unsafe batch TCP audit input path")
        source = gate_path / relative
        if source.is_symlink() or any(parent.is_symlink() for parent in source.parents if gate_path in parent.parents):
            raise ValueError("Batch TCP audit input must not traverse symlinks")
        if (not source.is_file() or type(expected.get("bytes")) is not int or expected["bytes"] < 0
                or source.stat().st_size != expected["bytes"] or digest(source) != expected.get("sha256")):
            raise ValueError("Batch TCP audit input SHA256/bytes mismatch: " + name)
    sources = raw.get("parser_sources", {})
    if set(sources) != {"batch_tcp_verify.py", "batch_gate.py", "tcp_probe_ds_sizes.py"}:
        raise ValueError("Batch TCP audit parser source provenance is incomplete")
    records = [{"source": str(path), "relative": "datasystem-batch-tcp-verification.json",
                "sha256": digest(path), "bytes": path.stat().st_size}]
    for name, expected in sources.items():
        source = scripts / name
        if source.is_symlink() or not source.is_file() or source.stat().st_size != expected.get("bytes") or digest(source) != expected.get("sha256"):
            raise ValueError("Batch TCP audit parser bytes differ from its executed source: " + name)
        records.append({"source": str(source), "relative": "tcp-proof-sources/" + name,
                        "sha256": expected["sha256"], "bytes": expected["bytes"]})
    wanted = {(index, mode, batch) for index in [0, 1] for mode in ["insert", "upsert"] for batch in [1, 16, 256, 4096]}
    seen, cases, clients = set(), [], {}
    for case in raw.get("cases", []):
        key = case.get("client_index"), case.get("write_mode"), case.get("batch_keys")
        if key not in wanted or key in seen or case.get("valid") is not True or case.get("errors"):
            raise ValueError("Batch TCP audit has duplicate, invalid or unplanned cases")
        seen.add(key)
        index, mode, batch = key
        required, gate_name = batch * 1024, f"r0-datasystem/client-{index}-{mode}.json"
        if case.get("minimum_directional_bytes") != required or case.get("gate_path") != gate_name or gate_name not in raw["inputs"]:
            raise ValueError("Batch TCP audit case identity or required byte boundary differs")
        if gate_name not in clients:
            clients[gate_name] = json.loads((gate_path / gate_name).read_text())
        matches = [item for item in clients[gate_name].get("cases", []) if item.get("batch_keys") == batch]
        if len(matches) != 1 or not isinstance(matches[0].get("key_prefix"), str) or not matches[0]["key_prefix"]:
            raise ValueError("Batch TCP payload sample is missing its original exact-case key prefix")
        wire = case.get("wire", {})
        if wire.get("malformed") or set(wire.get("operations", {})) != {"put", "get"}:
            raise ValueError("Batch TCP audit must cover both directions without malformed syscalls")
        directions = {}
        for operation, observed in wire["operations"].items():
            sample, count = observed.get("payload_sample", {}), observed.get("successful_bytes")
            at = sample.get("sample_key_index")
            if (type(count) is not int or count < required or observed.get("full_payload_observed") is not True
                    or sample.get("actual_medium") != "TCP" or sample.get("sample_size") != 1024
                    or sample.get("sample_version") != 0 or type(at) is not int or not 0 <= at < batch):
                raise ValueError("Batch TCP direction lacks the required complete same-case 1KiB sample and byte lower bound")
            sample_key = matches[0]["key_prefix"] + "k" + str(at)
            if (sample.get("sample_key") != sample_key
                    or sample.get("sample_binary_sha256") != hashlib.sha256(payload(sample_key, 0, 1024)).hexdigest()
                    or sample.get("input_path") not in raw["inputs"]):
                raise ValueError("Batch TCP sample key/digest does not match its exact native gate case")
            directions[operation] = {"successful_bytes": count, "minimum_directional_bytes": required,
                                     "full_payload_observed": True, "sample_key_index": at,
                                     "sample_size": 1024, "sample_binary_sha256": sample["sample_binary_sha256"],
                                     "actual_medium": "TCP"}
        cases.append({"client_index": index, "write_mode": mode, "batch_keys": batch, "operations": directions})
    if seen != wanted:
        raise ValueError("Batch TCP audit does not cover all sixteen client/mode/batch combinations")
    return {"valid": True, "kind": raw["kind"], "cases": cases, "case_count": len(cases), "direction_count": 2 * len(cases),
            "full_evidence": {key: records[0][key] for key in ["sha256", "bytes"]} | {"path": records[0]["relative"]},
            "input_files_revalidated": len(raw["inputs"]), "parser_sources": sources,
            "scope": raw["scope"], "summary_scope": "Compact reference to the completed offline audit; exact original input/parser hashes and structural sample identities revalidated, without reparsing syscalls. One full same-case 1KiB sample and directional TCP byte lower bound do not prove every payload byte or formal-run tracing."}, records


def configuration_profile(products):
    records = products.get("products", []) if isinstance(products, dict) else products
    mooncake = next((row for row in records if row.get("name") == "mooncake"), {})
    env = mooncake.get("env", {})
    settings = []
    for key, default in [("MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER", 1024), ("MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER", 1024),
                         ("MC_TCP_LANES_PER_PEER", 4), ("MC_TCP_ADMISSION_TIMEOUT_MS", 1000)]:
        value = int(env[key]) if key in env else default
        settings.append({"key": key, "value": value, "pinned_source_default": default,
                         "origin": "explicit frozen product env" if key in env else "pinned source default"})
    adapted = settings[0]["value"] == 8192 and settings[1]["value"] == 8192 and settings[2]["value"] == 4 and settings[3]["value"] == 1000
    return {"name": "Mooncake TCP 批量容量适配（queue / pending = 8192 / 8192）" if adapted else "冻结输入中记录的原生配置",
            "capacity_adapted": adapted, "settings": settings,
            "scope": ("Mooncake queued/pending capacities are 8192; lane count 4 and admission timeout 1000ms retain pinned source defaults."
                      if adapted else "Recorded values only; this configuration does not match the reviewed 8192/8192/4/1000 batch capacity profile."),
            "source_commit": "719735896c86b56fabec6cf3e825fb2ea640597a",
            "source_location": "mooncake-transfer-engine/src/transport/tcp_transport/tcp_transport.cpp:301-338"}


def history_evidence(roots, out, namespace="history"):
    phases, records, labels = [], [], set()
    for root in roots:
        root = Path(root).resolve(strict=True)
        if root.name in labels:
            raise ValueError("History roots need distinct names")
        labels.add(root.name)
        for source in sorted(path for path in root.rglob("*") if path.suffix in {".json", ".py"}):
            relative = namespace + "/" + root.name + "/" + source.relative_to(root).as_posix()
            records.append(copy_file(source, out, relative))
            if source.name != "summary.json":
                continue
            raw = json.loads(source.read_text())
            action = raw.get("action", raw.get("kind", "unknown"))
            if action == "run" and raw.get("ended") and (raw.get("failed") or raw.get("fatal") or raw.get("valid") is False):
                action = "failed_formal_attempt"
            if action not in {"gate", "smoke", "composed_correctness_gate", "untraced_correctness_diagnostic", "mooncake_tcp_queue_capacity_diagnostic", "failed_formal_attempt"}:
                continue
            manifest_path = next((path for path in [source.parent / "products.json", root / "products.json", root / "batch-gate-001/products.json"] if path.is_file()), None)
            recorded_profile = configuration_profile(json.loads(manifest_path.read_bytes())) if manifest_path else None
            configuration = "capacity_adapted" if recorded_profile and recorded_profile["capacity_adapted"] else "default" if recorded_profile else "unrecorded"
            status = ("failed" if raw.get("failed") or raw.get("fatal") else "incomplete" if not raw.get("ended")
                      else "passed" if raw.get("valid") is True else "failed" if raw.get("valid") is False else "incomplete")
            if action == "mooncake_tcp_queue_capacity_diagnostic":
                profiles = raw.get("profiles", [])
                complete = (raw.get("ended") and raw.get("completed_and_clean") is True
                            and raw.get("completed") == ["default", "queue8192"] and len(profiles) == 2
                            and {profile.get("name") for profile in profiles} == {"default", "queue8192"}
                            and all(profile.get("cleanup_ok") is True and not profile.get("errors") for profile in profiles))
                status = "failed" if raw.get("errors") or raw.get("fatal") else "completed" if complete else "incomplete"
            prefix = ("容量机制诊断 · " if action == "mooncake_tcp_queue_capacity_diagnostic" else "正式失败历史 · " if action == "failed_formal_attempt"
                      else "容量配置历史 · " if configuration == "capacity_adapted" else "默认配置 · " if configuration == "default" else "历史 · ")
            phases.append({"identity": relative, "action": action, "status": status,
                           "duration_s": raw.get("duration_s"), "start_wall_ns": raw.get("start_wall_ns"),
                           "end_wall_ns": raw.get("end_wall_ns"), "evidence": relative,
                           "sha256": digest(source), "label": prefix + source.parent.name,
                           "configuration": configuration,
                           "performance_eligible": False, "failed": raw.get("failed", raw.get("errors", [])),
                           "diagnostic_profiles": [{key: profile.get(key) for key in ["name", "native_valid", "cleanup_ok"]}
                                                   for profile in raw.get("profiles", [])]})
    return phases, records


def failed_attempt_evidence(roots, out):
    phases, records = history_evidence(roots, out, namespace="failed-attempts")
    bindings, frozen_records = [], []
    for root in roots:
        root = Path(root).resolve(strict=True)
        summaries = []
        for source in root.rglob("summary.json"):
            raw = json.loads(source.read_bytes())
            if raw.get("action") == "run" and raw.get("ended") and (raw.get("failed") or raw.get("fatal") or raw.get("valid") is False):
                summaries.append(source)
        if not summaries:
            raise ValueError("Each --failed-attempts root must retain a terminal unsuccessful formal execution")
        for source in summaries:
            stage, inputs = verify_runtime_inputs(source.parent, root / "runtime-inputs")
            for record in inputs:
                relative = "failed-attempts/" + root.name + "/frozen-runtime/" + record["relative"]
                copied = copy_file(Path(record["source"]), out, relative)
                if any(copied[key] != record[key] for key in ["sha256", "bytes"]):
                    raise ValueError("Historical failed-attempt runtime changed during copy")
                frozen_records.append({**copied, "source": record["source"]})
            bindings.append({"name": root.name, "source_root": stage["root"], "files": len(inputs),
                             "stage_sha256": digest(source.parent / "batch-stage.json"),
                             "summary_sha256": digest(source), "performance_eligible": False,
                             "scope": "Exact original failed-attempt stage inputs, separately preserved; none are substituted into current measured runtime or performance repetitions."})
    return [row for row in phases if row["action"] == "failed_formal_attempt"], records, bindings, frozen_records


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


def verify_resource_evidence(directory, suite, analysis, scripts):
    from batch_resource_audit import markdown
    directory, suite = Path(directory).resolve(strict=True), Path(suite).resolve(strict=True)
    source, narrative = directory / "resource-audit.json", directory / "resource-audit.md"
    if any(path.is_symlink() or not path.is_file() for path in [source, narrative]):
        raise ValueError("The independent resource audit requires regular JSON and Markdown files")
    raw = json.loads(source.read_bytes())
    helper = Path(scripts) / "batch_resource_audit.py"
    if (raw.get("schema_version") != 1 or raw.get("kind") != "native_batch_collected_resource_audit"
            or Path(raw.get("suite", "")).resolve() != suite or raw.get("helper_sha256") != digest(helper)
            or raw.get("suite_valid_recorded") is not analysis["summary"].get("valid")):
        raise ValueError("Resource audit suite/source identity differs from the current evidence")
    if narrative.read_bytes() != markdown(raw).encode("utf-8"):
        raise ValueError("Resource audit Markdown does not match its original JSON")
    expected = {"resources/samples.jsonl", "summary.json"}
    expected |= {name for name in ["resources/interference.jsonl", "resources/monitor-manifest.json"] if (suite / name).exists()}
    result_paths = {path.relative_to(suite).as_posix() for path in suite.glob("r*-*/b*-*.json")}
    expected |= result_paths
    seen = set()
    for record in raw.get("inputs", []):
        name = record.get("path", "")
        relative = Path(name)
        if (name in seen or name not in expected or relative.is_absolute() or ".." in relative.parts
                or relative.as_posix() != name or name in {"", "."}):
            raise ValueError("Resource audit input inventory is unsafe or duplicated")
        seen.add(name)
        path = suite / relative
        if (path.is_symlink() or any(parent.is_symlink() for parent in path.parents if suite in parent.parents)
                or not path.is_file() or type(record.get("bytes")) is not int or path.stat().st_size != record.get("bytes") or digest(path) != record.get("sha256")):
            raise ValueError("Resource audit input SHA256/bytes mismatch: " + name)
    if seen != expected:
        raise ValueError("Resource audit input set does not match every actual sample/result source")
    windows, unsupported, unmeasured = {}, [], []
    for name in sorted(result_paths):
        result = json.loads((suite / name).read_bytes())
        if result.get("status") == "not_supported":
            unsupported.append(name)
            continue
        start, end = result.get("measure_start_mono_ns"), result.get("measure_end_mono_ns")
        if type(start) is int and type(end) is int and end > start:
            windows[name[:-5]] = result
        else:
            unmeasured.append(name)
    audited = raw.get("windows", [])
    if (len(audited) != len(windows) or {row.get("identity") for row in audited} != set(windows)
            or raw.get("sdk_window_count") != len(windows) or sorted(raw.get("unsupported", [])) != unsupported
            or raw.get("unsupported_count") != len(unsupported) or sorted(raw.get("unmeasured_results", [])) != unmeasured
            or raw.get("valid_sdk_window_count") != sum(result.get("valid") is True for result in windows.values())
            or raw.get("invalid_sdk_window_count") != sum(result.get("valid") is not True for result in windows.values())
            or raw.get("suite_monitor_exit") != analysis["summary"].get("monitor_exit")
            or raw.get("suite_interference_exit") != analysis["summary"].get("interference_exit")):
        raise ValueError("Resource audit SDK identities or N/A/unmeasured sets differ")
    for row in audited:
        result = windows[row["identity"]]
        if (row.get("start_mono_ns") != result["measure_start_mono_ns"] or row.get("end_mono_ns") != result["measure_end_mono_ns"]
                or any(row.get(target) != result.get(original) for target, original in
                       [("result_valid_recorded", "valid"), ("result_success_recorded", "success"),
                        ("result_failures_recorded", "failures"), ("result_fatal_recorded", "fatal")])):
            raise ValueError("Resource audit SDK boundary/result identity differs")
    records = [{"source": str(path), "relative": "resource-audit/" + name,
                "sha256": digest(path), "bytes": path.stat().st_size}
               for path, name in [(source, "resource-audit.json"), (narrative, "resource-audit.md"), (helper, "source/batch_resource_audit.py")]]
    return {"verified": True, "path": records[0]["relative"], "markdown": records[1]["relative"],
            "sha256": records[0]["sha256"], "bytes": records[0]["bytes"], "sdk_windows": len(windows),
            "input_files_revalidated": len(expected), "scope": raw["scope"],
            "summary_scope": "Independent read-only postprocessing of the original sample stream; no new samples, synthetic intervals or performance repetitions. Parent/monitor and host/affinity estimates retain their separate scopes."}, records


def render(args):
    cutoff = time.time_ns()
    suite, rfc = args.suite.resolve(strict=True), args.rfc.resolve(strict=True)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh empty report directory; existing deliveries are not overwritten")
    scripts = Path(__file__).resolve().parent
    source_roots = [suite, rfc, scripts] + [Path(path).resolve(strict=True) for path in getattr(args, "history", [])]
    source_roots += [Path(path).resolve(strict=True) for path in getattr(args, "failed_attempts", [])]
    source_roots += [Path(path).resolve(strict=True) for path in [getattr(args, key, None) for key in ["gate", "smoke", "builds", "environment", "runtime_inputs", "resource_audit"]] if path]
    if any(out == source or source in out.parents or out in source.parents for source in source_roots):
        raise ValueError("Report output must be separate from the measured suite")
    analysis = analyze(suite, args.device)
    if not args.allow_partial and not analysis["passed"]:
        raise RuntimeError("A completed successful independently recomputed suite is required; use --allow-partial only for an explicitly partial preview")
    if not args.allow_partial and analysis["summary"].get("action") != "run":
        raise ValueError("Only the formal run may be rendered as a completed report")
    stage, runtime_records = verify_runtime_inputs(suite, getattr(args, "runtime_inputs", None), require=not args.allow_partial)
    if runtime_records:
        record_map = {record["relative"]: record for record in runtime_records}
        if "products.json" not in record_map:
            raise ValueError("The frozen runtime must bind products.json")
        original_products = json.loads(Path(record_map["products.json"]["source"]).read_text())
        frozen_products = original_products.get("products", []) if isinstance(original_products, dict) else original_products
        actual_products = json.loads((suite / "products.json").read_text())
        actual_products = actual_products.get("products", []) if isinstance(actual_products, dict) else actual_products
        if actual_products != frozen_products or analysis["parameters"].get("root") != stage.get("root"):
            raise ValueError("Suite product/root provenance differs from the frozen runtime")
    out.mkdir(parents=True, exist_ok=True)
    export(analysis, out)
    for name in ["batch-report.js", "batch-report.css", "echarts.min.js"]:
        copy_file(scripts / name, out, name)
    docs = sorted(rfc.glob("*.md"))
    if not docs:
        raise ValueError("The batch RFC directory has no Markdown documents")
    for source in docs:
        copy_file(source, out, "docs/" + source.name)
    provenance = []
    for record in runtime_records:
        copied = copy_file(Path(record["source"]), out, "measured-runtime/" + record["relative"])
        if any(copied[key] != record[key] for key in ["sha256", "bytes"]):
            raise ValueError("Frozen runtime input changed during copy: " + record["relative"])
        provenance.append(copied)
    for name in ["parameters.json", "plan.json", "summary.json", "status.json", "products.json", "batch-stage.json", "events.jsonl"]:
        if (suite / name).exists():
            provenance.append(copy_file(suite / name, out, "provenance/" + name))
    for name in ["monitor-manifest.json", "monitor-summary.json"]:
        if (suite / "resources" / name).exists():
            provenance.append(copy_file(suite / "resources" / name, out, "provenance/resources/" + name))
    history_roots_explicit = [Path(path).resolve(strict=True) for path in getattr(args, "history", [])]
    history_phases, history_records = history_evidence(history_roots_explicit, out)
    provenance.extend(history_records)
    failed_roots = [Path(path).resolve(strict=True) for path in getattr(args, "failed_attempts", [])]
    failed_phases, failed_records, failed_bindings, failed_runtime = failed_attempt_evidence(failed_roots, out)
    history_phases.extend(failed_phases)
    provenance.extend(failed_records)
    if not args.allow_partial and not failed_bindings:
        raise ValueError("Retain the original shared-RocksDB failed formal attempt with --failed-attempts")
    builds_path = args.builds or (suite / "builds" if (suite / "builds").is_dir() else suite.parent / "builds")
    builds = []
    if builds_path.is_dir():
        for source in sorted(builds_path.glob("*.json")):
            copy_file(source, out, "builds/" + source.name)
            builds.append({**json.loads(source.read_text()), "evidence": "builds/" + source.name})
            if stage and (name := "builds/" + source.name) in stage["inputs"] and digest(source) != stage["inputs"][name]["sha256"]:
                raise ValueError("Build record differs from the frozen stage: " + source.name)
    if not args.allow_partial and (len(builds) != 3 or any(build.get("build_exit") != 0 for build in builds)):
        raise ValueError("The three successful original build records are required")
    gate = None
    gate_verification = None
    datasystem_isolation = None
    history_roots = []
    if args.gate:
        gate_path = args.gate.resolve(strict=True)
        for source in sorted(path for path in gate_path.rglob("*") if path.suffix in {".json", ".flags"}):
            if source.is_symlink():
                raise ValueError("Unexpected gate evidence symlink")
            provenance.append(copy_file(source, out, "gate/" + source.relative_to(gate_path).as_posix()))
        if (gate_path / "summary.json").exists():
            gate = json.loads((gate_path / "summary.json").read_text())
        if stage:
            gate, gate_verification = verify_gate_evidence(gate_path, stage, json.loads((suite / "products.json").read_text()))
            try:
                datasystem_isolation = verify_datasystem_isolation(gate_path)
            except (OSError, ValueError, KeyError) as error:
                if not args.allow_partial:
                    raise
                datasystem_isolation = {"verified": False, "error": str(error)}
            for history in gate_verification["histories"]:
                root = Path(history["directory"])
                history_roots.append(root)
                for source in sorted(root.rglob("*.json")):
                    provenance.append(copy_file(source, out, "gate-history/" + root.name + "/" + source.relative_to(root).as_posix()))
            (out / "gate-verification.json").write_text(json.dumps(gate_verification, ensure_ascii=False, indent=2) + "\n")
    if not args.allow_partial and not gate_verification:
        raise ValueError("Successful batch gate with independently revalidated source evidence is required")
    tcp_verification, tcp_records = None, []
    if getattr(args, "tcp_verification", None):
        if not args.gate:
            raise ValueError("The batch TCP payload audit requires its original --gate")
        tcp_verification, tcp_records = verify_tcp_evidence(args.tcp_verification, args.gate, scripts)
        for record in tcp_records:
            copied = copy_file(Path(record["source"]), out, record["relative"])
            if any(copied[key] != record[key] for key in ["sha256", "bytes"]):
                raise ValueError("Batch TCP proof/source changed during copy")
            provenance.append(copied)
    elif not args.allow_partial:
        raise ValueError("A completed same-gate --tcp-verification JSON is required")
    smoke_path = getattr(args, "smoke", None) or suite.parent / "batch-smoke-001"
    smoke = None
    if smoke_path.is_dir():
        smoke_path = smoke_path.resolve(strict=True)
        smoke_analysis = analyze(smoke_path, args.device)
        if (smoke_analysis["summary"].get("action") != "smoke" or not smoke_analysis["passed"]
                or json.loads((smoke_path / "batch-stage.json").read_text()) != stage
                or json.loads((smoke_path / "products.json").read_text()) != json.loads((suite / "products.json").read_text())):
            raise ValueError("The C8 smoke must independently pass under the same frozen stage/products")
        smoke = smoke_analysis["summary"]
        for source in sorted(smoke_path.rglob("*.json")):
            provenance.append(copy_file(source, out, "smoke/" + source.relative_to(smoke_path).as_posix()))
    elif not args.allow_partial:
        raise ValueError("The completed same-stage C8 smoke evidence is required")
    if args.environment:
        for source in sorted(args.environment.resolve(strict=True).glob("*.json")):
            provenance.append(copy_file(source, out, "environment/" + source.name))
    resource_audit, resource_records = None, []
    if getattr(args, "resource_audit", None):
        resource_audit, resource_records = verify_resource_evidence(args.resource_audit, suite, analysis, scripts)
        for record in resource_records:
            copied = copy_file(Path(record["source"]), out, record["relative"])
            if any(copied[key] != record[key] for key in ["sha256", "bytes"]):
                raise ValueError("Independent resource audit/source changed during copy")
            provenance.append(copied)
    elif not args.allow_partial:
        raise ValueError("A completed independent --resource-audit directory is required")
    for row in analysis["rows"]:
        source = suite / (row["identity"] + ".json")
        if source.exists():
            copy_file(source, out, row["evidence"])
    for epoch in analysis.get("epochs", []):
        relative = Path(epoch["evidence"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "results":
            raise ValueError("Unsafe service epoch evidence path")
        copy_file(suite / Path(*relative.parts[1:]), out, relative.as_posix())
    payload = compact_payload(analysis)
    profile = configuration_profile(json.loads((suite / "products.json").read_text()))
    if not args.allow_partial and not profile["capacity_adapted"]:
        raise ValueError("This final report requires the reviewed frozen 8192/8192/4/1000 Mooncake batch capacity profile")
    if not args.allow_partial and profile["capacity_adapted"] and not {"gate", "smoke"} <= {row["action"] for row in history_phases if row["status"] == "failed"}:
        raise ValueError("The capacity-adapted matrix must retain the original default gate and default C8 smoke failures via --history")
    payload.update(builds=builds, gate=gate, gate_verification=gate_verification, provenance=provenance,
                   configuration_profile=profile, history_phases=history_phases, smoke=smoke,
                   tcp_verification=tcp_verification,
                   datasystem_isolation=datasystem_isolation, failed_attempts=failed_bindings,
                   resource_audit=resource_audit,
                   publication_eligible=not args.allow_partial and analysis["passed"] and analysis["summary"].get("action") == "run",
                   runtime_binding={"verified": bool(runtime_records), "files": len(runtime_records),
                                    "scope": "Only exact stage.inputs files are archived under measured-runtime; current report/analysis tools are separate."},
                   report_scope="Independent native batch TCP matrix; the prior single-key reports and measurements are unchanged.")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (out / "data.json").write_text(encoded)
    (out / "data.js").write_text("window.KVC_BATCH_DATA=" + encoded.replace("</", "<\\/") + ";\n")
    for name in ["data.json", "data.js", "analysis.json"]:
        if (out / name).stat().st_size >= 20 << 20:
            raise RuntimeError(name + " exceeds the reviewed 20MiB public JSON/script budget; preserve raw evidence separately")
    if args.operation_log:
        (out / "operations.jsonl").write_bytes(operation_ledger(args.operation_log, cutoff, "batch_report"))
    if args.raw_evidence:
        if not analysis["summary"].get("ended"):
            raise RuntimeError("Raw evidence packaging requires a stopped suite")
        with tarfile.open(out / "raw-evidence.tar.gz", "w:gz", compresslevel=6) as archive:
            for root, name in [(suite, suite.name)] + ([(args.gate, args.gate.name)] if args.gate else []) + ([(smoke_path, smoke_path.name)] if smoke else []) + [(root, root.name) for root in history_roots] + [(root, "history/" + root.name) for root in history_roots_explicit] + [(root, "failed-attempts/" + root.name) for root in failed_roots]:
                for source in sorted(root.rglob("*")):
                    if source.is_symlink():
                        raise RuntimeError("Raw evidence must not follow symlinks")
                    if source.is_file():
                        archive.add(source, arcname=(Path(name) / source.relative_to(root)).as_posix(), recursive=False)
            for record in tcp_records + resource_records:
                archive.add(record["source"], arcname="audits/" + record["relative"], recursive=False)
    template = (scripts / "batch-report.html").read_text()
    count = analysis["counts"]
    status = ("部分预览 · " + str(analysis["summary"].get("action", "unknown")) + " · " + ("源阶段重算通过" if analysis["passed"] else "有未完成或无效证据")) if args.allow_partial else "矩阵完成 · 重算通过"
    build_rows = []
    for build in builds:
        cells = [build.get(key, "未提供") for key in ["product", "tag", "sha", "python"]]
        cells.append(str(build.get("build_exit", "未提供")) + " / " + str(build.get("smoke_exit", "未提供")))
        build_rows.append("<tr>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in cells)
                          + '<td><a href="' + html.escape(build["evidence"], quote=True) + '">原始记录 ↗</a></td></tr>')
    if not builds:
        build_rows.append('<tr><td colspan="6">未提供构建复用记录；不据此声称重新构建或核验。</td></tr>')
    gate_text = ('<a href="gate/summary.json">' + ("组合批量正确性证据" if gate.get("action") == "composed_correctness_gate" else "本配置完整批量正确性门禁") + ' ↗</a>（' + ("通过" if gate.get("valid") is True else "未通过") + ("；组合本身不是重新执行）" if gate.get("action") == "composed_correctness_gate" else "；十个真实客户端 / 写模式执行）")) if gate else "尚未提供批量门禁记录。"
    now = datetime.datetime.now(datetime.timezone.utc)
    replacements = {
        "{{STATUS}}": status, "{{COUNTS}}": f"{count.get('valid', 0)} 有效 · {count.get('not_supported', 0)} N/A · {count.get('invalid', 0)} 无效 · {count.get('missing', 0)} 缺失",
        "{{REPEATS}}": str(analysis["repeats"]), "{{BUILD_ROWS}}": "".join(build_rows), "{{GATE}}": gate_text,
        "{{GATE_HISTORY}}": f"主矩阵无效 {count.get('invalid', 0)} 行 · 正式尝试历史失败 {sum(row['action'] == 'failed_formal_attempt' for row in history_phases)} 轮 · 历史 gate / C8 smoke 失败 {sum(row['status'] == 'failed' and row['action'] in {'gate', 'smoke'} for row in history_phases)} 轮",
        "{{PROFILE_NAME}}": html.escape(profile["name"]),
        "{{TCP_PROOF}}": ('<a href="datasystem-batch-tcp-verification.json">DataSystem 批量 TCP 完整审计 ↗</a>：16 组合 / 32 方向，逐方向成功 TCP 字节 ≥ B × 1024（含 RPC 开销），并捕获同批某 key 的完整 1 KiB 样本；不等于逐字节证明整批载荷或持续跟踪正式测量。' if tcp_verification else '尚未提供本批量门禁的 TCP payload 审计；不以旧单 key 证明替代。'),
        "{{DS_ISOLATION}}": ('<b>本配置已核对两个 Worker 的独立 RocksDB 目录。</b>' + ''.join('<p class="small">Worker ' + str(row["worker_index"]) + '：<a href="' + row["evidence"] + '">原始旗标 ↗</a> <span class="path">' + html.escape(row["rocksdb_store_dir"]) + '</span></p>' for row in datasystem_isolation["workers"])) if datasystem_isolation and datasystem_isolation.get("verified") else '尚无两个 Worker 独立 RocksDB 目录的完整门禁证明；预览不具备公平部署结论。',
        "{{RESOURCE_AUDIT}}": ('<a href="resource-audit/resource-audit.md">独立资源审计 ↗</a> · <a href="resource-audit/resource-audit.json">完整 JSON ↗</a>：核对原始采样与 SDK 窗口，保留 CPU throttle、内存事件、swap、parent/monitor 和外部负载估计的独立口径，不新增采样或性能重复。' if resource_audit else '尚未提供独立资源审计。'),
        "{{CONFIG_ROWS}}": "".join('<tr><td>' + html.escape(row["key"]) + '</td><td>' + str(row["pinned_source_default"]) + '</td><td>' + str(row["value"]) + '</td><td>' + html.escape(row["origin"]) + '</td></tr>' for row in profile["settings"]),
        "{{CREATED}}": "生成 " + now.astimezone(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "{{GENERATED}}": html.escape(now.isoformat()),
        "{{RAW_DOWNLOAD}}": '<a href="raw-evidence.tar.gz" download>完整运行、资源与门禁原始日志</a>' if args.raw_evidence else "",
        "{{OPERATIONS}}": '<a href="operations.jsonl" download>编排操作耗时 JSONL</a>' if args.operation_log else "",
        "{{DOC_LINKS}}": "".join('<a href="docs/' + html.escape(source.name, quote=True) + '">' + html.escape(source.stem) + " ↗</a>" for source in docs),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    if re.search(r"\{\{[A-Z_]+\}\}", template):
        raise RuntimeError("Unresolved report template placeholder")
    archive = reproduction_archive(scripts, rfc, out)
    with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as zipped:
        for directory in ["builds", "provenance", "gate", "smoke", "gate-history", "history", "failed-attempts", "environment", "measured-runtime", "tcp-proof-sources", "resource-audit", "results"]:
            for source in sorted((out / directory).rglob("*")) if (out / directory).exists() else []:
                if source.is_file():
                    zipped.write(source, "kvc-fair/" + source.relative_to(out).as_posix())
        if tcp_verification:
            zipped.write(out / "datasystem-batch-tcp-verification.json", "kvc-fair/datasystem-batch-tcp-verification.json")
        for record in tcp_records + resource_records:
            packaged = zipped.read("kvc-fair/" + record["relative"])
            if len(packaged) != record["bytes"] or hashlib.sha256(packaged).hexdigest() != record["sha256"]:
                raise ValueError("Packaged batch TCP proof/source bytes changed")
        for record in runtime_records:
            packaged = zipped.read("kvc-fair/measured-runtime/" + record["relative"])
            if len(packaged) != record["bytes"] or hashlib.sha256(packaged).hexdigest() != record["sha256"]:
                raise ValueError("Packaged measured runtime changed: " + record["relative"])
        for record in failed_runtime:
            packaged = zipped.read("kvc-fair/" + record["path"])
            if len(packaged) != record["bytes"] or hashlib.sha256(packaged).hexdigest() != record["sha256"]:
                raise ValueError("Packaged historical failed-attempt stage input changed")
        if zipped.testzip() is not None or len(zipped.namelist()) != len(set(zipped.namelist())):
            raise RuntimeError("Invalid or duplicate reproduction ZIP entries")
    (out / "index.html").write_bytes(version_zip_href(template, digest(archive)).encode("utf-8"))
    finalize_artifacts(out)
    return {"out": str(out), "passed": analysis["passed"], "counts": count,
            "publication_eligible": payload["publication_eligible"],
            "issues": len(analysis["issues"]), "samples_embedded": len(payload["samples"]),
            "data_bytes": (out / "data.json").stat().st_size, "zip_sha256": digest(archive),
            "scope": "Fresh independent report; no service, workload, old result, or remote mutation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", "--output", dest="out", type=Path, required=True)
    parser.add_argument("--rfc", type=Path, required=True)
    parser.add_argument("--builds", type=Path)
    parser.add_argument("--gate", type=Path)
    parser.add_argument("--smoke", type=Path)
    parser.add_argument("--tcp-verification", type=Path)
    parser.add_argument("--resource-audit", type=Path)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--runtime-inputs", type=Path)
    parser.add_argument("--history", type=Path, action="append", default=[])
    parser.add_argument("--failed-attempts", type=Path, action="append", default=[])
    parser.add_argument("--operation-log", type=Path, action="append", default=[])
    parser.add_argument("--device", default="253:2")
    parser.add_argument("--raw-evidence", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    print(json.dumps(render(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
