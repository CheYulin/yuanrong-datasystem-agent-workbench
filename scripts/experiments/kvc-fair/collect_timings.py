#!/usr/bin/env python3
"""Collect recorded experiment durations without assigning clocks to unobserved stages."""
import argparse
from collections import Counter
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

UTC = dt.timezone.utc
OPERATIONS = {"put", "get", "exist_hit", "exist_miss"}


def utc_ns(value):
    if value is None:
        return None
    seconds, nanos = divmod(int(value), 1_000_000_000)
    return dt.datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}Z"


def utc(value):
    if value is None:
        return None
    stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("UTC conversion requires an explicit source timezone: " + str(value))
    stamp = stamp.astimezone(UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S") + f".{stamp.microsecond:06d}000Z"


def seconds(start, end):
    if start is None or end is None:
        return None
    return (dt.datetime.fromisoformat(end.replace("Z", "+00:00")) -
            dt.datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()


def event_utc(event):
    return utc(event["utc"]) if event.get("utc") else utc_ns(event.get("wall_ns"))


def event_duration(start, end):
    if end is None:
        return None, "target_event_monotonic"
    if start.get("mono_ns") is not None and end.get("mono_ns") is not None:
        return (end["mono_ns"] - start["mono_ns"]) / 1e9, "target_event_monotonic"
    return seconds(event_utc(start), event_utc(end)), "source_wall_clock"


def cell_fields(cell):
    match = re.fullmatch(r"r(\d+)-(.+)-s(\d+)-c(\d+)", cell or "")
    if not match:
        return {"cell": cell, "product": None}
    repeat, product, size, concurrency = match.groups()
    return {"cell": cell, "product": product, "repeat": int(repeat),
            "size": int(size), "concurrency": int(concurrency)}


class Collector:
    def __init__(self):
        self.steps = []
        self.issues = []
        self.sources = set()
        self.source_timestamps = []
        self.session_summaries = []
        self.overall_sessions = []

    def issue(self, evidence, message):
        self.issues.append({"evidence": str(evidence), "message": message})

    def read_json(self, path):
        path = Path(path)
        if not path.exists():
            self.issue(path, "Input unavailable; no timing inferred")
            return None
        self.sources.add(str(path))
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            self.issue(path, "Input may still be written; skipped this snapshot: " + str(exc))
            return None

    def read_lines(self, path, start_byte=0, end_byte=None):
        path = Path(path)
        if not path.exists():
            self.issue(path, "Input unavailable; no timing inferred")
            return []
        self.sources.add(str(path))
        records = []
        content = path.read_bytes()
        if start_byte > len(content) or end_byte is not None and end_byte > len(content):
            self.issue(path, "Recorded session byte range exceeds available events; no records inferred")
            return []
        first_line = content[:start_byte].count(b"\n") + 1
        for number, line in enumerate(content[start_byte:end_byte].decode("utf-8").splitlines(), first_line):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                row["_evidence"] = f"{path}#L{number}"
                records.append(row)
            except json.JSONDecodeError as exc:
                self.issue(f"{path}#L{number}", "Incomplete or invalid JSONL row retained as an issue: " + str(exc))
        return records

    def add(self, identity, name, category, start=None, end=None, duration=None,
            status="completed", evidence=None, scope="", clock="source_wall_clock",
            estimated=False, **extra):
        if duration is not None and duration < 0:
            self.issue(evidence, "Negative interval; clock order not assumed")
            duration = None
            status = "invalid_clock"
        self.steps.append({"id": identity, "name": name, "category": category,
                           "product": None, "cell": None, "operation": None,
                           "start_utc": start, "end_utc": end, "duration_s": duration,
                           "status": status, "evidence": evidence or [], "scope": scope,
                           "clock_domain": clock, "estimated": estimated, "additive": False,
                           **extra})

    def builds(self, directory):
        directory = Path(directory)
        if not directory.exists():
            self.issue(directory, "Build summaries unavailable")
            return
        paths = [directory] if directory.is_file() else sorted(directory.glob("*.json"))
        for path in paths:
            raw = self.read_json(path)
            if not raw or not raw.get("product"):
                continue
            product = str(raw["product"])
            key = "mooncake" if "mooncake" in product.lower() else product.lower()
            start, end = utc(raw.get("build_started_utc")), utc(raw.get("build_finished_utc"))
            timing = raw.get("timing_scope", {})
            notes = raw.get("build_scope_notes", raw.get("notes", []))
            scope = "Build-summary wall interval; exact inclusion details are in source_notes."
            if key == "datasystem":
                scope = "Official source build command including dependency preparation/build and packaging; not isolated compiler time."
            elif key == "mooncake":
                scope = "Source build wall interval includes the failed USE_HTTP=OFF configuration attempt and successful retry; excludes earlier dependency preparation. " + str(timing.get("timestamp_method", ""))
            elif key == "lmcache":
                scope = "Source wheel-build/install command wall interval; excludes preceding dependency downloads/installations."
            status = "completed" if raw.get("build_exit") == 0 else "failed" if raw.get("build_exit") is not None else "running" if not end else "unknown"
            duration = timing.get("own_compile_including_failed_configuration_attempt_s", raw.get("build_wall_seconds", seconds(start, end)))
            self.add(f"build:{key}", f"{product} build wall", "build", start, end,
                     duration if end else None, status, [str(path)], scope, product=key,
                     reported_exit=raw.get("build_exit"), source_tag=raw.get("tag"), source_sha=raw.get("sha"), source_notes=notes)
            if timing.get("dependency_download_install_and_all_compile_s") is not None:
                self.add(f"build:{key}:dependencies-total", f"{product} dependencies + build wall", "build_total",
                         utc(timing.get("dependency_download_install_start_utc")), end,
                         timing["dependency_download_install_and_all_compile_s"] if end else None,
                         status, [str(path) + "#/timing_scope"],
                         "Dependency download/install + source build attempts + inter-stage shell gaps; overlaps the source-build row; do not sum or rank as pure compile speed.",
                         product=key)
            if raw.get("source_compile_seconds") is not None:
                self.add(f"build:{key}:native", f"{product} native wheel build (reported)", "native_build",
                         duration=raw["source_compile_seconds"], status=status,
                         evidence=[str(path) + "#/source_compile_seconds"],
                         scope="Reported native wheel-build duration excludes dependencies; exact internal UTC start/end were not recorded, so no timeline position is invented.",
                         clock="reported_elapsed", product=key)
            if timing.get("failed_attempt_exit") is not None:
                self.add(f"build:{key}:failed-configuration", f"{product} failed build configuration attempt", "build_attempt",
                         start=start, status="failed", evidence=[str(path) + "#/timing_scope/failed_attempt_exit"],
                         scope="Failed USE_HTTP=OFF source-build attempt is included in the parent build wall interval; its separate finish time and duration are unavailable.",
                         product=key, reported_exit=timing["failed_attempt_exit"])

    def environment(self, supplied, suite):
        paths = []
        if supplied:
            path = Path(supplied)
            paths += sorted(path.glob("environment*.json")) if path.is_dir() else [path]
        formal_snapshot = Path(suite) / "environment.json"
        if formal_snapshot.exists() and formal_snapshot not in paths:
            paths.append(formal_snapshot)
        for path in paths:
            raw = self.read_json(path)
            if not raw:
                continue
            if raw.get("before", {}).get("utc"):
                start = utc(raw["before"]["utc"])
                end = utc(raw.get("after", {}).get("utc"))
                self.source_timestamps.append({"evidence": str(path), "before_utc_original": raw["before"]["utc"],
                                               "after_utc_original": raw.get("after", {}).get("utc"),
                                               "before_utc": start, "after_utc": end})
                if end:
                    self.add("environment:cleanup:" + path.name, "Environment archive + cleanup wall", "environment",
                             start, end, seconds(start, end), evidence=[str(path) + "#/before/utc", str(path) + "#/after/utc"],
                             scope="Observed before/after snapshot interval includes archive, validation/hash, cleanup, service-stop and shell gaps. Separate compression/copy/delete durations were not recorded.")
                else:
                    self.add("environment:snapshot:" + path.name, "Environment before snapshot", "snapshot",
                             start=start, status="observed", evidence=[str(path) + "#/before/utc"],
                             scope="Point-in-time observation; no elapsed preparation time recorded.", kind="instant")
            elif raw.get("wall_ns") is not None:
                self.add("environment:metadata:" + path.name, "Formal environment metadata snapshot", "snapshot",
                         start=utc_ns(raw["wall_ns"]), status="observed", evidence=[str(path) + "#/wall_ns"],
                         scope="Source wall-clock anchor for environment command collection; collection finish and duration were not recorded.", kind="instant")

    def operations(self, paths):
        for path in dict.fromkeys(paths):
            for index, raw in enumerate(self.read_lines(path), 1):
                start, end = utc_ns(raw.get("start_wall_ns")), utc_ns(raw.get("end_wall_ns"))
                status = "running" if end is None else "failed" if raw.get("error") or raw.get("returncode") not in (0, None) else "completed" if raw.get("returncode") == 0 else "unknown"
                duration = raw.get("duration_s") if end else None
                self.add(f"operation:{Path(path)}:{index}", raw.get("step", "Recorded command"), "local_operation",
                         start, end, duration, status, [raw["_evidence"]],
                         "Command wrapper wall interval measured by local monotonic clock; includes command startup, wait and wrapper exception handling; not SDK operation latency.",
                         clock="local_monotonic_elapsed_with_wall_endpoints", argv=raw.get("argv"), reported_exit=raw.get("returncode"))

    def result(self, path, raw, prefix, number=0, excluded=False, session_id=None, archived=False):
        params = raw.get("parameters", {})
        fields = cell_fields(path.parent.name)
        fields["operation"] = params.get("op", path.name.split(".")[0])
        fields["result_path"] = str(path)
        excluded = excluded or ".superseded.interference." in path.name
        fields.update(excluded_from_comparison=excluded, exclusion_reason_kind="interference_exclusion" if excluded else None,
                      original_valid=raw.get("valid"), session_id=session_id, archived=archived)
        start, end = utc_ns(raw.get("wall_start_ns")), utc_ns(raw.get("wall_end_ns"))
        status = "running" if end is None else "completed" if raw.get("valid") else "failed"
        if excluded and status == "completed":
            status = "superseded_interference"
        identity = f"{prefix}:result:{path.parent.name}:{path.name}:{number}"
        self.add(identity + ":harness", f"{path.parent.name} {fields['operation']} harness wall", "harness_wall",
                 start, end, (raw["wall_end_ns"] - raw["wall_start_ns"]) / 1e9 if start and end else None,
                 status, [str(path)],
                 "Harness internal wall interval includes client setup/preload, warmup/barriers, SDK measured window, validation and close; excludes Python imports before the recorded start. Overlaps command and SDK rows.",
                 clock="target_wall_clock", **fields)
        measured_start = utc_ns(raw.get("measure_start_wall_ns"))
        begin, finish = raw.get("measure_start_mono_ns"), raw.get("measure_end_mono_ns")
        elapsed = (finish - begin) / 1e9 if begin is not None and finish is not None else raw.get("duration_s")
        measured_end = utc_ns(raw["measure_start_wall_ns"] + finish - begin) if measured_start and begin is not None and finish is not None else None
        if measured_start or elapsed is not None:
            self.add(identity + ":sdk", f"{path.parent.name} {fields['operation']} SDK window", "sdk_window",
                     measured_start, measured_end, elapsed if finish is not None or raw.get("duration_s") is not None else None,
                     "superseded_interference" if excluded and measured_end and raw.get("valid") else "completed" if measured_end and raw.get("valid") else "failed" if end and not raw.get("valid") else "running",
                     [str(path) + "#/measure_start_wall_ns", str(path) + "#/measure_start_mono_ns", str(path) + "#/measure_end_mono_ns"],
                     "UTC start is the benchmark's wall anchor; UTC end adds the same-process monotonic elapsed window. Excludes setup/preload/warmup and later verification/close; PUT fixed-volume windows may be shorter than GET/EXIST windows.",
                     clock="target_monotonic_elapsed_with_explicit_wall_anchor", valid=raw.get("valid"), **fields)

    def suite(self, supplied, prefix="formal"):
        path = Path(supplied)
        directory = path.parent if path.is_file() else path
        history = directory / "history"
        archives = sorted([p for p in history.glob("session-*") if re.fullmatch(r"session-\d{4,}", p.name)],
                          key=lambda p: int(p.name.split("-")[-1])) if history.exists() else []
        if not archives:
            self.suite_session(supplied, prefix)
            return
        sessions = []
        last_metadata = None
        for archive in archives:
            metadata = self.read_json(archive / "session.json")
            if not metadata:
                continue
            last_metadata = metadata
            info = self.suite_session(archive, prefix + ":" + archive.name, metadata.get("events_range"), archived=True,
                                      session_id=archive.name, exclusions={row["cell"] for row in metadata.get("invalidation_plan", [])})
            sessions.append(info)
        if last_metadata is None:
            self.issue(history, "No readable historical session metadata; current session kept separate")
            self.suite_session(supplied, prefix + ":current")
            return
        current_events = directory / "events.jsonl"
        content = current_events.read_bytes() if current_events.exists() else b""
        begin = last_metadata["events_range"]["end_byte"]
        valid_prefix = len(content) >= begin and hashlib.sha256(content[:begin]).hexdigest() == last_metadata["events_prefix_sha256"]
        if not valid_prefix:
            self.issue(current_events, "Archived append prefix changed; current event attribution suppressed")
            begin = len(content)
        last_archive = history / last_metadata["session_id"]
        current_summary = directory / "summary.json"
        same_summary = current_summary.exists() and (last_archive / "summary.json").exists() and current_summary.read_bytes() == (last_archive / "summary.json").read_bytes()
        if not same_summary or len(content) > begin:
            stale_summary = same_summary and len(content) > begin
            if stale_summary:
                self.issue(current_summary, "New session events exist, but current summary still equals archived summary; new suite boundaries remain unknown")
            sessions.append(self.suite_session(supplied, prefix + ":current", {"start_byte": begin, "end_byte": len(content)},
                                                session_id="current", ignore_summary=stale_summary))
        first = sessions[0].get("start_utc") if sessions else None
        latest = sessions[-1] if sessions else {}
        end = latest.get("end_utc")
        durations = [row.get("duration_s") for row in sessions]
        overall = {"suite": str(directory), "sessions": sessions, "earliest_start_utc": first, "latest_end_utc": end,
                   "wall_span_including_gaps_s": seconds(first, end),
                   "sum_session_wall_s": sum(durations) if durations and all(value is not None for value in durations) else None,
                   "recorded_session_wall_s": sum(value for value in durations if value is not None),
                   "historical_failure_count": sum(len(row.get("source_summary", {}).get("failed", [])) for row in sessions),
                   "scope": "Original per-session summaries remain separate; wall span includes operator pauses, summed session wall excludes pauses. No missing original end is replaced by archive time."}
        self.overall_sessions.append(overall)
        self.add(prefix + ":overall", prefix + " multi-session wall span", "suite_overall", first, end,
                 seconds(first, end), "running" if latest.get("status") == "running" else "observed" if end is None else "completed",
                 [str(directory / "history/index.json"), str(current_summary)], overall["scope"],
                 historical_failure_count=overall["historical_failure_count"])

    def suite_session(self, supplied, prefix="formal", event_range=None, archived=False, session_id=None, ignore_summary=False, exclusions=()):
        path = Path(supplied)
        summary_path = path if path.is_file() else path / "summary.json"
        directory = summary_path.parent
        original_summary = self.read_json(summary_path) or {}
        summary = {} if ignore_summary else original_summary
        events = self.read_lines(directory / "events.jsonl", (event_range or {}).get("start_byte", 0), (event_range or {}).get("end_byte"))
        start, end = utc(summary.get("started")), utc(summary.get("ended"))
        terminal = next((e for e in reversed(events) if e.get("event") in ["suite_complete", "suite_fatal", "suite_interrupted"]), None)
        status = ("failed" if summary.get("fatal") else "running") if end is None else "failed" if summary.get("fatal") or summary.get("failed") else "completed"
        if archived and status == "running":
            status = "interrupted_unknown_end"
        info = {"session_id": session_id or prefix, "evidence": str(summary_path), "start_utc": start, "end_utc": end,
                "duration_s": seconds(start, end), "status": status, "archived": archived, "events_range": event_range,
                "source_summary": summary, "observed_summary": original_summary if ignore_summary else None}
        self.session_summaries.append(info)
        self.add(prefix + ":suite", prefix + " suite wall", "suite_wall", start, end,
                 seconds(start, end), status, [str(summary_path)],
                 "Original session summary interval includes startup/preparation, commands, teardown and monitor shutdown. Archived sessions without original end keep null end/duration; archive time is not a substitute.", session_id=info["session_id"], archived=archived)
        attempts, current, seen_cells = [], {}, set()
        for event in events:
            kind, cell = event.get("event"), event.get("cell")
            if cell:
                seen_cells.add(cell)
            if kind == "cell_start":
                attempt = {"cell": cell, "start": event, "end": None, "commands": [], "status": "running", "number": sum(a["cell"] == cell for a in attempts) + 1}
                attempts.append(attempt); current[cell] = attempt
            elif kind == "resume_skip":
                self.add(f"{prefix}:resume:{cell}:{event['_evidence']}", cell + " resume skip", "resume_skip",
                         start=event_utc(event), status="skipped", evidence=[event["_evidence"]],
                         scope="Existing valid result reused; no runtime duration measured for this skip event.", kind="instant", **cell_fields(cell))
            elif kind == "measure_prepare" and cell in current:
                current[cell]["commands"].append({"operation": event.get("operation"), "start": event, "end": None, "status": "running"})
            elif kind == "measure_complete" and cell in current:
                command = next((c for c in reversed(current[cell]["commands"]) if c["operation"] == event.get("operation") and c["end"] is None), None)
                if command:
                    command.update(end=event, status="completed")
            elif kind in ["cell_complete", "cell_failed"] and cell in current:
                attempt = current[cell]
                attempt.update(end=event, status="completed" if kind == "cell_complete" else "failed")
                for command in attempt["commands"]:
                    if command["end"] is None:
                        command.update(end=event, status="failed")
            elif kind in ["suite_fatal", "suite_interrupted"]:
                for attempt in attempts:
                    if attempt["end"] is None:
                        attempt.update(end=event, status="interrupted" if kind == "suite_interrupted" else "failed")
                        for command in attempt["commands"]:
                            if command["end"] is None:
                                command.update(end=event, status=attempt["status"])
        for index, attempt in enumerate(attempts):
            cell, first, last = attempt["cell"], attempt["start"], attempt["end"]
            fields = {**cell_fields(cell), "attempt": attempt["number"]}
            fields.update(session_id=info["session_id"], archived=archived)
            if archived and attempt["status"] == "running":
                attempt["status"] = "interrupted_unknown_end"
                for command in attempt["commands"]:
                    if command["status"] == "running":
                        command["status"] = "interrupted_unknown_end"
            identity = f"{prefix}:cell:{cell}:attempt-{attempt['number']}"
            duration, domain = event_duration(first, last)
            self.add(identity, cell + " active cell interval", "cell_wall", event_utc(first), event_utc(last) if last else None,
                     duration, attempt["status"], [first["_evidence"]] + ([last["_evidence"]] if last else []),
                     "cell_start through cell_complete/cell_failed; includes pre-stop/start, settle wait and benchmark commands; excludes finally-block teardown.", domain, **fields)
            preparation_end = attempt["commands"][0]["start"] if attempt["commands"] else last
            duration, domain = event_duration(first, preparation_end)
            self.add(identity + ":prepare", cell + " launch/preparation envelope", "launch_prepare",
                     event_utc(first), event_utc(preparation_end) if preparation_end else None, duration,
                     "completed" if attempt["commands"] else attempt["status"],
                     [first["_evidence"]] + ([preparation_end["_evidence"]] if preparation_end else []),
                     "Event-bounded aggregate includes prior-instance stop, service launch and fixed settle wait; individual deploy/start/settle durations were not separately recorded.", domain, **fields)
            for ordinal, command in enumerate(attempt["commands"], 1):
                begin, finish = command["start"], command["end"]
                duration, domain = event_duration(begin, finish)
                self.add(identity + f":command-{ordinal}", cell + " " + str(command["operation"]) + " command event interval", "command_wall",
                         event_utc(begin), event_utc(finish) if finish else None, duration, command["status"],
                         [begin["_evidence"]] + ([finish["_evidence"]] if finish else []),
                         "measure_prepare through measure_complete/failure event; includes cgroup/Python startup, setup/preload/warmup, measured SDK window, validation, close and event handling; not the SDK window.",
                         domain, operation=command["operation"], **fields)
            if last:
                boundary = attempts[index + 1]["start"] if index + 1 < len(attempts) else terminal
                if boundary and events.index(boundary) <= events.index(last):
                    boundary = None
                duration, domain = event_duration(last, boundary)
                self.add(identity + ":teardown", cell + " teardown/scheduling envelope (estimate)", "teardown_estimate",
                         event_utc(last), event_utc(boundary) if boundary else None, duration,
                         "estimated" if boundary else "unknown" if archived else "running", [last["_evidence"]] + ([boundary["_evidence"]] if boundary else []),
                         "Upper-bound event envelope after active cell completion, containing stop, empty-cgroup verification, summary write and scheduling/operator gaps. Exact teardown start/end were not recorded; this is not an exact stop-command duration.",
                         domain, estimated=True, **fields)
        for result_path in sorted((directory / "cells").glob("*/*.json")):
            operation = result_path.name.split(".")[0]
            if operation not in OPERATIONS:
                continue
            raw = self.read_json(result_path)
            if raw:
                if event_range is not None:
                    result_start = utc_ns(raw.get("wall_start_ns"))
                    lower = start or (event_utc(events[0]) if events else None)
                    if lower is None or result_start is None or result_start < lower or end and result_start > end:
                        continue
                self.result(result_path, raw, prefix, excluded=result_path.parent.name in exclusions,
                            session_id=info["session_id"], archived=archived)
        plan_path = directory / "plan.json"
        if plan_path.exists():
            for index, planned in enumerate(self.read_json(plan_path) or []):
                cell = f"r{planned['repeat']}-{planned['product']}-s{planned['size']}-c{planned['concurrency']}"
                if cell not in seen_cells:
                    self.add(prefix + ":pending:" + cell, cell + " pending", "cell_wall", status="not_run_in_session" if archived else "pending",
                             evidence=[str(plan_path) + f"#/{index}"],
                             scope="Planned cell has no start event in this source snapshot; start/end/duration remain null.",
                             session_id=info["session_id"], archived=archived, **cell_fields(cell))
        return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--builds", type=Path)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--orchestration", type=Path)
    parser.add_argument("--operations", type=Path, nargs="*", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    collector = Collector()
    collector.builds(args.builds or args.suite / "builds")
    collector.environment(args.environment, args.suite)
    collector.operations(args.operations)
    if args.orchestration:
        collector.suite(args.orchestration, "orchestration-smoke")
    collector.suite(args.suite)
    collector.steps.sort(key=lambda row: (row["start_utc"] is None, row["start_utc"] or "", row["id"]))
    output = {"schema_version": 1, "generated_utc": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"), "time_zone": "UTC", "duration_unit": "seconds",
              "steps": collector.steps, "status_counts": dict(Counter(row["status"] for row in collector.steps)),
              "source_timestamps": collector.source_timestamps, "sources": sorted(collector.sources), "issues": collector.issues,
              "session_summaries": collector.session_summaries, "overall_sessions": collector.overall_sessions,
              "rules": ["All UTC calendar endpoints come from recorded timezone-aware timestamps or Unix wall nanoseconds.",
                        "Monotonic elapsed durations are used only within the source process/host clock domain; raw monotonic values are never treated as Unix time.",
                        "SDK end UTC uses its explicit wall start anchor plus the same-process monotonic elapsed duration.",
                        "Unknown starts/ends/durations are null; running/pending cells are not assigned elapsed-to-now durations.",
                        "Teardown is an estimated event envelope unless exact stop events exist; compression/delete/warmup substages are not invented.",
                        "Rows overlap: suite, cell, command, harness, SDK and build-total rows must not be summed or compared as equal-scope compiler benchmarks.",
                        "Live input files are read sequentially, not as an atomic snapshot; event state may precede a newly completed result file."]}
    json_path = args.out if args.out.suffix == ".json" else args.out / "timings.json"
    csv_path = json_path.with_suffix(".csv")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_temp = json_path.with_suffix(json_path.suffix + ".tmp")
    json_temp.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    json_temp.replace(json_path)
    fields = ["id", "name", "category", "product", "cell", "operation", "attempt", "session_id", "archived", "excluded_from_comparison", "exclusion_reason_kind", "original_valid", "start_utc", "end_utc", "duration_s", "status", "clock_domain", "estimated", "additive", "scope", "evidence"]
    csv_temp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with csv_temp.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for step in collector.steps:
            writer.writerow({**step, "evidence": json.dumps(step["evidence"], ensure_ascii=False)})
    csv_temp.replace(csv_path)
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "steps": len(collector.steps), "status_counts": output["status_counts"], "issues": len(collector.issues)}))


if __name__ == "__main__":
    main()
