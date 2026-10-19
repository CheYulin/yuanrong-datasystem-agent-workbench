#!/usr/bin/env python3
"""Generate evidence-led ECharts report without inventing missing measurements."""
import argparse
import bisect
from collections import defaultdict
import csv
import datetime
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile


def io_device(text, device):
    if text is None:
        return None
    for line in (text or "").splitlines():
        parts = line.split()
        if parts and parts[0] == device:
            return {k: int(v) for item in parts[1:] for k,v in [item.split("=", 1)]}
    return {}


def reduce_samples(path, device):
    samples = []
    manifest = path.with_name("monitor-manifest.json")
    ticks = json.loads(manifest.read_text()).get("clk_tck", 100) if manifest.exists() else 100
    if not path.exists():
        return samples
    with path.open() as stream:
        for line in stream:
            raw = json.loads(line)
            groups = raw["groups"]
            row = {"mono_ns": raw["mono_ns"], "wall_ms": raw["wall_ns"] // 1000000,
                   "sampling_ms": raw["sampling_ms"]}
            host_cpu = (raw.get("host", {}).get("stat") or "").splitlines()
            if host_cpu:
                cpu = [int(v) for v in host_cpu[0].split()[1:9]]
                row["host_busy_ticks"] = sum(cpu) - cpu[3] - cpu[4]
            for role in ["server", "client", "monitor"]:
                group = groups.get(role, {})
                cpu = group.get("cpu.stat") or {}
                mem = group.get("memory.stat") or {}
                io = io_device(group.get("io.stat"), device)
                row[role] = {"cpu_usec": cpu.get("usage_usec"), "throttled_usec": cpu.get("throttled_usec"),
                             "memory": group.get("memory.current"), "shmem": mem.get("shmem"),
                             "anon": mem.get("anon"), "file": mem.get("file"),
                             "read_bytes": io.get("rbytes", 0) if io is not None else None,
                             "write_bytes": io.get("wbytes", 0) if io is not None else None,
                             "events": group.get("memory.events")}
            if samples:
                row["prev_mono_ns"] = samples[-1]["mono_ns"]
                dt = (row["mono_ns"] - samples[-1]["mono_ns"]) / 1e9
                for role in ["server", "client", "monitor"]:
                    for metric in ["cpu_usec", "read_bytes", "write_bytes"]:
                        current, previous = row[role].get(metric), samples[-1][role].get(metric)
                        row[role][metric + "_per_s"] = (current - previous) / dt if current is not None and previous is not None else None
                if "host_busy_ticks" in row and "host_busy_ticks" in samples[-1]:
                    row["host_cpu_cores"] = (row["host_busy_ticks"] - samples[-1]["host_busy_ticks"]) / ticks / dt
                    used = sum((row[role].get("cpu_usec_per_s") or 0) / 1e6 for role in ["server", "client", "monitor"])
                    row["other_cpu_cores_estimate"] = max(0, row["host_cpu_cores"] - used)
            samples.append(row)
    return samples


def rows_from_results(suite, samples):
    rows = []
    times = [s["mono_ns"] for s in samples]
    for path in sorted((suite / "cells").glob("*/*.json")):
        if path.stem not in ["put", "get", "exist_hit", "exist_miss"]:
            continue
        raw = json.loads(path.read_text())
        match = re.fullmatch(r"r(\d+)-(.+)-s(\d+)-c(\d+)", path.parent.name)
        if not match:
            continue
        repeat, product, size, concurrency = match.groups()
        row = {"product": product, "repeat": int(repeat), "size": int(size), "concurrency": int(concurrency),
               "operation": path.stem, "valid": bool(raw.get("valid")), "error": raw.get("fatal"),
               "qps": raw.get("qps"), "throughput_mib_s": raw.get("throughput_mib_s"),
               "duration_s": raw.get("duration_s"), "success": raw.get("success"), "failures": raw.get("failures"),
               "mean_us": raw.get("mean_us"), "max_us": raw.get("max_us"), "short_window": raw.get("short_window"),
               "p50_us": (raw.get("p50") or {}).get("upper_us"), "p99_us": (raw.get("p99") or {}).get("upper_us"),
               "p99_lower_us": (raw.get("p99") or {}).get("lower_us"),
               "start_mono_ns": raw.get("measure_start_mono_ns"), "end_mono_ns": raw.get("measure_end_mono_ns"),
               "evidence": "results/" + path.parent.name + "/" + path.name}
        if row["valid"] and sum(raw.get("histogram", {}).values()) != row["success"]:
            raise ValueError("histogram count mismatch " + str(path))
        if times and row["start_mono_ns"] and row["end_mono_ns"]:
            left = bisect.bisect_left(times, row["start_mono_ns"])
            right = bisect.bisect_right(times, row["end_mono_ns"])
            inside = samples[left:right]
            row["resource_samples"] = len(inside)
            row["peak_memory_gib"] = max((s["server"]["memory"] + s["client"]["memory"]) / (1 << 30) for s in inside) if inside else None
            if len(inside) >= 2:
                first, last = inside[0], inside[-1]
                seconds = (last["mono_ns"] - first["mono_ns"]) / 1e9
                row["resource_span_s"] = seconds
                row["resource_coverage"] = seconds / row["duration_s"]
                for role in ["server", "client"]:
                    row[role + "_cpu_cores"] = (last[role]["cpu_usec"] - first[role]["cpu_usec"]) / 1e6 / seconds
                    row[role + "_throttle_ms"] = (last[role]["throttled_usec"] - first[role]["throttled_usec"]) / 1000
                    a, b = last[role]["write_bytes"], first[role]["write_bytes"]
                    row[role + "_write_mib_s"] = (a - b) / (1 << 20) / seconds if a is not None and b is not None else None
                row["total_cpu_cores"] = row["server_cpu_cores"] + row["client_cpu_cores"]
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rfc", type=Path, required=True)
    parser.add_argument("--device", default="253:2")
    parser.add_argument("--timings", type=Path)
    parser.add_argument("--raw-evidence", action="store_true", help="Package the finished suite's raw logs and metadata")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    verification_source = args.suite / "verification-final.json"
    if verification_source.exists():
        shutil.copy2(verification_source, args.out / verification_source.name)
    samples = reduce_samples(args.suite / "resources" / "samples.jsonl", args.device)
    rows = rows_from_results(args.suite, samples)
    summary = json.loads((args.suite / "summary.json").read_text()) if (args.suite / "summary.json").exists() else {"running": True}
    history = []
    if (args.suite / "events.jsonl").exists():
        for line in (args.suite / "events.jsonl").read_text().splitlines():
            event = json.loads(line)
            if event.get("event") in {"cell_failed", "suite_fatal", "suite_interrupted", "stop_failed"}:
                history.append(event)
    plan = json.loads((args.suite / "plan.json").read_text()) if (args.suite / "plan.json").exists() else []
    status = "异常中断" if summary.get("fatal") else "矩阵结束（含失败）" if summary.get("ended") and summary.get("failed") else "矩阵完成" if summary.get("ended") and len(summary.get("completed", [])) == len(plan) and plan else "测量进行中"
    if status == "矩阵完成":
        expected = sum(4 if p["size"] == min(x["size"] for x in plan) else 2 for p in plan)
        if len(rows) != expected or not all(row["valid"] for row in rows):
            raise RuntimeError("Completed suite is missing valid interface evidence")
    builds = []
    for path in (args.suite / "builds").glob("*.json"):
        builds.append(json.loads(path.read_text()))
    valid = [r for r in rows if r["valid"]]
    timing_source = args.timings or args.suite.parent / "timings" / "timings.json"
    timings = json.loads(timing_source.read_text()) if timing_source.exists() else {"steps": [], "issues": ["Timing evidence not supplied"]}
    payload = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "rows": rows,
               "samples": samples, "summary": summary, "plan": plan, "builds": builds, "timings":timings,"io_device": args.device,"failure_history":history}
    (args.out / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=2))
    timing_csv = timing_source.with_suffix(".csv")
    if timing_csv.exists():
        shutil.copy2(timing_csv, args.out / "timings.csv")
    (args.out / "data.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    (args.out / "data.js").write_text("window.KVC_DATA=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + ";\n")
    fields = sorted(set().union(*(row.keys() for row in rows))) if rows else ["product", "operation", "valid"]
    with (args.out / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader(); writer.writerows(rows)
    docs = args.out / "docs"
    docs.mkdir(exist_ok=True)
    for path in args.rfc.glob("*.md"):
        shutil.copy2(path, docs / path.name)
    for path in (args.suite / "cells").glob("*/*.json"):
        if path.stem in ["put", "get", "exist_hit", "exist_miss", "failure"]:
            target = args.out / "results" / path.parent.name / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    template = Path(__file__).with_name("report-template.html").read_text()
    rendered = template.replace("{{VALID}}", str(len(valid))).replace("{{RECORDED}}", str(len(rows))).replace("{{FAILED}}", str(len(rows) - len(valid)))
    rendered = rendered.replace("{{STATUS}}", status)
    rendered = rendered.replace("{{VERIFICATION_LINK}}", '<a href="verification-final.json">最终完整性校验记录 ↗</a>' if verification_source.exists() else '<span>完整性校验待矩阵结束后执行</span>')
    rendered = rendered.replace("{{CELLS}}", f"{len(summary.get('completed', []))}/{len(plan)} 用例完成 · {len(summary.get('failed', []))} 用例待补跑 · 历史失败 {len(history)} 次")
    rendered = rendered.replace("{{RAW_EVIDENCE}}", '<a href="raw-evidence.tar.gz" download>原始监控、运行日志与会话证据</a>' if args.raw_evidence else '')
    build_rows = ""
    for b in builds:
        build_rows += "<tr>" + "".join("<td>" + html.escape(str(b.get(k, "待采集"))) + "</td>" for k in ["product", "tag", "sha", "python", "compiler", "build_exit", "smoke_exit"]) + "</tr>"
    rendered = rendered.replace("{{BUILD_ROWS}}", build_rows)
    (args.out / "index.html").write_text(rendered)
    for name in ["report.js", "report.css"]:
        shutil.copy2(Path(__file__).with_name(name), args.out / name)
    vendor = Path(__file__).with_name("echarts.min.js")
    if not vendor.is_file():
        raise RuntimeError("Missing packaged ECharts dependency; use the complete scripts directory")
    shutil.copy2(vendor, args.out / vendor.name)
    scripts = Path(__file__).resolve().parent
    archive = args.out / "reproduce.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(scripts.rglob("*")):
            relative = path.relative_to(scripts)
            if len(relative.parts) > 1 and relative.parts[0] not in {"datasystem", "mooncake", "lmcache"}:
                continue
            if path.is_file() and not any(part.startswith(".") or part in {"source","evidence","__pycache__","venv","venv311"} for part in relative.parts) and path.suffix in {".py",".sh",".mjs",".js",".css",".html",".lock",".md",".json"}:
                zipped.write(path, "kvc-fair/" + str(relative))
        for path in sorted(args.rfc.glob("*.md")):
            zipped.write(path, "kvc-fair/docs/" + path.name)
    if args.raw_evidence:
        if not summary.get("ended"):
            raise RuntimeError("Raw evidence packaging requires a stopped suite")
        with tarfile.open(args.out / "raw-evidence.tar.gz", "w:gz", compresslevel=6) as archive:
            for path in sorted(args.suite.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError("Unexpected symlink in raw evidence: " + str(path))
                if path.is_file():
                    archive.add(path, arcname=str(Path(args.suite.name) / path.relative_to(args.suite)), recursive=False)
    subprocess.run([sys.executable, str(Path(__file__).with_name("analyze_results.py")),
                    "--data", str(args.out / "data.json"), "--out", str(args.out)], check=True)
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.suffix in {".html", ".js", ".css", ".json", ".csv", ".md"}:
            Path(str(path) + ".gz").write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    hashes = {}
    for path in sorted(args.out.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            hashes[str(path.relative_to(args.out))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (args.out / "SHA256SUMS").write_text("".join(digest + "  " + name + "\n" for name,digest in hashes.items()))
    print(json.dumps({"valid":len(valid),"recorded":len(rows),"samples":len(samples),"out":str(args.out)}))


if __name__ == "__main__":
    main()
