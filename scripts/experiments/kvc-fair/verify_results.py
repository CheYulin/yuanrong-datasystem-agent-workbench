#!/usr/bin/env python3
"""Verify completed local evidence; never run workloads or modify suite files.

Only current cells/<cell>/{put,get,exist_hit,exist_miss}.json are graded.
Historical attempts may remain archived. The verifier checks recorded counters,
histograms, byte-check counts and timing contracts; it does not rerun SDK calls
or prove unobserved recovery, hardware behavior or absence of unrecorded load.
Missing resource/monitor observations remain null and produce warnings.
"""
import argparse
import bisect
from collections import Counter
import datetime
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import tempfile


PRODUCTS = {"datasystem", "mooncake", "lmcache"}
OPS = ("put", "get", "exist_hit", "exist_miss")
EVENTS = ("low", "high", "max", "oom", "oom_kill", "oom_group_kill")
ROLES = ("server", "client", "monitor", "parent")


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return value


def near(actual, expected, label, abs_tol=1e-9):
    actual = number(actual, label)
    if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=abs_tol):
        raise ValueError(f"{label} mismatch: recorded={actual}, recomputed={expected}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    with path.open() as stream:
        return json.load(stream)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def histogram(raw, label):
    require(isinstance(raw, dict), label + " must be a histogram object")
    result = Counter()
    for key, count in raw.items():
        index = int(key)
        require(str(index) == str(key) and index >= 384, label + " has invalid bucket index")
        count = integer(count, label + " bucket count")
        require(index not in result, label + " has duplicate normalized bucket index")
        if count:
            result[index] = count
    return result


def percentile(hist, fraction):
    rank = math.ceil(sum(hist.values()) * fraction)
    seen = 0
    for index, count in sorted(hist.items()):
        seen += count
        if seen >= rank:
            exponent, sub = divmod(index, 64)
            return {"lower_us": ((64 + sub) << (exponent - 6)) / 1000,
                    "upper_us": ((65 + sub) << (exponent - 6)) / 1000}
    return None


def client_indices(rows, concurrency, label):
    require(isinstance(rows, list) and len(rows) == concurrency, label + " count mismatch")
    indices = [integer(row.get("client"), label + " client index") for row in rows]
    require(sorted(indices) == list(range(concurrency)), label + " client indices must be unique and complete")


def verify_parameters(p, op, params, root_params, product):
    size, concurrency = params["size"], params["concurrency"]
    smoke = root_params.get("smoke")
    require(type(smoke) is bool, "suite parameters.smoke must be boolean")
    total_bytes = max(size * concurrency * 4, 1 << 20) if smoke else min(2 << 30, size * 65536)
    for key, expected in (("op", op), ("size", params["size"]), ("concurrency", params["concurrency"]),
                          ("duration", root_params["duration"]), ("warmup", root_params["warmup"]),
                          ("total_bytes", total_bytes), ("preload_bytes", 1 << 20 if smoke else 64 << 20),
                          ("run_id", "r{repeat}-s{size}-c{concurrency}".format(**params)),
                          ("adapter", product["adapter"]), ("config", product["config"])):
        require(p.get(key) == expected, f"parameters.{key} mismatch")
    require(type(p.get("skip_preload")) is bool and p["skip_preload"] == op.startswith("exist"),
            "parameters.skip_preload must be true only for EXIST operations")


def verify_operation(path, cell, op, params, root_params, formal, product):
    raw = read_json(path)
    p = raw.get("parameters", {})
    verify_parameters(p, op, params, root_params, product)
    require(raw.get("valid") is True and not raw.get("fatal"), "result is invalid or fatal")
    success = integer(raw.get("success"), "success", 1)
    require(raw.get("failures") == 0, "global failures must be zero")
    start = integer(raw.get("measure_start_mono_ns"), "measure_start_mono_ns", 1)
    end = integer(raw.get("measure_end_mono_ns"), "measure_end_mono_ns", start)
    duration = (end - start) / 1e9
    require(duration > 0, "SDK window must be nonempty")
    near(raw.get("duration_s"), duration, "SDK duration_s")
    wall_start = integer(raw.get("measure_start_wall_ns"), "measure_start_wall_ns", 1)
    clients = raw.get("clients")
    concurrency, size = params["concurrency"], params["size"]
    client_indices(clients, concurrency, "clients")
    setup, warm = raw.get("setup"), raw.get("warmup")
    client_indices(setup, concurrency, "setup")
    client_indices(warm, concurrency, "warmup")
    require(all(x.get("ready") is True for x in setup), "setup ready barrier incomplete")
    require(all(x.get("warm") is True for x in warm), "warmup barrier incomplete")
    total = 0
    merged = Counter()
    offsets = []
    sampled_validations = 0
    for client in clients:
        count = integer(client.get("success"), "client success", 1)
        require(client.get("failures") == 0 and client.get("errors") == [], "client failures/errors must be zero/empty")
        hist = histogram(client.get("histogram"), "client histogram")
        require(sum(hist.values()) == count, "client histogram count differs from success")
        require(sum(client.get("per_second", {}).values()) == count, "client per-second count differs from success")
        first = integer(client.get("first_ns"), "client first_ns", start)
        last = integer(client.get("last_ns"), "client last_ns", first)
        require(last <= end, "client last_ns exceeds SDK window end")
        offset = number(client.get("start_offset_us"), "start_offset_us")
        near(offset, (first - start) / 1000, "start_offset_us")
        offsets.append(offset)
        validations = integer(client.get("validations"), "sampled validations")
        if op == "get":
            require(validations == (count + 1023) // 1024, "GET sampled byte-check count mismatch")
        sampled_validations += validations
        if op == "put":
            target = integer(client.get("target"), "client PUT target", 1)
            expected_target = integer(p.get("total_bytes"), "total_bytes", 1) // size // concurrency
            require(target == expected_target and count == target, "PUT success/target count mismatch")
        total += count
        merged.update(hist)
    require(max(x["last_ns"] for x in clients) == end, "SDK end is not maximum client last_ns")
    require(total == success, "client success sum differs from global success")
    global_hist = histogram(raw.get("histogram"), "global histogram")
    require(sum(global_hist.values()) == success, "global histogram count differs from success")
    require(merged == global_hist, "merged client histogram differs from original global histogram")
    for key, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
        expected = percentile(global_hist, fraction)
        actual = raw.get(key)
        require(isinstance(actual, dict), key + " missing")
        for bound in ("lower_us", "upper_us"):
            near(actual.get(bound), expected[bound], key + "." + bound)
    near(raw.get("qps"), success / duration, "qps")
    if op in ("put", "get"):
        near(raw.get("throughput_mib_s"), success * size / duration / (1 << 20), "throughput_mib_s")
    else:
        require(raw.get("throughput_mib_s") is None, "EXIST byte throughput must remain null")
    near(raw.get("mean_us"), sum(x["latency_sum_ns"] for x in clients) / success / 1000, "mean_us")
    near(raw.get("max_us"), max(x["latency_max_ns"] for x in clients) / 1000, "max_us")
    short = duration < 10
    require(raw.get("short_window") is short, "short_window label mismatch")
    post = raw.get("post_validation")
    client_indices(post, concurrency, "post_validation barrier")
    require(all(x.get("post_validations") == (3 if op == "put" else 0) for x in post), "PUT requires exactly three post-measure byte checks per client; other ops require zero")
    return {"cell": cell, "operation": op, "success": success, "clients": concurrency,
            "duration_s": duration, "max_start_offset_us": max(offsets), "short_window": short,
            "sampled_byte_checks": sampled_validations, "post_byte_checks": sum(x["post_validations"] for x in post),
            "start_mono_ns": start, "end_mono_ns": end,
            "start_wall_ns": wall_start, "end_wall_ns": wall_start + end - start,
            "path": str(path), "sha256": sha256(path)}


class EventCounters:
    def __init__(self):
        self.samples = 0
        self.first_mono = self.last_mono = None
        self.values = {role: {} for role in ROLES}
        self.last_procs = {role: None for role in ROLES}

    def add(self, raw):
        self.samples += 1
        self.first_mono = raw["mono_ns"] if self.first_mono is None else self.first_mono
        self.last_mono = raw["mono_ns"]
        for role in ROLES:
            group = raw.get("parent") if role == "parent" else (raw.get("groups") or {}).get(role)
            group = group or {}
            self.last_procs[role] = group.get("cgroup.procs")
            events = group.get("memory.events")
            for key in EVENTS:
                value = events.get(key) if isinstance(events, dict) else None
                if type(value) is not int:
                    continue
                state = self.values[role].get(key)
                if state is None:
                    self.values[role][key] = {"first": value, "last": value, "observations": 1, "counter_resets": 0}
                else:
                    state["counter_resets"] += int(value < state["last"])
                    state["last"] = value
                    state["observations"] += 1

    def export(self):
        output = {"samples": self.samples, "first_sample_mono_ns": self.first_mono,
                  "last_sample_mono_ns": self.last_mono, "memory_event_counters": {},
                  "last_observed_cgroup_procs": self.last_procs}
        for role in ROLES:
            output["memory_event_counters"][role] = {}
            for key in EVENTS:
                state = self.values[role].get(key)
                if state is None:
                    output["memory_event_counters"][role][key] = {"first": None, "last": None, "observations": 0, "counter_resets": None, "delta": None}
                else:
                    valid = state["observations"] >= 2 and state["counter_resets"] == 0
                    output["memory_event_counters"][role][key] = dict(state, delta=state["last"] - state["first"] if valid else None)
        return output


def resources(suite, windows, warnings):
    path = suite / "resources/samples.jsonl"
    total = EventCounters()
    ordered = sorted(windows, key=lambda r: r["start_mono_ns"])
    starts = [r["start_mono_ns"] for r in ordered]
    per_window = [EventCounters() for _ in ordered]
    malformed = 0
    previous = None
    if path.exists():
        with path.open() as stream:
            for line in stream:
                try:
                    raw = json.loads(line)
                    mono = integer(raw.get("mono_ns"), "sample mono_ns", 1)
                    if previous is not None and mono <= previous:
                        warnings.append("Resource samples are not strictly increasing; monitor restarts/clock provenance need inspection")
                    previous = mono
                    total.add(raw)
                    index = bisect.bisect_right(starts, mono) - 1
                    if index >= 0 and mono <= ordered[index]["end_mono_ns"]:
                        per_window[index].add(raw)
                except (ValueError, TypeError, KeyError, AttributeError):
                    malformed += 1
    else:
        warnings.append("Resource samples are missing; cgroup event deltas and runtime resource coverage are unknown")
    if malformed:
        warnings.append(f"{malformed} resource sample lines could not be parsed; coverage is incomplete")
    summary = total.export()
    summary["malformed_lines"] = malformed
    summary["scope"] = "Observed first-to-last counter deltas only; one/missing sample or a counter reset yields null. Last sampled PIDs do not prove post-cleanup process state."
    summary["windows"] = [{"cell": w["cell"], "operation": w["operation"], **stats.export()} for w, stats in zip(ordered, per_window)]
    unknown = sum(stats.samples < 2 for stats in per_window)
    summary["resource_coverage_complete"] = bool(windows) and unknown == 0 and malformed == 0 and all(
        all(state["delta"] is not None for role in ("server", "client")
            for state in stats.export()["memory_event_counters"][role].values()) for stats in per_window)
    if unknown:
        warnings.append(f"{unknown} SDK windows have fewer than two resource samples; their event deltas remain unknown")
    for role, events in summary["memory_event_counters"].items():
        for key, values in events.items():
            if values["counter_resets"]:
                warnings.append(f"{role} memory.events.{key} counter decreased; aggregate delta is unknown")
            if values["delta"] is not None and values["delta"] > 0:
                warnings.append(f"Observed {role} memory.events.{key} delta={values['delta']} over the sampled suite span; inspect whether it affects SDK windows")
    manifest_path = suite / "resources/monitor-manifest.json"
    if not manifest_path.exists():
        summary["monitor"] = {"finished": None, "manifest": None, "commands": None}
        warnings.append("Monitor manifest is missing; actual monitor/tool termination state is unknown")
    else:
        manifest = read_json(manifest_path)
        commands = {}
        for name, row in manifest.get("commands", {}).items():
            code = row.get("returncode")
            available = row.get("available")
            log = suite / "resources" / (name + ".log")
            commands[name] = {"available": available, "returncode": code, "log_bytes": log.stat().st_size if log.is_file() else None,
                              "termination": "unavailable" if available is False else "unknown" if code is None else "exit_zero" if code == 0 else "interrupted" if code in (-2, 130) else "exit_nonzero"}
            if available is not False and code not in (0, -2, 130):
                warnings.append(f"Monitor tool {name} returncode={code}; usable output is not assumed")
        finished = True if manifest.get("end_utc") else None
        summary["monitor"] = {"finished": finished, "start_utc": manifest.get("start_utc"), "end_utc": manifest.get("end_utc"), "commands": commands,
                              "manifest": str(manifest_path), "manifest_sha256": sha256(manifest_path),
                              "scope": "Actual latest manifest and tool return codes; this is not proof that every hardware counter/tool produced valid samples for earlier monitor sessions."}
        if finished is not True:
            warnings.append("Monitor end_utc is absent; completed monitor shutdown is not established")
    return summary


def verify(args):
    suite = args.suite.resolve()
    errors, warnings, checks, windows = [], [], [], []
    result = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "suite": str(suite), "formal": args.formal,
              "scope": "Recorded current SDK results and listed interference spans only; no runtime tests are executed", "errors": errors, "warnings": warnings, "checks": checks}

    def check(name, callback):
        try:
            value = callback()
            checks.append({"name": name, "passed": True})
            return value
        except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError, AttributeError) as exc:
            errors.append({"check": name, "error": str(exc)})
            checks.append({"name": name, "passed": False})
            return None

    def plan_check():
        summary = read_json(suite / "summary.json")
        plan = read_json(suite / "plan.json")
        params = read_json(suite / "parameters.json")
        products = read_json(suite / "products.json")
        require(summary.get("ended") and not summary.get("fatal") and summary.get("failed") == [], "suite is unfinished, fatal or has failed cells")
        sizes, conc, repeats = params["sizes"], params["concurrencies"], integer(params["repeats"], "repeats", 1)
        require(sizes and conc and len(set(sizes)) == len(sizes) and len(set(conc)) == len(conc), "matrix dimensions empty or duplicate")
        for value in sizes + conc:
            integer(value, "matrix dimension", 1)
        names = {p["product"] for p in plan}
        require(names and names <= PRODUCTS, "plan contains missing/unknown products")
        require(type(params.get("smoke")) is bool, "suite parameters.smoke must be boolean")
        require(isinstance(products, list) and len(products) == len(names), "suite product manifest count mismatch")
        product_names = [p["name"] for p in products]
        require(len(set(product_names)) == len(product_names) and set(product_names) == names,
                "suite product manifest contains duplicate, missing or unexpected products")
        for product in products:
            for key in ("adapter", "config"):
                require(isinstance(product.get(key), str) and product[key], f"product {product['name']} {key} path missing")
        if params.get("products") is not None:
            require(set(params["products"]) == names, "product selection differs from plan")
        expected = {(r, n, s, c) for r, n, s, c in itertools.product(range(repeats), names, sizes, conc)}
        actual = [(p["repeat"], p["product"], p["size"], p["concurrency"]) for p in plan]
        require(len(actual) == len(expected) and set(actual) == expected, "plan does not match the complete configured grid")
        ids = {f"r{r}-{n}-s{s}-c{c}" for r, n, s, c in expected}
        completed = summary.get("completed", [])
        require(len(completed) == len(ids) and set(completed) == ids, "completed cells missing, duplicated or unexpected")
        if args.formal:
            require(names == PRODUCTS and set(sizes) == {1024, 65536, 1048576} and set(conc) == {2, 8, 16} and repeats == 3,
                    "--formal requires three products, sizes1KiB/64KiB/1MiB, concurrency2/8/16 and three repeats")
            require(params.get("duration") == 20 and params.get("warmup") == 3 and params.get("smoke") is False, "--formal requires20s/3s and smoke=false")
        expected_ops = sum(4 if p["size"] == min(sizes) else 2 for p in plan)
        result.update(expected_cells=len(expected), expected_results=expected_ops, products=sorted(names), parameters=params,
                      manifest_hashes={name: sha256(suite / name) for name in ("summary.json", "plan.json", "parameters.json", "products.json")})
        return plan, params, {p["name"]: p for p in products}

    configured = check("complete_plan_and_summary", plan_check)
    if configured is not None:
        plan, params, products = configured
        for p in plan:
            cell = "r{repeat}-{product}-s{size}-c{concurrency}".format(**p)
            ops = ["put", "get"] + (["exist_hit", "exist_miss"] if p["size"] == min(params["sizes"]) else [])
            for op in ops:
                path = suite / "cells" / cell / (op + ".json")
                row = check(cell + "/" + op, lambda: verify_operation(path, cell, op, p, params, args.formal, products[p["product"]]))
                if row is not None:
                    windows.append(row)
        check("all_expected_current_results", lambda: require(len(windows) == result["expected_results"], "not every expected current operation passed integrity checks"))
    result["verified_results"] = len(windows)
    result["max_start_offset_us"] = max((w["max_start_offset_us"] for w in windows), default=None)
    result["short_windows"] = [{k: w[k] for k in ("cell", "operation", "duration_s")} for w in windows if w["short_window"]]
    result["operations"] = windows

    if args.interference_json is None:
        result["interference"] = {"checked": None, "operation_spans": None, "overlaps": None, "padding_seconds": 2}
        warnings.append("No interference JSON supplied; exclusion of recorded external operations is not checked")
        checks.append({"name": "recorded_interference_excluded", "passed": None})
    else:
        def interference_check():
            source = read_json(args.interference_json)
            spans = source["operation_spans"]
            require(isinstance(spans, list), "operation_spans must be a list")
            overlaps = []
            for span in spans:
                start = integer(span.get("start_wall_ns"), "interference start_wall_ns", 1)
                end = integer(span.get("end_wall_ns"), "interference end_wall_ns", start)
                for window in windows:
                    if window["start_wall_ns"] < end + 2_000_000_000 and window["end_wall_ns"] > start - 2_000_000_000:
                        overlaps.append({"cell": window["cell"], "operation": window["operation"], "step": span.get("step"), "span_start_wall_ns": start, "span_end_wall_ns": end})
            result["interference"] = {"checked": True, "operation_spans": spans, "overlaps": overlaps, "padding_seconds": 2,
                                      "checked_sdk_windows": len(windows), "all_current_windows_checked": len(windows) == result.get("expected_results"),
                                      "input_sha256": sha256(args.interference_json), "clock_scope": source.get("clock_scope"),
                                      "scope": "Only current standard operation JSON SDK windows are checked, using recorded wall anchors plus/minus2s; archived attempts and unrecorded load are outside this assertion."}
            require(not overlaps, f"{len(overlaps)} current SDK windows still overlap recorded interference with2s padding")
        check("recorded_interference_excluded", interference_check)
    resource = check("resource_evidence_inspected", lambda: resources(suite, windows, warnings))
    result["resources"] = resource
    monitor_finished = resource["monitor"]["finished"] if resource else None
    checks.append({"name": "monitor_shutdown_observed", "passed": monitor_finished})
    result["passed"] = not errors
    result["unknown_checks"] = [c["name"] for c in checks if c["passed"] is None]
    result["evidence_complete"] = result["passed"] and not result["unknown_checks"] and bool(resource and resource["resource_coverage_complete"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--interference-json", type=Path)
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=args.out.parent, prefix=args.out.name + ".tmp-", delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        pending.replace(args.out)
    finally:
        pending.unlink(missing_ok=True)
    print(json.dumps({"passed": result["passed"], "evidence_complete": result["evidence_complete"], "verified_results": result["verified_results"], "expected_results": result.get("expected_results"), "errors": len(result["errors"]), "warnings": len(result["warnings"]), "out": str(args.out)}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
