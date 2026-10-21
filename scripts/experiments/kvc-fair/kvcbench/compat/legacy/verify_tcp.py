#!/usr/bin/env python3
"""Read-only release gate for the separate all-TCP experiment.

verify_tcp(args) returns evidence and errors without writing files. The CLI only
writes --out; it never starts products, probes, tracing or benchmarks. Original
native build/smoke metadata is retained as reused evidence, not a TCP rebuild.
"""

import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in {None, ""}:
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[3]))

import argparse
import ast
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import sys

sys.dont_write_bytecode = True

from kvcbench.collect.audits import legacy as verify_results
from kvcbench.build.metadata import PRODUCTS as BUILD_PINS, write_json
from kvcbench.collect.traces_legacy import trace_bytes
from kvcbench.diagnostics import datasystem_sizes as ds_medium_probe
PRODUCTS = {"datasystem", "mooncake", "lmcache"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text())


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            result.update(block)
    return result.hexdigest()


def relative_to_root(path, root):
    relative = PurePosixPath(path).relative_to(PurePosixPath(root))
    require(relative.parts and ".." not in relative.parts, "Unsafe execution input path: " + str(path))
    return relative.as_posix()


def primary_evidence(suite):
    interference = suite / "interference-audit.json"
    result = verify_results.verify(argparse.Namespace(suite=suite, formal=True,
        interference_json=interference if interference.exists() else None, put_profile=None))
    require(result["passed"] and result["expected_cells"] == 81 and result["verified_results"] == result["expected_results"] == 216,
            "Primary formal integrity gate failed: " + json.dumps(result.get("errors"), ensure_ascii=False))
    plan = read_json(suite / "plan.json")
    groups = Counter()
    for row in plan:
        for operation in ("put", "get", "exist_hit", "exist_miss") if row["size"] == 1024 else ("put", "get"):
            groups[(row["product"], row["size"], row["concurrency"], operation)] += 1
    require(len(groups) == 72 and set(groups.values()) == {3}, "Expected exactly 72 complete three-repeat groups")
    return dict(result, completed_three_repeat_groups=len(groups))


def suite_file(suite, relative):
    raw = PurePosixPath(relative)
    require(not raw.is_absolute() and ".." not in raw.parts, "Unsafe suite evidence path")
    path = suite / raw.as_posix()
    require(path.is_file() and not path.is_symlink(), "Missing suite evidence: " + str(path))
    return path


def preflight_evidence(suite, root, stage):
    expected = {name: row["sha256"] for name, row in stage["inputs"].items()}
    path = suite / "preflight-changes.json"
    if not path.exists():
        return expected, {"present": False, "revisions": []}
    audit = read_json(path)
    require(audit.get("schema_version") == 1 and audit.get("purpose") == "tcp_protocol_preflight_correction"
            and audit.get("root") == root and audit.get("stage_sha256") == digest(suite / "tcp-stage.json"),
            "Preflight changes do not identify the immutable original stage")
    events = [json.loads(line) for line in (suite / "events.jsonl").read_text().splitlines() if line.strip()]
    require(events, "Preflight changes cannot be ordered before absent formal events")
    first_formal = min(row["wall_ns"] for row in events)
    previous_end, verified, permission_records = 0, [], []
    launcher = "mooncake/launch_master.sh"
    allowed = {"tcp_stage.py", "tcp_protocol_gate.py", "tcp_probe_ds_sizes.py", launcher}
    require(audit["revisions"], "Preflight audit contains no revisions")
    for index, revision in enumerate(audit["revisions"], 1):
        label = "attempt-" + str(index).zfill(4)
        require(revision["number"] == index and revision.get("formal_started") is False
                and type(revision["start_wall_ns"]) is int and type(revision["end_wall_ns"]) is int
                and 0 < revision["start_wall_ns"] and previous_end <= revision["start_wall_ns"] <= revision["end_wall_ns"] < first_formal,
                "Preflight helper correction must be ordered strictly before formal measurement")
        guard = revision["guard"]
        members = guard["members"]
        require(guard["populated"] == [] and members and all(values == [] for values in members.values())
                and all(any(name.endswith("/" + role + "/cgroup.procs") for name in members) for role in ("server", "client", "monitor")),
                "Preflight helper correction lacks a captured empty-cgroup guard")
        require(revision["protocol_archive"] == "protocol-history/" + label, "Unexpected preflight protocol archive path")
        archive = suite / revision["protocol_archive"]
        files = revision["protocol_files"]
        require(files and set(files) == {str(p.relative_to(archive)) for p in archive.rglob("*") if p.is_file()},
                "Failed protocol archive file inventory changed")
        for relative, record in files.items():
            saved = suite_file(archive, relative)
            require(digest(saved) == record["sha256"] and saved.stat().st_size == record["bytes"], "Failed protocol archive SHA/size mismatch")
        failed = read_json(archive / "summary.json")
        require(failed.get("valid") is False and type(failed.get("end_wall_ns")) is int
                and failed["end_wall_ns"] <= revision["start_wall_ns"], "Preflight archive must be an earlier terminal failed protocol gate")
        changes, seen = revision["changes"], set()
        permissions = revision.get("deployment_permissions", [])
        require(changes or permissions, "Preflight revision contains no corrections")
        before_expected = dict(expected)
        for change in changes:
            name = change["relative_path"]
            require(name in allowed and name not in seen and change["before_sha256"] == expected[name]
                    and change["after_sha256"] != change["before_sha256"], "Unapproved or discontinuous preflight helper SHA chain")
            seen.add(name)
            for side in ("before", "after"):
                require(change[side + "_file"] == "preflight-history/" + label + "/" + side + "/" + name,
                        "Preflight source snapshot path differs from its revision")
                require(digest(suite_file(suite, change[side + "_file"])) == change[side + "_sha256"], "Preflight helper snapshot SHA mismatch")
            expected[name] = change["after_sha256"]
        require(isinstance(permissions, list) and len(permissions) <= 1, "Unexpected deployment permission inventory")
        audited_permissions = []
        for permission in permissions:
            require(permission["relative_path"] == launcher and type(permission["before_mode"]) is int
                    and type(permission["after_mode"]) is int and permission["before_mode"] in (0o644, 0o755)
                    and permission["after_mode"] == 0o755
                    and (permission["before_mode"] != permission["after_mode"] or launcher in seen),
                    "Only the Mooncake launcher deployment mode 0644/0755 to 0755 may be recorded")
            checksum = permission["sha256"]
            require(checksum in {before_expected[launcher], expected[launcher]}, "Deployment permission SHA is outside the launcher content chain")
            legacy = checksum != expected[launcher]
            require(not legacy or launcher in seen, "Legacy permission SHA must identify this revision's previous launcher body")
            record = dict(permission, revision=index, legacy_sequencing_bug=legacy,
                          recorded_sha_position="before_content_copy" if legacy else "after_content_copy",
                          effective_mode_proven=False)
            permission_records.append(record)
            audited_permissions.append(record)
        previous_end = revision["end_wall_ns"]
        verified.append({"number": index, "end_wall_ns": previous_end, "protocol_archive": revision["protocol_archive"],
                         "archived_files": len(files), "changes": changes, "deployment_permissions": audited_permissions})
    if permission_records:
        final = permission_records[-1]
        require(final["sha256"] == expected[launcher] and not final["legacy_sequencing_bug"], "Final deployment permission record must bind the final launcher body")
        require(all(not row["legacy_sequencing_bug"] or row["revision"] < final["revision"] for row in permission_records),
                "A legacy permission sequencing bug requires a later corrective revision")
    return expected, {"present": True, "sha256": digest(path), "immutable_stage_sha256": digest(suite / "tcp-stage.json"),
                      "first_formal_event_wall_ns": first_formal, "revisions": verified, "deployment_permissions": permission_records,
                      "scope": "Only three diagnostic/safety helpers and the specified Mooncake lifecycle launcher may change before formal events. Original stage bytes, before/after bodies and complete failed protocol archives remain hashed. Permission records do not prove historical modes took effect; legacy copy ordering errors require later correction and final snapshot verification. Benchmark, adapter, config and native artifacts receive no exception."}


class ExecutionEvidence:
    def __init__(self, suite):
        self.suite = suite
        self.parameters = read_json(suite / "parameters.json")
        self.root = self.parameters["root"]
        self.products = read_json(suite / "products.json")
        self.by_name = {p["name"]: p for p in self.products}
        require(len(self.products) == 3 and set(self.by_name) == PRODUCTS, "Formal product manifest must contain each product exactly once")
        self.stage = read_json(suite / "tcp-stage.json")
        require(self.stage.get("profile") == "tcp" and self.stage.get("root") == self.root,
                "TCP stage identity differs from formal root")
        require(self.stage.get("source_builds_reused") is True and self.stage.get("native_inputs_modified") is False,
                "Stage must explicitly preserve and reuse the original source builds")
        self.expected, self.preflight = preflight_evidence(suite, self.root, self.stage)
        self.snapshot = suite / "build-and-script-evidence"
        manifest = read_json(self.snapshot / "snapshot-manifest.json")
        rows = manifest["files"]
        self.snapshot_files = {row["path"]: row for row in rows}
        require(len(rows) == len(self.snapshot_files), "Duplicate stage snapshot paths")
        self.provenance = read_json(suite / "resume-provenance.json")
        require(self.provenance.get("products") == self.products, "Resume provenance product configuration drift")
        for key in ("root", "output", "manifest", "duration", "warmup", "sizes", "concurrencies", "repeats", "smoke", "products"):
            require(self.provenance["parameters"].get(key) == self.parameters.get(key), "Resume provenance parameter drift: " + key)
        self.checked = {}

    def file(self, relative, *, provenance=True):
        require(".." not in PurePosixPath(relative).parts and not PurePosixPath(relative).is_absolute(), "Unsafe snapshot path")
        expected = self.expected[relative]
        path = self.snapshot / relative
        observed = digest(path)
        require(observed == expected == self.snapshot_files[relative]["sha256"], "Stage/snapshot SHA drift: " + relative)
        if relative == "mooncake/launch_master.sh" and self.preflight.get("deployment_permissions"):
            require(path.stat().st_mode & 0o777 == self.preflight["deployment_permissions"][-1]["after_mode"],
                    "Final captured Mooncake launcher mode differs from its corrective deployment record")
        if provenance:
            record = self.provenance["files"][str(PurePosixPath(self.root) / relative)]
            require(record["sha256"] == observed and record["size"] == path.stat().st_size,
                    "Formal execution provenance SHA/size drift: " + relative)
        self.checked[relative] = {"sha256": observed, "bytes": path.stat().st_size, "formal_inventory_checked": provenance}
        return path

    def source_hash(self, path, expected, *, provenance=True):
        relative = relative_to_root(path, self.root)
        require(digest(self.file(relative, provenance=provenance)) == expected, "Probe input SHA drift: " + relative)

    def validate(self):
        root_manifest = read_json(self.file("products.json", provenance=False))
        require(root_manifest["products"] == self.products, "Staged actual product manifest differs from formal/provenance products")
        required = {"run_suite.py", "benchmark.py", "cgroup.py", "monitor.py"}
        for product in self.products:
            required.update(relative_to_root(product[key], self.root) for key in ("adapter", "config"))
            for action in ("start", "stop"):
                required.update(relative_to_root(token, self.root) for token in product[action]
                                if token.endswith((".py", ".sh")) and "{" not in token)
        required.update("mooncake/" + name for name in ("env.sh", "launch_master.sh", "peer.py", "process_identity.py"))
        required.add("lmcache/start_server.sh")
        for relative in sorted(required):
            self.file(relative)
        # The TCP subclass imports this existing helper; stage and snapshot bind it
        # even when the original driver's direct-input inventory omitted it.
        self.file("lmcache/adapter_lmcache.py", provenance=False)
        for relative in ("tcp_stage.py", "tcp_protocol_gate.py", "tcp_probe_ds_sizes.py"):
            self.file(relative, provenance=False)
        script_hashes = read_json(self.suite / "script-hashes.json")
        for relative in ("run_suite.py", "benchmark.py", "cgroup.py", "monitor.py"):
            require(script_hashes.get(relative) == self.checked[relative]["sha256"], "Formal script-hashes drift: " + relative)
        for product in self.products:
            config = read_json(self.file(relative_to_root(product["config"], self.root)))
            require(config.get("transport") == "tcp", "Formal product config is not TCP: " + product["name"])
        return {"root": self.root, "checked_inputs": self.checked, "preflight_changes": self.preflight,
                "manifest_sha256": {name: digest(self.suite / name) for name in ("tcp-stage.json", "products.json", "parameters.json", "resume-provenance.json", "script-hashes.json")},
                "scope": "Required execution scripts/configs match staged bytes, saved execution inventory and post-run snapshots. Imported LMCache base helper is additionally bound by stage/snapshot. This does not prove absence of transient mutate-and-restore actions."}


def transport_configuration(evidence, protocol):
    ds = evidence.by_name["datasystem"]
    tokens = ds["start"]
    require("--transport" in tokens and tokens[tokens.index("--transport") + 1] == "tcp", "DataSystem start must select --transport tcp")
    runtime = evidence.file("scripts/datasystem/runtime.py").read_text()
    tree = ast.parse(runtime)
    literal_pairs = {(key.value, value.value) for node in ast.walk(tree) if isinstance(node, ast.Dict)
                     for key, value in zip(node.keys, node.values)
                     if isinstance(key, ast.Constant) and isinstance(value, ast.Constant)}
    require({("enable_rdma", "false"), ("enable_urma", "false")} <= literal_pairs, "DataSystem runtime source does not explicitly disable RDMA/URMA")
    flags = []
    for index in (0, 1):
        path = protocol / "datasystem" / "run" / ("w" + str(index)) / "worker.flags"
        values = {}
        for line in path.read_text().splitlines():
            if line.startswith("--") and "=" in line:
                key, value = line[2:].split("=", 1)
                require(key not in values, "Duplicate worker flag: " + key)
                values[key] = value
        require(all(values.get(key) == "false" for key in ("enable_rdma", "enable_urma", "ipc_through_shared_memory")),
                "Recorded DataSystem worker flags must disable RDMA, URMA and SHM")
        flags.append({"path": str(path), "sha256": digest(path), "disabled": ["enable_rdma", "enable_urma", "ipc_through_shared_memory"]})
    lm = evidence.by_name["lmcache"]
    require(PurePosixPath(lm["adapter"]).name == "adapter_lmcache_tcp.py", "LMCache formal adapter must be the TCP adapter")
    adapter_tree = ast.parse(evidence.file(relative_to_root(lm["adapter"], evidence.root)).read_text())
    require(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance"
                and len(node.args) == 2 and isinstance(node.args[1], ast.Name) and node.args[1].id == "EngineDrivenContextPickle"
                for node in ast.walk(adapter_tree)), "LMCache adapter lacks the native pickle-context type guard")
    response_guards = {(node.left.attr, node.comparators[0].value) for node in ast.walk(adapter_tree)
                       if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotEq)
                       and isinstance(node.left, ast.Attribute) and isinstance(node.left.value, ast.Name)
                       and node.left.value.id == "response" and isinstance(node.comparators[0], ast.Constant)}
    require({("shm_name", ""), ("pool_size", 0)} <= response_guards, "LMCache adapter lacks empty-SHM/zero-pool response guards")
    require(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith"
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "tcp://"
                for node in ast.walk(adapter_tree)), "LMCache adapter lacks an explicit TCP endpoint guard")
    wrapper = shlex.split(evidence.file("lmcache/start_server.sh").read_text(), comments=True)
    positions = [i for i, token in enumerate(wrapper) if token == "--shm-name"]
    require(positions and all(i + 1 < len(wrapper) and wrapper[i + 1] == "" for i in positions), "LMCache executed wrapper must disable SHM with an empty name")
    require("--no-l1-use-lazy" in wrapper, "LMCache TCP wrapper must fix the eager CPU L1 allocator")
    moon = evidence.by_name["mooncake"]
    require(all(moon["env"].get(key) == "0" for key in ("MC_STORE_MEMCPY", "MC_STORE_LOCAL_HOT_CACHE_SIZE")),
            "Mooncake must disable same-node memcpy and local hot cache")
    return {"datasystem_worker_flags": flags, "lmcache": "native CPU pickle over explicit TCP; no advertised SHM pool",
            "mooncake": {key: moon["env"][key] for key in ("MC_STORE_MEMCPY", "MC_STORE_LOCAL_HOT_CACHE_SIZE")}}


def native_build_evidence(evidence):
    expected_artifacts, records = {}, {}
    base = PurePosixPath(evidence.stage["base"])
    suite_relative = relative_to_root(evidence.parameters["output"], evidence.root)
    for name in sorted(PRODUCTS):
        path = evidence.suite / "builds" / (name + ".json")
        row = read_json(path)
        require(row.get("tag") == BUILD_PINS[name]["tag"] and row.get("sha") == BUILD_PINS[name]["sha"], "Reused build source pin mismatch: " + name)
        require(row.get("build_exit") == row.get("smoke_exit") == 0, "Original build/smoke did not succeed: " + name)
        require(evidence.stage["inputs"][suite_relative + "/builds/" + name + ".json"]["sha256"] == digest(path), "Original build metadata bytes changed: " + name)
        artifacts = row["artifact_hashes"]
        pairs = artifacts.items() if isinstance(artifacts, dict) else ((a["path"], a["sha256"]) for a in artifacts)
        retained = {}
        for relative, sha in pairs:
            artifact = base / name / relative
            if artifact.name == "datasystem_worker" or artifact.name.endswith((".whl", ".tar.gz")) or ".so" in artifact.name:
                expected_artifacts[str(artifact)] = sha
                retained[relative] = sha
        require(retained, "No reusable native artifact identities: " + name)
        records[name] = {"tag": row["tag"], "sha": row["sha"], "metadata_sha256": digest(path), "native_artifacts": retained,
                         "build_exit": row["build_exit"], "original_native_smoke_exit": row["smoke_exit"]}
    observed = evidence.stage["native_artifacts_revalidated"]
    require(set(observed) == set(expected_artifacts), "Stage native artifact revalidation inventory differs from original build binaries")
    require(all(observed[path]["sha256"] == sha and observed[path]["bytes"] > 0 for path, sha in expected_artifacts.items()),
            "Native artifact was not revalidated against its original build SHA")
    return {"source_builds_reused": True, "tcp_rebuild_performed": False, "products": records,
            "scope": "Original source-build and native-smoke metadata is preserved. Only native binaries/packages are matched to staging revalidation; old script/wrapper hashes are not identities of new TCP scripts. No offline rehash of absent remote binaries or new build is claimed."}


def protocol_evidence(evidence, protocol):
    summary = read_json(protocol / "summary.json")
    rows = summary["products"]
    require(summary.get("valid") is True and not summary.get("fatal") and not summary.get("cgroup_drain_error"), "Protocol gate is invalid or failed")
    drained = summary["cgroups_after"]
    require(drained["populated"] == [] and drained["members"] and all(values == [] for values in drained["members"].values()),
            "Protocol gate lacks captured empty final cgroups")
    require(len(rows) == 3 and {row["product"] for row in rows} == PRODUCTS, "Protocol summary must contain exactly three unique products")
    clients = []
    for row in rows:
        name = row["product"]
        require(row.get("valid") is True and len(row["clients"]) == 2 and {c["client_index"] for c in row["clients"]} == {0, 1}, "Protocol product requires two unique successful clients: " + name)
        for record in row["clients"]:
            index = record["client_index"]
            require(record.get("valid") is True and Path(record["probe_file"]).name == record["probe_file"], "Invalid protocol client/probe path")
            path = protocol / name / record["probe_file"]
            require(digest(path) == record["probe_sha256"], "Protocol probe SHA mismatch")
            probe = read_json(path)
            require(probe.get("valid") is True and probe.get("product") == name and probe.get("client_index") == index,
                    "Protocol probe identity/correctness mismatch")
            require(probe.get("value_size") == 1 << 20 and type(probe.get("objects")) is int and 4 <= probe["objects"] <= 16, "Expected at least four bounded 1MiB objects")
            source = probe["source"]
            product = evidence.by_name[name]
            for key in ("adapter", "config"):
                require(source[key] == product[key], "Protocol/measurement input path drift: " + key)
                evidence.source_hash(source[key], source[key + "_sha256"])
            evidence.source_hash(source["benchmark"], source["benchmark_sha256"])
            require(digest(evidence.file("tcp_wire_probe.py", provenance=False)) == source["probe_sha256"], "Wire probe helper SHA mismatch")
            operations = probe["operations"]
            require(all(op.get("status") == "ok" for op in operations), "A protocol operation did not succeed")
            require(Counter(op["operation"] for op in operations) == Counter({"initialize": 1, "close": 1,
                **{op: probe["objects"] for op in ("exist_miss_before_put", "put", "get", "exist_hit", "exist_miss")}}), "Protocol operation counts are incomplete")
            for number in range(probe["objects"]):
                by_op = {op["operation"]: op for op in operations if op.get("object_index") == number}
                require(len(by_op) == 5, "Protocol object has missing/duplicate operations")
                key = by_op["put"]["key"]
                expected_sha = hashlib.sha256(hashlib.shake_256(key.encode()).digest(1 << 20)).hexdigest()
                require(by_op["put"].get("value_bytes") == 1 << 20 and by_op["put"].get("value_sha256") == expected_sha, "Protocol PUT payload identity mismatch")
                validation = by_op["get"]["validation"]
                require(by_op["get"]["key"] == key and validation.get("valid") is True and validation.get("exact_bytes_equal") is True
                        and validation.get("length") == 1 << 20 and validation.get("sha256") == expected_sha, "Protocol GET lacks complete exact-byte validation")
                for op, expected in (("exist_miss_before_put", False), ("exist_hit", True), ("exist_miss", False)):
                    validation = by_op[op]["validation"]
                    require(by_op[op]["key"] == (key + "-never-written" if op == "exist_miss" else key), "Protocol EXIST key identity mismatch")
                    require(validation.get("valid") is True and validation.get("exists") is expected and validation.get("expected") is expected,
                            "Protocol EXIST value is not verified")
            if name == "lmcache":
                require(probe["adapter"]["class"].endswith(".LMCacheTCPAdapter") and probe["adapter"]["component_classes"]["transfer"].endswith(".EngineDrivenContextPickle"), "Probe did not instantiate the native TCP/pickle adapter")
            if name == "mooncake":
                require(all(probe["environment"].get(key) == "0" for key in ("MC_STORE_MEMCPY", "MC_STORE_LOCAL_HOT_CACHE_SIZE")), "Mooncake probe environment differs from the TCP configuration")
            paths = sorted((protocol / name).glob("strace-c" + str(index) + ".*"))
            hashes = record["trace_sha256"]
            require(paths and set(hashes) == {p.name for p in paths} and all(not p.is_symlink() and digest(p) == hashes[p.name] for p in paths), "Protocol strace SHA inventory mismatch")
            observed = trace_bytes(paths)
            minimum = max(8 << 20, 2 * (1 << 20) * probe["objects"])
            require(observed["successful_tcp_syscall_bytes"] >= minimum, "Insufficient directly observed TCP payload bytes")
            for key in ("successful_tcp_syscall_bytes", "successful_tcp_data_calls", "trace_files", "tcp_endpoints"):
                require(record[key] == observed[key], "Reparsed strace differs from protocol summary: " + key)
            clients.append({"product": name, "client_index": index, "probe_sha256": digest(path), "trace_sha256": hashes,
                            "minimum_required_bytes": minimum, "exact_payload_objects": probe["objects"], **observed})
    return {"valid": True, "summary_sha256": digest(protocol / "summary.json"), "clients": clients,
            "scope": "Six client processes: complete PUT/GET byte checks plus reparsed successful TCP syscall bytes. Includes protocol overhead; this is not performance or proof that every future request uses TCP."}


def protocol_file(path, protocol, remote_root):
    raw = PurePosixPath(path)
    if not raw.is_absolute():
        relative = raw
    else:
        try:
            relative = raw.relative_to(PurePosixPath(protocol))
        except ValueError:
            relative = raw.relative_to(PurePosixPath(remote_root) / "protocol-gate-001")
    require(".." not in relative.parts, "Unsafe protocol evidence path")
    result = protocol / relative.as_posix()
    require(result.is_file() and not result.is_symlink(), "Missing raw protocol evidence: " + str(result))
    return result


def protocol_identity(value, protocol, remote_root):
    if isinstance(value, list):
        return [protocol_identity(item, protocol, remote_root) for item in value]
    if isinstance(value, dict):
        return {key: str(protocol_file(item, protocol, remote_root).relative_to(protocol)) if key == "path"
                else protocol_identity(item, protocol, remote_root) for key, item in value.items()}
    return value


def datasystem_configuration(evidence, protocol, raw, endpoints):
    configuration = raw["configuration_evidence"]
    require(configuration.get("source") == raw["source"], "DataSystem worker configuration source identity drift")
    workers = configuration["workers"]
    require(len(workers) == 2 and {row["worker_index"] for row in workers} == {0, 1}, "DataSystem medium worker identities are incomplete")
    processes_path = protocol_file(configuration["processes_path"], protocol, evidence.root)
    require(processes_path == protocol / "datasystem/run/processes.json" and digest(processes_path) == configuration["processes_sha256"],
            "DataSystem live-process inventory SHA/path mismatch")
    processes = read_json(processes_path)["processes"]
    for worker in workers:
        index = worker["worker_index"]
        path = protocol_file(worker["path"], protocol, evidence.root)
        require(path == protocol / "datasystem/run" / ("w" + str(index)) / "worker.flags" and digest(path) == worker["sha256"],
                "DataSystem medium flags SHA/path mismatch")
        lines = [line[2:].split("=", 1) for line in path.read_text().splitlines() if line.startswith("--")]
        flags = dict(lines)
        required = {"worker_address": endpoints[index], "ipc_through_shared_memory": "false", "enable_rdma": "false", "enable_urma": "false"}
        require(len(lines) == len(flags) and worker["flags"] == required and all(flags.get(key) == value for key, value in required.items()),
                "DataSystem medium worker flags disagree with the formal TCP endpoints/configuration")
        matching = [row for row in processes if row["name"] == "worker" + str(index)]
        require(len(matching) == 1 and matching[0]["pid"] == worker["pid"] and str(matching[0]["start_ticks"]) == str(worker["start_ticks"]),
                "DataSystem medium recorded live PID identity differs from process inventory")
        require(all("--" + key + "=" + value in worker["live_argv"] for key, value in required.items()),
                "DataSystem recorded live argv does not bind the disabled transports and endpoint")
    return {"workers": workers, "processes_sha256": digest(processes_path),
            "scope": "Saved worker flags and captured live argv/PID start identity agree. This offline check does not re-observe a live process."}


def datasystem_snapshots(child, directory, protocol, root):
    verified = {}
    for name in ("before", "after"):
        snapshot = child["process_snapshots"][name]
        path = protocol_file(snapshot["maps"]["path"], protocol, root)
        require(path == directory / (name + ".maps") and digest(path) == snapshot["maps"]["sha256"], "DataSystem process maps SHA/path mismatch")
        shared = [line for line in path.read_text().splitlines()
                  if len(line.split()) >= 2 and line.split()[1].startswith("rw") and line.split()[1].endswith("s")]
        require(shared == snapshot["writable_shared_mappings"] == [], "DataSystem client captured a writable shared mapping")
        descriptors = snapshot["fds"]
        require(len(descriptors) == len({fd["fd"] for fd in descriptors})
                and all(type(fd["fd"]) is int and fd["fd"] >= 0 and isinstance(fd["target"], str) and type(fd["unix_socket"]) is bool for fd in descriptors),
                "DataSystem captured descriptor inventory is malformed")
        require(snapshot["unix_socket_fds"] == [fd for fd in descriptors if fd["unix_socket"]] == [],
                "DataSystem client captured UNIX socket descriptors")
        verified[name] = {"maps_sha256": digest(path), "writable_shared_mappings": shared, "unix_socket_fds": [], "descriptor_count": len(descriptors)}
    return verified


def datasystem_medium(evidence, protocol, path):
    raw = read_json(path)
    require(raw.get("schema_version") == 2 and raw.get("passed") is True and raw["source"].get("sha") == BUILD_PINS["datasystem"]["sha"]
            and raw["source"].get("tag") == BUILD_PINS["datasystem"]["tag"], "DataSystem medium gate failed or source pin differs")
    summaries = [row for row in read_json(protocol / "summary.json")["products"] if row["product"] == "datasystem"]
    require(len(summaries) == 1, "Protocol summary lacks a unique DataSystem product")
    reference = summaries[0]["actual_medium_evidence"]
    require(protocol_file(reference["path"], protocol, evidence.root) == path.resolve() and reference["sha256"] == digest(path),
            "DataSystem medium bytes/path are not bound by the successful protocol summary")
    product = evidence.by_name["datasystem"]
    for key in ("adapter", "config"):
        evidence.source_hash(product[key], raw["input_sha256"][key])
    helper = protocol / "tcp_probe_ds_sizes.py"
    if "tcp_probe_ds_sizes.py" in evidence.stage["inputs"]:
        helper = evidence.file("tcp_probe_ds_sizes.py", provenance=False)
    require(digest(helper) == raw["input_sha256"]["probe"], "DataSystem medium helper SHA mismatch")
    require(digest(Path(ds_medium_probe.__file__)) == digest(helper), "Offline DataSystem evidence parser differs from the captured helper")
    require(raw.get("environment") == ds_medium_probe.ENVIRONMENT, "DataSystem medium diagnostic environment differs from the helper")
    config = read_json(evidence.file(relative_to_root(product["config"], evidence.root)))
    endpoints = config["endpoints"]
    require(len(endpoints) == 2 and len(set(endpoints)) == 2, "DataSystem formal config needs two distinct TCP endpoints")
    configuration = datasystem_configuration(evidence, protocol, raw, endpoints)
    cases = raw["cases"]
    require(len(cases) == 6 and {(c["worker_index"], c["size"]) for c in cases} == {(i, s) for i in (0, 1) for s in (1024, 65536, 1048576)}, "DataSystem medium requires exactly six endpoint/size cases")
    verified = []
    for case in cases:
        require(case.get("passed") is True and case.get("returncode") == 0 and case.get("timed_out") is False and case.get("malformed") == []
                and case.get("actual_media") == ["TCP"], "DataSystem medium case failed or contains non-TCP/malformed access")
        child = case["child"]
        require(child.get("key") == case["key"] and child.get("size") == case["size"] and child.get("worker_index") == case["worker_index"],
                "DataSystem medium child identity differs from the case")
        require(case["bound_endpoint"] == endpoints[case["worker_index"]], "DataSystem medium endpoint differs from the measured adapter config")
        block = hashlib.sha256(case["key"].encode()).digest()
        expected_sha = hashlib.sha256(block * (case["size"] // len(block))).hexdigest()
        require(child.get("correctness") is True and child.get("returned_bytes") == case["size"]
                and child.get("payload_sha256") == child.get("returned_sha256") == expected_sha
                and child.get("payload_prefix_hex") == block.hex() and child.get("environment") == ds_medium_probe.ENVIRONMENT
                and all(child["checks"].get(key) is True for key in ("fresh_key_absent", "independent_miss_before", "get_exact_bytes", "exists_hit", "exists_miss")),
                "DataSystem medium lacks exact complete bytes/EXIST checks")
        windows = child["windows"]
        require(set(windows) == {"put", "get"} and all(type(window.get(key)) is int for window in windows.values() for key in ("start_wall_ns", "end_wall_ns"))
                and raw["start_wall_ns"] <= windows["put"]["start_wall_ns"] <= windows["put"]["end_wall_ns"] <= windows["get"]["start_wall_ns"] <= windows["get"]["end_wall_ns"] <= raw["end_wall_ns"],
                "DataSystem medium operation wall windows are absent, unordered or outside the probe")
        traces = case["trace_sha256"]
        require(traces, "DataSystem medium raw strace inventory is absent")
        directory = protocol_file(traces[0]["path"], protocol, evidence.root).parent
        child_path = directory / "child-result.json"
        require(read_json(child_path) == child, "DataSystem independently saved child result differs from the medium summary")
        snapshots = datasystem_snapshots(child, directory, protocol, evidence.root)
        access = ds_medium_probe.read_access_logs(directory)
        materialize, info_logs = ds_medium_probe.read_materialize(directory, case["key"])
        wire = ds_medium_probe.trace_evidence(directory, windows, block.hex(), endpoints)
        require(all(operation["actual_payload_endpoints"] and all(any(address.endswith("->" + endpoint) for endpoint in endpoints)
                    for address in operation["actual_payload_endpoints"]) for operation in wire["operations"].values()),
                "DataSystem payload endpoints are absent or outside the formal worker whitelist")
        repeated = ds_medium_probe.audit_evidence(access, materialize, wire, child, case["key"], case["size"],
                                                 returncode=case["returncode"], timed_out=case["timed_out"])
        repeated["log_sha256"].extend(info_logs)
        require(repeated["passed"] is True, "DataSystem raw syscall/materialization/access evidence failed: " + json.dumps(repeated["errors"]))
        for key, value in repeated.items():
            require(protocol_identity(case[key], protocol, evidence.root) == protocol_identity(value, protocol, evidence.root),
                    "Reparsed DataSystem medium evidence differs from its summary: " + key)
        verified.append({"worker_index": case["worker_index"], "size": case["size"], "actual_media": ["TCP"], "exact_bytes": True,
                         "logs": case["log_sha256"], "trace_sha256": wire["trace_sha256"], "tcp_syscalls": wire["operations"],
                         "process_snapshots": snapshots, "child_sha256": digest(child_path)})
    return {"passed": True, "path": str(path), "sha256": digest(path), "configuration": configuration, "cases": verified,
            "scope": "Three sizes and two configured endpoints: reparse successful directional TCP syscalls inside synchronous PUT/GET windows, require at least payload-size bytes and the key-specific 32-byte payload prefix, and bind exact-key/size actual TCP GET Materialize logs. Official Python access records can omit transportType. Captured maps/FD snapshots are point observations; no performance, continuous no-SHM, routed storage target or worker-worker payload claim."}


def verify_tcp(args):
    suite, protocol = Path(args.suite).resolve(), Path(args.protocol_evidence).resolve()
    errors, checks = [], []
    result = {"schema_version": 1, "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "suite": str(suite), "protocol_evidence": str(protocol), "errors": errors, "checks": checks,
              "scope": "Read-only offline release gate. Native source builds are reused; no product, tracing, benchmark, rebuild or original-result write is performed."}
    def check(name, call):
        try:
            value = call()
            checks.append({"name": name, "passed": True})
            return value
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, SyntaxError) as error:
            errors.append({"check": name, "error": str(error)})
            checks.append({"name": name, "passed": False})
            return None
    result["primary"] = check("formal_216_81_72", lambda: primary_evidence(suite))
    evidence = check("load_execution_evidence", lambda: ExecutionEvidence(suite))
    if evidence is not None:
        result["execution"] = check("stage_provenance_snapshot_identity", evidence.validate)
        result["transport"] = check("tcp_no_rdma_urma_shm_configuration", lambda: transport_configuration(evidence, protocol))
        result["reused_native_builds"] = check("native_artifact_reuse", lambda: native_build_evidence(evidence))
        result["protocol"] = check("six_clients_tcp_payload_and_exact_bytes", lambda: protocol_evidence(evidence, protocol))
        medium = Path(getattr(args, "ds_medium", None) or protocol / "datasystem-medium.json")
        result["datasystem_medium"] = check("datasystem_three_sizes_actual_medium", lambda: datasystem_medium(evidence, protocol, medium))
    result["passed"] = not errors
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--protocol-evidence", type=Path, required=True)
    parser.add_argument("--ds-medium", type=Path, help="Defaults to protocol-evidence/datasystem-medium.json; required to exist")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = verify_tcp(args)
    write_json(args.out, result)
    print(json.dumps({"passed": result["passed"], "checks": len(result["checks"]), "errors": result["errors"], "out": str(args.out)}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
