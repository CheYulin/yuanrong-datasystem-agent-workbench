"""Independently revalidate collected gate inputs."""
import hashlib
import json
from pathlib import Path
import re

from kvcbench.platform.files import digest


def verify_gate_evidence(gate_path, stage, products):
    from kvcbench.collect.audits.integrity import component
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



def verify_tcp_evidence(path, gate_path, scripts):
    from kvcbench.core.payload import payload
    path, gate_path, scripts = Path(path), Path(gate_path).resolve(strict=True), Path(scripts)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected an immutable regular batch TCP verification JSON")
    raw = json.loads(path.read_bytes())
    if (raw.get("schema_version") not in {2, 3} or raw.get("kind") != "datasystem_native_batch_tcp_any_key_full_payload_audit"
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
    from kvcbench.collect.provenance import tcp_sources
    expected_sources = set(tcp_sources(scripts, legacy=raw["schema_version"] == 2))
    if set(sources) != expected_sources:
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
