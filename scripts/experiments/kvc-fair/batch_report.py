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
from kvcbench.collect.audits.postprocessing import verify_deployment_evidence, verify_resource_evidence
from kvcbench.collect.audits.gate_inputs import verify_gate_evidence, verify_tcp_evidence
from kvcbench.collect.audits.runtime import verify_datasystem_isolation, verify_runtime_inputs


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            checksum.update(block)
    return checksum.hexdigest()


def verify_tar_inputs(path, inputs):
    seen, checked = set(), set()
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            name = member.name
            if name in seen:
                raise ValueError("Raw evidence contains duplicate archive paths")
            seen.add(name)
            if name not in inputs:
                continue
            expected = inputs[name]
            if (not member.isfile() or member.size != expected["bytes"]
                    or "mode" in expected and member.mode & 0o777 != expected["mode"]):
                raise ValueError("Packaged deployment input has different bytes: " + name)
            checksum = hashlib.sha256()
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(8 << 20), b""):
                    checksum.update(block)
            if checksum.hexdigest() != expected["sha256"]:
                raise ValueError("Packaged deployment input SHA256 mismatch: " + name)
            checked.add(name)
    if checked != set(inputs):
        raise ValueError("Raw evidence omits deployment audit inputs")
    return len(checked)


def copy_file(source, out, relative):
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Expected regular evidence file: " + str(source))
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": relative, "sha256": digest(target), "bytes": target.stat().st_size}


def verify_source_build_provenance(stage, runtime_records, builds):
    from build_metadata import PRODUCTS
    from kvcbench.collect.audits.runtime import verify_source_build_provenance as verify
    return verify(stage, runtime_records, builds, products=PRODUCTS)


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


def report_completion(analysis):
    summary, rows = analysis.get("summary", {}), analysis.get("rows", [])
    failures = [row for row in rows if row.get("status") == "failed"]
    wanted = {(repeat, product, batch, operation) for repeat in range(3)
              for product in ["datasystem", "mooncake", "lmcache"] for batch in [1, 16, 256, 4096]
              for operation in ["put", "upsert", "get", "exist_hit", "exist_miss"]}
    identities = [(row.get("repeat"), row.get("product"), row.get("batch"), row.get("operation")) for row in rows]
    row_errors = any(row.get("status") not in {"valid", "failed", "not_supported"}
                     or (row.get("status") == "valid" and row.get("valid") is not True)
                     or (row.get("status") == "failed" and (row.get("valid") is not False
                         or row.get("evidence_verified") is not True or not row.get("native_errors")
                         or row.get("operation") not in {"get", "exist_hit", "exist_miss"})) for row in rows)
    na = [row for row in rows if row.get("status") == "not_supported"]
    counts = {status: sum(row.get("status") == status for row in rows)
              for status in ["valid", "failed", "not_supported", "invalid", "missing"]}
    declared = analysis.get("counts", {})
    terminal = (summary.get("action") == "run" and summary.get("ended") and not summary.get("failed")
                and not summary.get("fatal") and (summary.get("execution_complete") is True or summary.get("valid") is True)
                and summary.get("valid") is (not failures)
                and summary.get("measurement_failures", []) == [row.get("identity") for row in failures])
    complete = bool((analysis.get("evidence_verified") is True or analysis.get("passed") is True)
                    and not analysis.get("issues") and analysis.get("repeats") == 3 and terminal and not row_errors
                    and len(identities) == len(wanted) and set(identities) == wanted
                    and counts["valid"] + counts["failed"] == 150 and counts["not_supported"] == 30
                    and all(declared.get(key, 0) == value for key, value in counts.items())
                    and all(row.get("product") == "lmcache" and (row.get("operation") == "upsert"
                            or row.get("batch") != 1 and row.get("operation") in {"put", "get"}) for row in na))
    return {"complete": complete, "native_failures": failures, "counts": counts}


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
    pure_kv = ({row.get("name") for row in records} == {"datasystem", "mooncake", "lmcache"}
               and all(row.get("env", {}).get("KVC_FEATURE_PROFILE") == "pure-kv" for row in records))
    return {"name": ("纯 KV 内存 · " if pure_kv else "") + ("Mooncake TCP 批量容量适配（queue / pending = 8192 / 8192）" if adapted else "冻结输入中记录的原生配置"),
            "feature_profile": "pure-kv" if pure_kv else "legacy-isolated",
            "capacity_adapted": adapted, "settings": settings,
            "scope": ("Mooncake queued/pending capacities are 8192; lane count 4 and admission timeout 1000ms retain pinned source defaults."
                      if adapted else "Recorded values only; this configuration does not match the reviewed 8192/8192/4/1000 batch capacity profile."),
            "source_commit": "719735896c86b56fabec6cf3e825fb2ea640597a",
            "source_location": "mooncake-transfer-engine/src/transport/tcp_transport/tcp_transport.cpp:301-338"}


def deadline_diagnostic_cases(source, relative):
    source = Path(source)
    raw = json.loads(source.read_bytes())
    if (raw.get("schema_version") != 1 or raw.get("action") != "deadline_lifetime_diagnostic"
            or raw.get("benchmark_eligible") is not False or raw.get("diagnostic_complete") is not True):
        raise ValueError("Only a completed, explicitly non-benchmark deadline diagnostic can be summarized")
    directory = source.parent
    artifacts = raw.get("artifacts", [])
    seen = set()
    for record in artifacts:
        name = record.get("path", "")
        path = Path(name)
        if (not name or path.is_absolute() or ".." in path.parts or path.as_posix() != name or name in seen):
            raise ValueError("Unsafe or duplicate diagnostic artifact path")
        seen.add(name)
        actual = directory / path
        if (any(part.is_symlink() for part in [actual, *actual.parents]) or not actual.is_file()
                or actual.stat().st_size != record.get("bytes") or digest(actual) != record.get("sha256")):
            raise ValueError("Deadline diagnostic artifact bytes differ from its recorded execution")
    helper = directory / "executed-helper.py"
    if "executed-helper.py" not in seen or digest(helper) != raw.get("helper_sha256"):
        raise ValueError("Deadline diagnostic must preserve its actual executed helper source")
    cases = raw.get("cases", [])
    if len(cases) != 4 or {row.get("case") for row in cases} != set("ABCD"):
        raise ValueError("Deadline diagnostic requires four distinct original control cases")
    result, shared = [], None
    for row in sorted(cases, key=lambda value: value["case"]):
        name = "case-" + row["case"] + ".json"
        path = directory / name
        if (name not in seen or Path(row.get("child_path", "")).name != name or digest(path) != row.get("child_sha256")
                or row.get("returncode") != 0 or row.get("reaped") is not True or row.get("complete") is not True):
            raise ValueError("Deadline diagnostic child receipt/result identity is incomplete")
        child = json.loads(path.read_bytes())
        steps = child.get("steps", [])
        exists = [step for step in steps if step.get("name") == "exists"]
        failed = [step for step in steps if step.get("status") == "failed"]
        got = [step for step in steps if step.get("name") == "get-full"]
        pairs = child.get("keys"), child.get("key_payload_sha256")
        keys, hashes = pairs
        if (not isinstance(keys, list) or len(keys) != 16 or any(not isinstance(key, str) or not key for key in keys)
                or len(set(keys)) != 16 or not isinstance(hashes, dict) or set(hashes) != set(keys)
                or any(hashes[key] != hashlib.sha256(hashlib.shake_256(("ds-deadline-v1\0" + key).encode()).digest(1024)).hexdigest() for key in keys)
                or child.get("metadata", {}).get("source_sha") != "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6"
                or child.get("parameters", {}).get("value_size") != 1024
                or child.get("parameters", {}).get("batch") != (1 if row["case"] == "D" else 16)
                or len(got) != (0 if row["case"] == "A" else 1)):
            raise ValueError("Deadline diagnostic case source, batch, keys or full payload identities differ")
        if shared is None:
            shared = pairs
        if (pairs != shared or child.get("case") != row["case"] or child.get("benchmark_eligible") is not False
                or child.get("complete") is not True or child.get("setup_error") or not exists
                or child.get("all_sdk_steps_passed") is not (not failed)
                or row.get("all_sdk_steps_passed") != child["all_sdk_steps_passed"]):
            raise ValueError("Deadline control cases do not preserve the same keys/payloads and honest SDK outcomes")
        for step in steps:
            start, end = step.get("start_mono_ns"), step.get("end_mono_ns")
            if (type(start) is not int or type(end) is not int or end < start
                    or type(step.get("duration_s")) not in {int, float}
                    or not abs(step["duration_s"] - (end - start) / 1e9) <= 1e-9):
                raise ValueError("Deadline diagnostic step timing is inconsistent")
        parameters = child["parameters"]
        result.append({"case": row["case"], "description": child.get("description"), "batch": parameters["batch"],
                       "sleep_s": parameters.get("sleep_s"), "exist_elapsed_s": child.get("exist_elapsed_s"),
                       "exists_calls": len(exists), "all_sdk_steps_passed": child["all_sdk_steps_passed"],
                       "first_failure_after_get_s": (failed[0]["end_mono_ns"] - got[0]["end_mono_ns"]) / 1e9 if failed and got else None,
                       "errors": [{"step": step["name"], "message": step.get("error", {}).get("message")} for step in failed],
                       "observed_tcp_send_lines": row.get("wire", {}).get("successful_send_lines"),
                       "wire_scope": row.get("wire", {}).get("scope"),
                       "evidence": (Path(relative).parent / name).as_posix(), "sha256": digest(path)})
    return result


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
            if action == "unknown" and source.parent.name.startswith("deadline-diagnostic-") and raw.get("benchmark_eligible") is False and "diagnostic_complete" in raw and "commands" in raw:
                action = "datasystem_deadline_orchestration"
            if action == "run" and raw.get("ended") and (raw.get("failed") or raw.get("fatal") or raw.get("valid") is False):
                action = "failed_formal_attempt"
            if action not in {"gate", "smoke", "composed_correctness_gate", "untraced_correctness_diagnostic", "mooncake_tcp_queue_capacity_diagnostic", "failed_formal_attempt", "deadline_lifetime_diagnostic", "datasystem_deadline_orchestration"}:
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
            if action in {"deadline_lifetime_diagnostic", "datasystem_deadline_orchestration"}:
                status = "failed" if raw.get("errors") or raw.get("fatal") else "completed" if raw.get("diagnostic_complete") is True else "incomplete"
            controls = deadline_diagnostic_cases(source, relative) if action == "deadline_lifetime_diagnostic" and raw.get("diagnostic_complete") is True else []
            prefix = ("DataSystem期限诊断 · " if action in {"deadline_lifetime_diagnostic", "datasystem_deadline_orchestration"} else "容量机制诊断 · " if action == "mooncake_tcp_queue_capacity_diagnostic" else "正式失败历史 · " if action == "failed_formal_attempt"
                      else "容量配置历史 · " if configuration == "capacity_adapted" else "默认配置 · " if configuration == "default" else "历史 · ")
            phases.append({"identity": relative, "action": action, "status": status,
                           "duration_s": raw.get("duration_s"), "start_wall_ns": raw.get("start_wall_ns"),
                           "end_wall_ns": raw.get("end_wall_ns"), "evidence": relative,
                           "sha256": digest(source), "label": prefix + source.parent.name,
                           "source_collection": root.name,
                           "configuration": configuration,
                           "performance_eligible": False, "failed": raw.get("failed", raw.get("errors", [])),
                           "diagnostic_cases": controls, "all_sdk_steps_passed": raw.get("all_sdk_steps_passed"),
                           "diagnostic_profiles": [{key: profile.get(key) for key in ["name", "native_valid", "cleanup_ok"]}
                                                   for profile in raw.get("profiles", [])]})
    return phases, records


def failed_attempt_evidence(roots, out):
    phases, records = history_evidence(roots, out, namespace="failed-attempts")
    bindings, frozen_records, selected = [], [], set()
    for root in roots:
        root = Path(root).resolve(strict=True)
        summaries = []
        for source in root.glob("*/summary.json"):
            raw = json.loads(source.read_bytes())
            if raw.get("action") == "run" and raw.get("ended") and (raw.get("failed") or raw.get("fatal") or raw.get("valid") is False):
                summaries.append(source)
        if not summaries:
            raise ValueError("Each --failed-attempts root must retain a terminal unsuccessful formal execution")
        for source in summaries:
            selected.add("failed-attempts/" + root.name + "/" + source.relative_to(root).as_posix())
            stage, inputs = verify_runtime_inputs(source.parent, root / "runtime-inputs")
            for record in inputs:
                relative = "failed-attempts/" + root.name + "/frozen-runtime/" + record["relative"]
                copied = copy_file(Path(record["source"]), out, relative)
                if any(copied[key] != record[key] for key in ["sha256", "bytes"]):
                    raise ValueError("Historical failed-attempt runtime changed during copy")
                if "mode" in record and (out / relative).stat().st_mode & 0o777 != record["mode"]:
                    raise ValueError("Historical frozen runtime mode changed during copy")
                if "mode" in record:
                    copied["mode"] = record["mode"]
                frozen_records.append({**copied, "source": record["source"]})
            bindings.append({"name": root.name, "source_root": stage["root"], "files": len(inputs),
                             "stage_sha256": digest(source.parent / "batch-stage.json"),
                             "summary_sha256": digest(source), "performance_eligible": False,
                             "scope": "Exact original failed-attempt stage inputs, separately preserved; none are substituted into current measured runtime or performance repetitions."})
    return [row for row in phases if row["action"] == "failed_formal_attempt" and row["evidence"] in selected], records, bindings, frozen_records


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
    if getattr(args, "deployment_audit", None):
        source_roots.append(Path(args.deployment_audit).resolve(strict=True))
    if any(out == source or source in out.parents or out in source.parents for source in source_roots):
        raise ValueError("Report output must be separate from the measured suite")
    analysis = analyze(suite, args.device)
    completion = report_completion(analysis)
    if not args.allow_partial and not completion["complete"]:
        raise RuntimeError("A complete independently verified matrix is required; native read-only failures need their original outcome evidence, while missing/invalid/fatal executions remain partial")
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
        if "mode" in record and (out / copied["path"]).stat().st_mode & 0o777 != record["mode"]:
            raise ValueError("Frozen runtime input mode changed during copy: " + record["relative"])
        if "mode" in record:
            copied["mode"] = record["mode"]
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
    source_builds = verify_source_build_provenance(stage, runtime_records, builds)
    if not args.allow_partial and not source_builds and not failed_bindings:
        raise ValueError("Retain the original shared-RocksDB failed formal attempt with --failed-attempts")
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
    deployment_audit, deployment_records = None, []
    if getattr(args, "deployment_audit", None):
        deployment_audit, deployment_records = verify_deployment_evidence(args.deployment_audit, suite, args.runtime_inputs, scripts)
        for record in deployment_records:
            copied = copy_file(Path(record["source"]), out, record["relative"])
            if any(copied[key] != record[key] for key in ["sha256", "bytes"]):
                raise ValueError("Independent deployment audit/executed-source changed during copy")
            provenance.append(copied)
    elif not args.allow_partial:
        raise ValueError("A completed same-stage --deployment-audit JSON is required")
    for row in analysis["rows"]:
        source = suite / (row["identity"] + ".json")
        if source.exists():
            copy_file(source, out, row["evidence"])
        if row["status"] == "failed" and row.get("evidence_verified") is True:
            relative = Path(row["identity"].split("/")[0]) / "case-outcomes" / (source.stem + ".json")
            row["outcome_evidence"] = "results/" + relative.as_posix()
            copy_file(suite / relative, out, row["outcome_evidence"])
    for epoch in analysis.get("epochs", []):
        relative = Path(epoch["evidence"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "results":
            raise ValueError("Unsafe service epoch evidence path")
        copy_file(suite / Path(*relative.parts[1:]), out, relative.as_posix())
    payload = compact_payload(analysis)
    profile = configuration_profile(json.loads((suite / "products.json").read_text()))
    if not args.allow_partial and not profile["capacity_adapted"]:
        raise ValueError("This final report requires the reviewed frozen 8192/8192/4/1000 Mooncake batch capacity profile")
    if stage and stage.get("feature_profile") == "pure-kv" and profile["feature_profile"] != "pure-kv":
        raise ValueError("All three frozen product environments must declare the same pure-KV feature profile")
    if not args.allow_partial and not source_builds and profile["capacity_adapted"] and not {"gate", "smoke"} <= {row["action"] for row in history_phases if row["status"] == "failed"}:
        raise ValueError("The capacity-adapted matrix must retain the original default gate and default C8 smoke failures via --history")
    payload.update(builds=builds, gate=gate, gate_verification=gate_verification, provenance=provenance,
                   configuration_profile=profile, history_phases=history_phases, smoke=smoke, source_build_provenance=source_builds,
                   tcp_verification=tcp_verification,
                   datasystem_isolation=datasystem_isolation, failed_attempts=failed_bindings,
                   resource_audit=resource_audit, deployment_audit=deployment_audit,
                   publication_eligible=not args.allow_partial and completion["complete"],
                   completion_scope="A complete evidence matrix may contain verified native read-only failures. Passed remains false for those runs; failed rates and partial windows do not enter successful performance aggregates.",
                   runtime_binding={"verified": bool(runtime_records), "files": len(runtime_records),
                                    "scope": "Only exact stage.inputs files are archived under measured-runtime; current report/analysis tools are separate. Optional frozen modes describe collected runtime files and are retained in ZIP/raw members; ordinary public mirrors are normalized to 0644."},
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
            roots_seen = set()
            for root, name in [(suite, suite.name)] + ([(args.gate, args.gate.name)] if args.gate else []) + ([(smoke_path, smoke_path.name)] if smoke else []) + [(root, root.name) for root in history_roots] + [(root, "history/" + root.name) for root in history_roots_explicit] + [(root, "failed-attempts/" + root.name) for root in failed_roots] + ([(suite.parent / "run", "run")] if runtime_records else []):
                identity = (root.resolve(strict=True), name)
                if identity in roots_seen:
                    continue
                roots_seen.add(identity)
                for source in sorted(root.rglob("*")):
                    if source.is_symlink():
                        raise RuntimeError("Raw evidence must not follow symlinks")
                    if source.is_file() or source.is_dir():
                        archive.add(source, arcname=(Path(name) / source.relative_to(root)).as_posix(), recursive=False)
            if runtime_records:
                archive.add(suite.parent / "batch-stage.json", arcname="batch-stage.json", recursive=False)
                for record in runtime_records:
                    archive.add(record["source"], arcname="runtime-inputs/" + record["relative"], recursive=False)
            for record in tcp_records + resource_records + deployment_records:
                archive.add(record["source"], arcname="audits/" + record["relative"], recursive=False)
        if runtime_records or tcp_records or resource_records or deployment_records:
            archive_inputs = {}
            def bind(name, expected):
                value = {key: expected[key] for key in ["sha256", "bytes", "mode"] if key in expected}
                if name in archive_inputs and any(archive_inputs[name][key] != value[key] for key in archive_inputs[name].keys() & value.keys()):
                    raise ValueError("Audits disagree on the same raw input: " + name)
                archive_inputs[name] = {**archive_inputs.get(name, {}), **value}
            if deployment_audit:
                for name, record in json.loads((out / "deployment-audit/deployment-audit.json").read_bytes())["inputs"].items():
                    bind(name, record)
            for record in runtime_records:
                bind("runtime-inputs/" + record["relative"], record)
            for record in tcp_records + resource_records + deployment_records:
                bind("audits/" + record["relative"], record)
            if tcp_verification:
                for name, record in json.loads((out / "datasystem-batch-tcp-verification.json").read_bytes())["inputs"].items():
                    bind(args.gate.name + "/" + name, record)
            if resource_audit:
                for record in json.loads((out / "resource-audit/resource-audit.json").read_bytes())["inputs"]:
                    bind(suite.name + "/" + record["path"], record)
            verify_tar_inputs(out / "raw-evidence.tar.gz", archive_inputs)
    template = (scripts / "batch-report.html").read_text()
    count = analysis["counts"]
    status = ("部分预览 · " + str(analysis["summary"].get("action", "unknown")) + " · " + ("源阶段重算通过" if analysis["passed"] else "有未完成或原生失败证据")) if args.allow_partial else ("证据矩阵完整 · 含原生接口失败" if completion["native_failures"] else "矩阵完成 · 重算通过")
    build_rows = []
    for build in builds:
        cells = [build.get(key, "未提供") for key in ["product", "tag", "sha", "python"]]
        old_smoke = "未记录（null）" if "smoke_exit" in build and build["smoke_exit"] is None else str(build.get("smoke_exit", "未提供"))
        cells.append(str(build.get("build_exit", "未提供")) + " / " + old_smoke)
        build_rows.append("<tr>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in cells)
                          + '<td><a href="' + html.escape(build["evidence"], quote=True) + '">原始记录 ↗</a></td></tr>')
    if not builds:
        build_rows.append('<tr><td colspan="6">未提供构建复用记录；不据此声称重新构建或核验。</td></tr>')
    gate_text = ('<a href="gate/summary.json">' + ("组合批量正确性证据" if gate.get("action") == "composed_correctness_gate" else "本配置完整批量正确性门禁") + ' ↗</a>（' + ("通过" if gate.get("valid") is True else "未通过") + ("；组合本身不是重新执行）" if gate.get("action") == "composed_correctness_gate" else "；十个真实客户端 / 写模式执行）")) if gate else "尚未提供批量门禁记录。"
    pure_kv = profile["feature_profile"] == "pure-kv"
    directory_label = "状态目录（rocksdb_store_dir）" if pure_kv else "RocksDB 目录"
    feature_note = ('<div class="notice small"><b>本矩阵冻结的精简配置：关闭 RocksDB 数据写入和 Stream Cache sockets。</b><details><summary>三产品关闭项与必要后台成本</summary>DataSystem 关闭 RocksDB 数据写入、Stream Cache sockets、spill、性能跟踪与采样；独立状态目录仍可保存 eviction-policy/worker-state.pb。Mooncake 关闭 store/TE metrics、master snapshot/restore、disk eviction、offload 与 KV events；LMCache 使用 noop eviction，关闭 observability、metrics、logging、KV event log 与 coordinator reporting。必要内存元数据、心跳与校验保留；Mooncake 无关闭旗标的 admin listener 与后台循环仍存在。配置和源码边界见文档，未测量这些开关各自的性能收益。</details></div>' if pure_kv else '')
    now = datetime.datetime.now(datetime.timezone.utc)
    replacements = {
        "{{STATUS}}": status, "{{COUNTS}}": f"{count.get('valid', 0)} 有效 · {count.get('failed', 0)} 原生失败 · {count.get('not_supported', 0)} N/A · {count.get('invalid', 0)} 无效 · {count.get('missing', 0)} 缺失",
        "{{REPEATS}}": str(analysis["repeats"]), "{{BUILD_ROWS}}": "".join(build_rows), "{{GATE}}": gate_text,
        "{{GATE_HISTORY}}": f"主矩阵无效 {count.get('invalid', 0)} 行 · 正式尝试历史失败 {sum(row['action'] == 'failed_formal_attempt' for row in history_phases)} 轮 · 历史 gate / C8 smoke 失败 {sum(row['status'] == 'failed' and row['action'] in {'gate', 'smoke'} for row in history_phases)} 轮",
        "{{PROFILE_NAME}}": html.escape(profile["name"]),
        "{{FEATURE_PROFILE}}": feature_note,
        "{{HISTORY_SCOPE}}": ('本页从已识别的源码构建物直接进入纯 KV 批量协议；旧单 key suite 和旧 smoke 不作为前置条件，旧 smoke_exit 按原值保留，null 不改写为成功。本轮默认容量配置未实测；8192/8192 是依据已公开实验选择的固定参数，本页不据此推断默认到适配后的收益。构建记录不替代本 stage 的批量门禁、C8 smoke 与 TCP 证明。<a href="' + source_builds["evidence"] + '">构建输入来源核验 ↗</a>' if source_builds else '默认 Mooncake 曾在 B=4096 的带跟踪门禁与无跟踪 C8 smoke 中出现 TRANSFER_FAIL；队列溢出仅为未确认的源码候选解释。r3 正式尝试的 DataSystem 两 Worker 共用了默认 RocksDB 目录，伴随 SST 缺失/大小错误和 RPC 超时，属于实验部署隔离遗漏，不能作为产品 API 缺陷结论。所有原始失败单独保留，主矩阵使用新的独立目录与门禁。'),
        "{{TCP_PROOF}}": ('<a href="datasystem-batch-tcp-verification.json">DataSystem 批量 TCP 完整审计 ↗</a>：16 组合 / 32 方向，逐方向成功 TCP 字节 ≥ B × 1024（含 RPC 开销），并捕获同批某 key 的完整 1 KiB 样本；不等于逐字节证明整批载荷或持续跟踪正式测量。' if tcp_verification else '尚未提供本批量门禁的 TCP payload 审计；不以旧单 key 证明替代。'),
        "{{DS_ISOLATION}}": (('<b>本配置已核对两个 Worker 的独立' + directory_label + '。</b>' + ''.join('<p class="small">Worker ' + str(row["worker_index"]) + '：<a href="' + row["evidence"] + '">原始旗标 ↗</a> <span class="path">' + html.escape(row["rocksdb_store_dir"]) + '</span></p>' for row in datasystem_isolation["workers"])) if datasystem_isolation and datasystem_isolation.get("verified") else '尚无两个 Worker 独立' + directory_label + '的完整门禁证明；预览不具备公平部署结论。') + ('<p class="small"><a href="deployment-audit/deployment-audit.json">全部服务期部署审计 ↗</a>：21 集群 / 42 个独立' + directory_label + '，完整 stdout 扫描匹配存储文件错误 0 条。仅 gate 1/21 有已保存 live procfs 观测，其余 20 个服务期只核启动记录与日志；不推断持续存活或远端 inode 隔离。</p>' if deployment_audit else '<p class="small">尚未提供全部服务期部署审计。</p>'),
        "{{RESOURCE_AUDIT}}": ('<a href="resource-audit/resource-audit.md">独立资源审计 ↗</a> · <a href="resource-audit/resource-audit.json">完整 JSON ↗</a>：核对原始采样与 SDK 窗口，保留 CPU throttle、内存事件、swap、parent/monitor 和外部负载估计的独立口径，不新增采样或性能重复。' if resource_audit else '尚未提供独立资源审计。'),
        "{{NATIVE_FAILURES}}": ('<div class="notice failure"><b>' + str(count.get("failed", 0)) + ' 个原生只读用例返回错误。</b>失败结果与全部客户端原始记录和实际错误单独保留。它们不是 N/A 或零吞吐；发生错误的实际窗口中成功批次速率不进入性能柱、散点或三轮聚合。完整执行与证据核验不代表全部 API 成功。<a href="#native-failures">查看全部失败原件 ↗</a></div>' if count.get("failed", 0) else ''),
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
        for directory in ["builds", "provenance", "gate", "smoke", "gate-history", "history", "failed-attempts", "environment", "measured-runtime", "tcp-proof-sources", "resource-audit", "deployment-audit", "results"]:
            for source in sorted((out / directory).rglob("*")) if (out / directory).exists() else []:
                if source.is_file():
                    zipped.write(source, "kvc-fair/" + source.relative_to(out).as_posix())
        if tcp_verification:
            zipped.write(out / "datasystem-batch-tcp-verification.json", "kvc-fair/datasystem-batch-tcp-verification.json")
        for record in tcp_records + resource_records + deployment_records:
            packaged = zipped.read("kvc-fair/" + record["relative"])
            if len(packaged) != record["bytes"] or hashlib.sha256(packaged).hexdigest() != record["sha256"]:
                raise ValueError("Packaged batch TCP proof/source bytes changed")
        for record in runtime_records:
            name = "kvc-fair/measured-runtime/" + record["relative"]
            packaged = zipped.read(name)
            if len(packaged) != record["bytes"] or hashlib.sha256(packaged).hexdigest() != record["sha256"]:
                raise ValueError("Packaged measured runtime changed: " + record["relative"])
            if "mode" in record and zipped.getinfo(name).external_attr >> 16 & 0o777 != record["mode"]:
                raise ValueError("Packaged measured runtime mode differs from frozen stage: " + record["relative"])
        for record in failed_runtime:
            packaged = zipped.read("kvc-fair/" + record["path"])
            if len(packaged) != record["bytes"] or hashlib.sha256(packaged).hexdigest() != record["sha256"]:
                raise ValueError("Packaged historical failed-attempt stage input changed")
            if "mode" in record and zipped.getinfo("kvc-fair/" + record["path"]).external_attr >> 16 & 0o777 != record["mode"]:
                raise ValueError("Packaged historical frozen runtime mode changed")
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
    parser.add_argument("--deployment-audit", type=Path)
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
