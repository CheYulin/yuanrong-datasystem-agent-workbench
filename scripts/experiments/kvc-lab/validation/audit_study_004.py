#!/usr/bin/env python3
"""Offline audit of closed KVC Lab study 004 evidence, independently of the service.

Example:
  python3 validation/audit_study_004.py \
    --run recovery=recovery-probe-004-result.json \
    --artifact-root recovery=audit-inputs/recovery-probe-004 \
    --out audit-recovery-probe-004.json

Repeat --run/--artifact-root to audit several named runs. The script never uses
SSH, starts a service, imports the benchmark, or changes an experiment. Pending
jobs are reported as waiting, never as a failed/missing final matrix. Summaries
keep role CPU, master CPU, memory, source SHA and build SHA in their grouping key;
different budgets or installed code are never pooled together. SDK JSON and
resource samples must be available in the local frozen job directory to pass.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time


BUILDS = {"datasystem": "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6",
          "mooncake": "719735896c86b56fabec6cf3e825fb2ea640597a"}
SOURCE = "61c5c2e3151b227d5cc8bc86688164e622027ce953f67ef555ca54c4e044779e"
TERMINAL = {"succeeded", "completed_with_failures", "failed", "cancelled", "interrupted"}
GROUP_FIELDS = ("product", "profile", "operation", "batch", "size", "nodes", "workers",
                "workers_per_node", "clients", "server_cpus", "client_cpus", "master_cpus",
                "memory_gib", "build_sha", "source_sha256")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def close(left, right):
    return finite(left) and finite(right) and math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-8)


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def load(path):
    return json.loads(path.read_text())


def path_in(root, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or str(relative) != name:
        raise ValueError("Noncanonical artifact path: " + str(name))
    path = root / relative
    if any(parent.is_symlink() for parent in [path, *path.parents]) or not path.is_file():
        raise ValueError("Artifact must be a non-symlink regular file: " + str(name))
    return path


def case_key(row):
    return (row.get("variant_id"), row.get("product"), row.get("repeat"), row.get("value_size", row.get("size")), row.get("batch"), row.get("operation"))


def expected_matrix(plan):
    result = {}
    for index, variant in enumerate(plan["variants"]):
        config = variant["config"]
        for name, repeat, size, batch, operation in product(config["products"], range(config["repeats"]),
                config["value_sizes"], config["batches"], config["operations"]):
            row = {"variant_id": "v" + str(index), "product": name, "repeat": repeat,
                   "size": size, "batch": batch, "operation": operation,
                   "profile": config.get("profile", "baseline"), "nodes": config["nodes"],
                   "workers_per_node": config["workers_per_node"], "workers": config["nodes"] * config["workers_per_node"],
                   **{key: config[key] for key in ["clients", "server_cpus", "client_cpus", "memory_gib"]},
                   "master_cpus": config.get("master_cpus", min(2, config["server_cpus"])), "requested_duration_s": config["duration_s"]}
            if case_key(row) in result:
                raise ValueError("Submitted plan contains duplicate case identities")
            result[case_key(row)] = row
    return result


def upper_percentile(histogram, fraction):
    entries = sorted((int(key), count) for key, count in histogram.items())
    if not entries or any(type(count) is not int or count < 0 for _, count in entries):
        raise ValueError("Invalid native histogram counts")
    total = sum(count for _, count in entries)
    if not total:
        raise ValueError("Empty native histogram")
    rank, accumulated = math.ceil(total * fraction), 0
    for bucket, count in entries:
        accumulated += count
        if accumulated >= rank:
            exponent, sub = divmod(bucket, 64)
            if exponent < 6:
                raise ValueError("Invalid logarithmic histogram bucket")
            return ((65 + sub) << (exponent - 6)) / 1000, total


def stats(values):
    values = [value for value in values if finite(value)]
    return {"count": len(values), "median": statistics.median(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


class Auditor:
    def __init__(self, label, evidence, artifact_root):
        self.label, self.evidence, self.root = label, evidence, artifact_root
        self.issues = []
        self.checks = Counter()
        self.sample_cache = {}
        self.artifacts = {}

    def check(self, condition, code, detail=None):
        self.checks["passed" if condition else "failed"] += 1
        if not condition:
            self.issues.append({"code": code, "detail": detail})
        return condition

    def read_artifact(self, name):
        if self.root is None:
            raise ValueError("No local frozen artifact root supplied")
        path = path_in(self.root, name)
        identity = self.artifacts.get(name)
        if identity is None:
            raise ValueError("Artifact is absent from the remote completion manifest: " + name)
        if path.stat().st_size != identity["size"] or sha(path) != identity["sha256"]:
            raise ValueError("Frozen artifact SHA/size mismatch: " + name)
        return path

    def samples(self, sdk_name):
        source = str(Path(sdk_name).parent.parent / "resources.jsonl")
        candidates = [name for name, value in self.artifacts.items() if name == source or value.get("source") == source]
        if not candidates:
            raise ValueError("No bound resource samples for " + sdk_name)
        name = min(candidates, key=lambda value: value.endswith(".gz"))
        if name not in self.sample_cache:
            path = self.read_artifact(name)
            opener = gzip.open if name.endswith(".gz") else open
            with opener(path, "rt") as stream:
                rows = [json.loads(line) for line in stream if line.strip()]
            if any(type(row.get("mono_ns")) is not int for row in rows) or any(left["mono_ns"] >= right["mono_ns"] for left, right in zip(rows, rows[1:])):
                raise ValueError("Resource samples are not strictly ordered")
            self.sample_cache[name] = rows
        return self.sample_cache[name]

    def audit_row(self, row, expected):
        identity = list(case_key(row))
        before = len(self.issues)
        self.check(row.get("status") == "valid", "native_case_not_valid", identity)
        for key in ["profile", "nodes", "workers", "workers_per_node", "clients", "server_cpus", "client_cpus", "master_cpus", "memory_gib"]:
            self.check(row.get(key) == expected[key], "specification_mismatch", {"case": identity, "field": key, "expected": expected[key], "actual": row.get(key)})
        self.check(row.get("build_sha") == BUILDS.get(row.get("product")), "row_build_sha_mismatch", identity)
        for key in ["keys_s", "batch_qps", "p50_us", "p95_us", "p99_us", "duration_s", "server_cpu_cores", "client_cpu_cores", "master_cpu_cores", "master_throttled_usec", "peak_charged_memory_gib"]:
            self.check(finite(row.get(key)) and row[key] >= 0, "nonfinite_or_missing_metric", {"case": identity, "field": key})
        rate = row.get("batch_qps")
        self.check(finite(rate) and close(row.get("keys_s"), rate * row["batch"]), "keys_s_batch_qps_inconsistent", identity)
        self.check(type(row.get("sample_count")) is int and row["sample_count"] >= 2, "insufficient_resource_samples", identity)
        try:
            raw_name = row["raw_artifact"]
            raw = load(self.read_artifact(raw_name))
            parameters = raw["parameters"]
            self.check(raw.get("valid") is True and raw.get("failures") == 0 and not raw.get("fatal"), "sdk_result_not_valid", identity)
            for key, expected_value in [("op", row["operation"]), ("batch", row["batch"]), ("size", row["size"]), ("concurrency", row["clients"])]:
                self.check(parameters.get(key) == expected_value, "sdk_parameters_mismatch", {"case": identity, "field": key})
            native_run = f"{self.job_identity}-{row['variant_id']}-r{row['repeat']}-s{row['size']}"
            if row["operation"] == "put":
                native_run += "-put-b" + str(row["batch"])
            self.check(parameters.get("run_id") == native_run, "sdk_repeat_namespace_mismatch", identity)
            for key in ["keys_s", "batch_qps", "duration_s"]:
                self.check(close(raw.get(key), row.get(key)), "sdk_metric_mismatch", {"case": identity, "field": key})
            self.check(raw.get("process_exit_codes") == [0] * row["clients"] and len(raw.get("clients", [])) == row["clients"], "sdk_client_completion_mismatch", identity)
            success = raw["success"]
            self.check(type(success) is int and success > 0 and success == sum(client["success"] for client in raw["clients"]), "sdk_success_count_mismatch", identity)
            self.check(close(raw["keys_s"], success * row["batch"] / raw["duration_s"]), "sdk_throughput_recompute_failed", identity)
            p99, total = upper_percentile(raw["histogram"], .99)
            self.check(total == success and close(row.get("p99_us"), p99) and close(raw.get("p99", {}).get("upper_us"), p99), "p99_not_numeric_native_bucket_upper_bound", identity)
            self.check(row.get("short_window") is (raw["duration_s"] < 10) and raw.get("short_window") is row.get("short_window"), "short_window_flag_mismatch", identity)
            self.check(row.get("low_sample_p99") is (success < 1000) and raw.get("low_sample_p99") is row.get("low_sample_p99"), "p99_sample_flag_mismatch", identity)
            start, end = raw["measure_start_mono_ns"], raw["measure_end_mono_ns"]
            self.check(type(start) is int and type(end) is int and end > start and row.get("window_start_mono_ns") == start and row.get("window_end_mono_ns") == end, "resource_window_sdk_mismatch", identity)
            self.check(close((end - start) / 1e9, raw["duration_s"]), "sdk_duration_window_mismatch", identity)
            if row["operation"] != "put":
                self.check(raw["duration_s"] >= expected["requested_duration_s"] * .99, "timed_read_ended_early", identity)
            samples = [sample for sample in self.samples(raw_name) if start <= sample["mono_ns"] <= end]
            self.check(len(samples) == row.get("sample_count") and len(samples) >= 2, "resource_count_recompute_failed", identity)
            if len(samples) >= 2:
                seconds = (samples[-1]["mono_ns"] - samples[0]["mono_ns"]) / 1e9
                coverage = seconds / raw["duration_s"]
                self.check(coverage >= .9 if raw["duration_s"] >= 10 else seconds > 0, "resource_window_coverage_low", {"case": identity, "coverage": coverage})
                for role, field in [("server", "server_cpu_cores"), ("client", "client_cpu_cores"), ("server/master", "master_cpu_cores")]:
                    first = samples[0]["roles"][role]["cpu.stat"]
                    last = samples[-1]["roles"][role]["cpu.stat"]
                    recomputed = (last["usage_usec"] - first["usage_usec"]) / 1e6 / seconds
                    self.check(close(row.get(field), recomputed), "resource_cpu_recompute_failed", {"case": identity, "field": field})
                    if role == "server/master":
                        throttle = last.get("throttled_usec", 0) - first.get("throttled_usec", 0)
                        self.check(row.get("master_throttled_usec") == throttle, "master_throttle_recompute_failed", identity)
                peak = max(sample["roles"]["server"]["memory.current"] for sample in samples) / (1 << 30)
                self.check(close(row.get("peak_charged_memory_gib"), peak), "resource_memory_recompute_failed", identity)
                row = {**row, "resource_window_coverage": coverage, "native_success_calls": success,
                       "max_sample_gap_s": max((right["mono_ns"] - left["mono_ns"]) / 1e9 for left, right in zip(samples, samples[1:]))}
        except (KeyError, ValueError, TypeError, OSError, ZeroDivisionError) as error:
            self.check(False, "required_native_evidence_unavailable", {"case": identity, "error": str(error)})
        return {**row, "audit_passed": len(self.issues) == before}

    def run(self):
        evidence = load(self.evidence)
        job = evidence.get("job", evidence)
        complete = job.get("completion")
        result = {"name": self.label, "evidence_file": str(self.evidence), "evidence_sha256": sha(self.evidence),
                  "job_id": job.get("id", evidence.get("job_id")), "artifact_root": str(self.root) if self.root else None}
        self.job_identity = result["job_id"]
        if job.get("status") not in TERMINAL or not isinstance(complete, dict):
            return {**result, "status": "waiting", "passed": None, "reason": "The same run has no authoritative terminal completion; no missing-case verdict was produced", "observed_service_rows": len(job.get("results", []))}
        rows = complete.get("results")
        if not isinstance(rows, list):
            return {**result, "status": "failed", "passed": False, "issues": [{"code": "missing_remote_completion_results"}]}
        plan = job["plan"]
        expected = expected_matrix(plan)
        counts = Counter(case_key(row) for row in rows)
        missing = [list(key) for key in expected if counts[key] == 0]
        duplicates = [{"identity": list(key), "count": count} for key, count in counts.items() if count > 1]
        unexpected = [list(key) for key in counts if key not in expected]
        self.check(not missing and not duplicates and not unexpected, "cartesian_matrix_mismatch", {"missing": missing, "duplicates": duplicates, "unexpected": unexpected})
        self.check(len(expected) == plan.get("expected_cases"), "plan_expected_count_mismatch")
        self.check(complete.get("job_id") == result["job_id"], "completion_job_id_mismatch")
        self.check(job.get("status") == complete.get("status") == "succeeded", "job_not_fully_succeeded", {"job": job.get("status"), "completion": complete.get("status")})
        source = (job.get("provenance") or {}).get("source_sha256")
        self.check(isinstance(source, str) and bool(re.fullmatch(r"[a-f0-9]{64}", source)) and source == SOURCE, "frozen_source_provenance_mismatch", source)
        for record in complete.get("artifacts", []):
            if isinstance(record, dict) and isinstance(record.get("name"), str):
                self.check(record["name"] not in self.artifacts, "duplicate_artifact_identity", record["name"])
                self.artifacts[record["name"]] = record
        try:
            frozen_complete = load(path_in(self.root, "complete.json"))
            self.check(frozen_complete.get("results") == [{k: v for k, v in row.items() if k != "raw_url"} for row in rows], "frozen_completion_rows_mismatch")
            frozen_request = load(self.read_artifact("request.json"))
            self.check(frozen_request.get("plan") == plan and frozen_request.get("config") == job.get("config")
                       and frozen_request.get("job_id") == result["job_id"], "frozen_request_plan_mismatch")
            builds = load(self.read_artifact("build-verification.json"))
            products = {row["product"] for row in expected.values()}
            for name in products:
                build = builds[name]
                self.check(build.get("sha") == BUILDS[name], "frozen_build_sha_mismatch", name)
                self.check(bool(build.get("artifacts")) and all(value.get("valid") and value.get("sha256") == value.get("expected_sha256") for value in build["artifacts"].values()), "build_artifact_identity_rejected", name)
        except (KeyError, ValueError, TypeError, OSError) as error:
            self.check(False, "frozen_completion_or_build_evidence_missing", str(error))
        audited = [self.audit_row({**row, "source_sha256": source}, expected[case_key(row)]) for row in rows if case_key(row) in expected]
        return {**result, "status": "passed" if not self.issues else "failed", "passed": not self.issues,
                "source_sha256": source, "coverage": {"expected": len(expected), "actual_remote_completion_rows": len(rows), "missing": missing,
                  "duplicates": duplicates, "unexpected": unexpected, "invalid_count": sum(row.get("status") != "valid" for row in rows)},
                "checks": dict(self.checks), "issues": self.issues, "rows": audited}


def summarize(runs):
    grouped = defaultdict(list)
    for run in runs:
        if run.get("passed") is not True:
            continue
        for row in run.get("rows", []):
            if row.get("audit_passed"):
                grouped[tuple(row.get(field) for field in GROUP_FIELDS)].append({**row, "run_name": run["name"], "job_id": run["job_id"]})
    result = []
    for identity, rows in sorted(grouped.items(), key=lambda item: repr(item[0])):
        summary = {**dict(zip(GROUP_FIELDS, identity)), "observations": len(rows), "runs": sorted({row["run_name"] for row in rows}),
                   "repeat_identities": [{"run": row["run_name"], "job_id": row["job_id"], "repeat": row["repeat"]} for row in rows]}
        for metric in ["keys_s", "batch_qps", "p50_us", "p95_us", "p99_us", "duration_s", "server_cpu_cores", "client_cpu_cores", "master_cpu_cores", "master_throttled_usec", "peak_charged_memory_gib", "resource_window_coverage", "max_sample_gap_s"]:
            summary[metric] = stats([row.get(metric) for row in rows])
        for role in ["server", "client", "master"]:
            summary[role + "_cpu_quota_utilization_pct"] = stats([100 * row[role + "_cpu_cores"] / row[role + "_cpus"] for row in rows])
        summary["short_window_count"] = sum(row.get("short_window") is True for row in rows)
        summary["low_sample_p99_count"] = sum(row.get("low_sample_p99") is True for row in rows)
        summary["native_success_calls"] = stats([row.get("native_success_calls") for row in rows])
        result.append(summary)
    return result


def mappings(values):
    result = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not path or name in result:
            raise ValueError("Use distinct NAME=/path mappings")
        result[name] = Path(path).absolute()
    return result


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="append", required=True, help="NAME=live-driver terminal result JSON; repeat to combine studies")
    parser.add_argument("--artifact-root", action="append", default=[], help="NAME=local frozen job directory, preserving remote relative paths")
    parser.add_argument("--out", required=True, help="New audit JSON path; existing audit evidence is never overwritten")
    args = parser.parse_args()
    named, roots = mappings(args.run), mappings(args.artifact_root)
    if set(roots) - set(named):
        parser.error("Artifact roots must refer to a named run")
    runs = []
    for name, evidence in named.items():
        try:
            runs.append(Auditor(name, evidence, roots.get(name)).run())
        except (KeyError, ValueError, TypeError, OSError) as error:
            runs.append({"name": name, "status": "failed", "passed": False, "issues": [{"code": "invalid_evidence_envelope", "detail": str(error)}]})
    duplicates = Counter(run.get("job_id") for run in runs if run.get("job_id"))
    for run in runs:
        if duplicates.get(run.get("job_id"), 0) > 1:
            run.update(status="failed", passed=False)
            run.setdefault("issues", []).append({"code": "same_physical_run_supplied_more_than_once", "detail": run.get("job_id")})
            run["rows"] = []
    report = {"schema_version": 1, "created_wall_ns": time.time_ns(), "auditor_sha256": sha(Path(__file__)),
              "purpose": "Independent closed native matrix, histogram and resource-window audit; service missing placeholders are excluded",
              "scope": {"physical_hosts": 1, "latency": "native histogram upper bounds for complete batch calls",
                        "memory": "sampled server-role charged cgroup memory including file cache",
                        "put": "bounded fresh insert cardinality; short windows are marked, not timed upserts",
                        "source": "declared installed source SHA checked against frozen study004 pin; no claim of recompiled source here",
                        "grouping": list(GROUP_FIELDS), "waiting_is_not_failure": True},
              "status": "failed" if any(run.get("passed") is False for run in runs) else "waiting" if any(run.get("passed") is None for run in runs) else "passed",
              "runs": runs, "summaries": summarize(runs)}
    out = Path(args.out)
    with out.open("x") as stream:
        json.dump(json_safe(report), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"out": str(out), "status": report["status"], "runs": [{"name": run["name"], "status": run["status"], "issues": len(run.get("issues", []))} for run in runs], "summary_groups": len(report["summaries"])}, ensure_ascii=False))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
