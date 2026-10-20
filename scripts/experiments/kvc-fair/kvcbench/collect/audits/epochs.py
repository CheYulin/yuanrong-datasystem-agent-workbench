"""Validate separate service lifetimes for the hot read set and each fresh PUT."""
import json
import math
from pathlib import Path


PROTOCOL = "separate-read-and-fresh-put-v1"


def verify(suite, plan, parameters, summary, rows, events):
    issues, epochs, expected, windows = [], [], [], []
    row_map = {row["identity"]: row for row in rows}
    for group in plan:
        name = f"r{group['repeat']}-{group['product']}"
        specs = group["operations"]
        read = [spec for spec in specs if spec["operation"] != "put"]
        put = [spec for spec in specs if spec["operation"] == "put"]
        if specs != read + put or group.get("workload_protocol") != PROTOCOL:
            issues.append({"group": name, "errors": ["Read operations must precede all fresh PUT epochs"]})
        if any(spec.get("epoch") != ("read" if spec["operation"] != "put" else f"put-b{spec['batch']}") for spec in specs):
            issues.append({"group": name, "errors": ["Operation assigned to an invalid read/PUT epoch"]})
        wanted = [{"id": "read", "kind": "read", "operations": [spec for spec in read if spec["supported"]]}]
        wanted += [{"id": spec["epoch"], "kind": "fresh_put", "operations": [spec]} for spec in put if spec["supported"]]
        if group.get("epochs") != wanted:
            issues.append({"group": name, "errors": ["Service epochs differ from the native supported read/PUT contract"]})
        for epoch in wanted:
            identity = name + "/" + epoch["id"]
            expected.append(identity)
            source = Path(suite) / name / "epochs" / epoch["id"] / "epoch-summary.json"
            errors = []
            if not source.is_file() or source.is_symlink():
                issues.append({"epoch": identity, "errors": ["Missing regular service epoch summary"]})
                continue
            raw = json.loads(source.read_text())
            failures = [name + f"/b{spec['batch']}-{spec['operation']}" for spec in epoch["operations"]
                        if row_map.get(name + f"/b{spec['batch']}-{spec['operation']}", {}).get("status") == "failed"]
            continued = parameters.get("continue_readonly_errors") is True
            valid = not failures
            run_dir = str(Path(parameters.get("root", "/missing")) / "run" / (parameters["action"] + "-" + name + "-" + epoch["id"]))
            if (raw.get("identity") != identity or raw.get("kind") != epoch["kind"]
                    or raw.get("operations") != epoch["operations"] or raw.get("run_dir") != run_dir
                    or raw.get("workload_protocol") != PROTOCOL or raw.get("valid") is not valid
                    or continued and (raw.get("execution_complete") is not True or raw.get("measurement_failures") != failures)):
                errors.append("Service lifetime identity/operations/directory or successful completion mismatch")
            start, end = raw.get("start_mono_ns"), raw.get("end_mono_ns")
            if (type(start) is not int or type(end) is not int or end <= start
                    or type(raw.get("duration_s")) not in (float, int) or not math.isfinite(raw["duration_s"])
                    or abs(raw["duration_s"] - (end - start) / 1e9) > 1e-9):
                errors.append("Service lifetime lacks its actual monotonic duration")
            else:
                windows.append((start, end, identity))
            drained = raw.get("drained", {})
            members = drained.get("members", {})
            members = members if isinstance(members, dict) else {}
            paths = {role: [path for path in members if path.endswith('/' + role + '/cgroup.procs')] for role in ['server', 'client']}
            if (not drained.get("checked_utc") or drained.get("populated") != [] or len(members) != 2
                    or any(type(value) is not list or value != [] for value in members.values())
                    or not all(len(paths[role]) == 1 for role in paths)):
                errors.append("Service lifetime lacks explicit empty server/client cgroups")
            starts = [event for event in events if event.get("event") == "epoch_start" and event.get("group") == name and event.get("epoch") == epoch["id"]]
            ends = [event for event in events if event.get("event") == "epoch_complete" and event.get("group") == name and event.get("epoch") == epoch["id"]]
            if (len(starts) != 1 or starts[0].get("run_dir") != run_dir or starts[0].get("epoch_kind") != epoch["kind"]
                    or len(ends) != 1 or ends[0].get("valid") is not valid
                    or continued and (ends[0].get("execution_complete") is not True or ends[0].get("measurement_failures") != failures)):
                errors.append("Unique actual epoch start/cleanup completion events are required")
            for spec in epoch["operations"]:
                operation_id = name + f"/b{spec['batch']}-{spec['operation']}"
                row = row_map.get(operation_id, {})
                row["epoch"] = epoch["id"]
                row["epoch_evidence"] = "results/" + source.relative_to(suite).as_posix()
                if (row.get("valid") or row.get("status") == "failed") and (type(start) is not int or type(end) is not int
                                         or row.get("start_mono_ns", -1) < start or row.get("end_mono_ns", end + 1) > end):
                    errors.append("SDK window is outside its own fresh service lifetime")
            epochs.append({**raw, "evidence": "results/" + source.relative_to(suite).as_posix()})
            if errors:
                issues.append({"epoch": identity, "errors": errors})
    if summary.get("epochs_expected") != expected or summary.get("epochs_completed") != expected:
        issues.append({"errors": ["Every planned separate service lifetime must complete, in order"]})
    if summary.get("workload_protocol") != PROTOCOL:
        issues.append({"errors": ["Terminal suite does not identify the isolated workload protocol"]})
    directories = [epoch.get("run_dir") for epoch in epochs]
    if len(set(directories)) != len(directories):
        issues.append({"errors": ["Separate service lifetimes reused a run directory"]})
    for previous, current in zip(windows, windows[1:]):
        if previous[1] > current[0]:
            issues.append({"epochs": [previous[2], current[2]], "errors": ["Service lifetimes overlap"]})
    return issues, epochs
