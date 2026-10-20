"""Read-only history/failure classification and explicit archived evidence copies."""
import hashlib
import json
from pathlib import Path

from kvcbench.platform.files import digest, copy_file
from kvcbench.core.configuration import configuration_profile
from kvcbench.collect.audits.runtime import verify_runtime_inputs


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
