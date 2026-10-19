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
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
import zipfile


def nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def known_sum(values):
    values = list(values)
    return sum(values) if values and all(nonnegative(value) for value in values) else None


def counter_delta(values):
    values = list(values)
    if len(values) < 2 or not all(nonnegative(value) for value in values):
        return None
    return values[-1] - values[0] if all(b >= a for a, b in zip(values, values[1:])) else None


def counter_rate(values, seconds, scale=1):
    delta = counter_delta(values)
    return delta / seconds / scale if delta is not None and nonnegative(seconds) and seconds > 0 else None


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
                             "read_bytes": (io.get("rbytes") if io else 0) if io is not None else None,
                             "write_bytes": (io.get("wbytes") if io else 0) if io is not None else None,
                             "events": group.get("memory.events")}
            if samples:
                row["prev_mono_ns"] = samples[-1]["mono_ns"]
                dt = (row["mono_ns"] - samples[-1]["mono_ns"]) / 1e9
                for role in ["server", "client", "monitor"]:
                    for metric in ["cpu_usec", "read_bytes", "write_bytes"]:
                        current, previous = row[role].get(metric), samples[-1][role].get(metric)
                        row[role][metric + "_per_s"] = counter_rate([previous, current], dt)
                if "host_busy_ticks" in row and "host_busy_ticks" in samples[-1]:
                    row["host_cpu_cores"] = counter_rate([samples[-1]["host_busy_ticks"], row["host_busy_ticks"]], dt, ticks)
                    used = known_sum(row[role].get("cpu_usec_per_s") for role in ["server", "client", "monitor"])
                    row["other_cpu_cores_estimate"] = max(0, row["host_cpu_cores"] - used / 1e6) if row["host_cpu_cores"] is not None and used is not None else None
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
            memory = [total / (1 << 30) for s in inside if (total := known_sum(s[role].get("memory") for role in ["server", "client"])) is not None]
            row["peak_memory_gib"] = max(memory) if memory else None
            app_memory = [total / (1 << 30) for s in inside
                          if (total := known_sum(s[role].get(key) for role in ["server", "client"] for key in ["anon", "shmem"])) is not None]
            row["peak_anon_shmem_gib"] = max(app_memory) if app_memory else None
            if len(inside) >= 2:
                first, last = inside[0], inside[-1]
                seconds = (last["mono_ns"] - first["mono_ns"]) / 1e9
                row["resource_span_s"] = seconds if seconds > 0 else None
                row["resource_coverage"] = seconds / row["duration_s"] if seconds > 0 and nonnegative(row["duration_s"]) and row["duration_s"] > 0 else None
                for role in ["server", "client"]:
                    row[role + "_cpu_cores"] = counter_rate((s[role].get("cpu_usec") for s in inside), seconds, 1e6)
                    throttle = counter_delta(s[role].get("throttled_usec") for s in inside)
                    row[role + "_throttle_ms"] = throttle / 1000 if throttle is not None else None
                    row[role + "_write_mib_s"] = counter_rate((s[role].get("write_bytes") for s in inside), seconds, 1 << 20)
                row["total_cpu_cores"] = known_sum(row[role + "_cpu_cores"] for role in ["server", "client"])
        rows.append(row)
    return rows


def failure_history_text(history, tcp_verification=None):
    if tcp_verification is None:
        return f"历史失败 {len(history)} 次"
    if tcp_verification.get("passed") is not True:
        raise ValueError("TCP failure history requires passed protocol verification")
    revisions = tcp_verification["execution"]["preflight_changes"]["revisions"]
    if not isinstance(revisions, list):
        raise ValueError("Verified TCP preflight revisions must be a list")
    return f"性能用例历史失败 {len(history)} 次 · 协议预检历史失败 {len(revisions)} 轮"


def compact_tcp_verification(verification, full_evidence):
    if (full_evidence.get("path") != "tcp-verification.json"
            or not isinstance(full_evidence.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", full_evidence.get("sha256", "")) is None
            or type(full_evidence.get("bytes")) is not int or full_evidence["bytes"] <= 0):
        raise ValueError("TCP verification reference requires its exact relative path, SHA256 and positive bytes")
    keys = ("schema_version", "generated_utc", "suite", "protocol_evidence",
            "passed", "errors", "checks", "scope", "execution")
    return {"summary_format": "kvc-tcp-verification-summary-v1",
            **{key: verification[key] for key in keys}, "full_evidence": full_evidence}


def reproduction_archive(scripts, rfc, out):
    archive = out / "reproduce.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(scripts.rglob("*")):
            relative = path.relative_to(scripts)
            if len(relative.parts) > 1 and relative.parts[0] not in {"datasystem", "mooncake", "lmcache"}:
                continue
            if path.is_file() and not any(part.startswith(".") or part in {"source","evidence","__pycache__","venv","venv311"} for part in relative.parts) and path.suffix in {".py",".sh",".mjs",".js",".css",".html",".lock",".md",".json"}:
                zipped.write(path, "kvc-fair/" + str(relative))
        for path in sorted(rfc.glob("*.md")):
            zipped.write(path, "kvc-fair/docs/" + path.name)
    return archive


def finalize_artifacts(out, gzip_paths=None, chmod_paths=None):
    if gzip_paths is None:
        gzip_paths = [path for path in sorted(out.iterdir()) if path.is_file() and path.suffix in {".html", ".js", ".css", ".json", ".jsonl", ".csv", ".md"}]
    for path in gzip_paths:
        target = Path(str(path) + ".gz")
        temporary = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(gzip.compress(path.read_bytes(), mtime=0))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    chmod_paths = None if chmod_paths is None else set(chmod_paths)
    hashes = {}
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            if chmod_paths is None or path in chmod_paths:
                path.chmod(0o644)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(8 << 20), b""):
                    digest.update(block)
            hashes[str(path.relative_to(out))] = digest.hexdigest()
    manifest = out / "SHA256SUMS"
    manifest.write_text("".join(digest + "  " + name + "\n" for name, digest in hashes.items()))
    manifest.chmod(0o644)
    return hashes


def operation_ledger(logs, cutoff_wall_ns, action):
    records, sources, seen, duplicates, excluded = [], [], set(), 0, 0
    for path in dict.fromkeys(Path(value).resolve(strict=True) for value in logs):
        content = path.read_bytes()
        sources.append({"path": str(path), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        for line_number, line in enumerate(content.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict) or "origin_log" in raw or raw.get("record_type") == "delivery_ledger_snapshot":
                raise ValueError("Use original command logs, not a delivery ledger: " + str(path))
            if (raw.get("end_wall_ns") or raw.get("start_wall_ns") or 0) > cutoff_wall_ns:
                excluded += 1
                continue
            key = (str(path), json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            records.append({**raw, "origin_log": str(path), "origin_line": line_number})
    whole, nanos = divmod(cutoff_wall_ns, 1_000_000_000)
    cutoff_utc = datetime.datetime.fromtimestamp(whole, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}Z"
    metadata = {"record_type": "delivery_ledger_snapshot", "snapshot_action": action, "cutoff_wall_ns": cutoff_wall_ns,
                "cutoff_utc": cutoff_utc, "sources": sources, "included_records": len(records),
                "duplicate_records_omitted": duplicates, "after_cutoff_records_omitted": excluded,
                "scope": "Original input records observed before this delivery action; origin_log identifies each source. This action and subsequent operations are not assigned fabricated records or durations. The first JSONL row is snapshot metadata, not a timed command."}
    return ("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in [metadata, *records])).encode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rfc", type=Path, required=True)
    parser.add_argument("--device", default="253:2")
    parser.add_argument("--timings", type=Path)
    parser.add_argument("--raw-evidence", action="store_true", help="Package the finished suite's raw logs and metadata")
    parser.add_argument("--put-profile", type=Path)
    parser.add_argument("--operation-log", type=Path, action="append", default=[])
    parser.add_argument("--transport-profile", choices=["native", "tcp"], default="native")
    args = parser.parse_args()
    operation_cutoff = time.time_ns()
    tcp_verification = None
    tcp_summary = None
    if args.transport_profile == "tcp":
        from verify_tcp import verify_tcp
        tcp_verification = verify_tcp(argparse.Namespace(suite=args.suite,
            protocol_evidence=args.suite / "protocol-evidence", ds_medium=None))
        if not tcp_verification.get("passed"):
            raise RuntimeError("TCP report requires independently verified protocol evidence: " + json.dumps(tcp_verification.get("errors"), ensure_ascii=False))
    args.out.mkdir(parents=True, exist_ok=True)
    if tcp_verification:
        verification_file = args.out / "tcp-verification.json"
        verification_file.write_text(json.dumps(tcp_verification, ensure_ascii=False, indent=2) + "\n")
        verification_bytes = verification_file.read_bytes()
        tcp_summary = compact_tcp_verification(tcp_verification, {
            "path": verification_file.name, "sha256": hashlib.sha256(verification_bytes).hexdigest(),
            "bytes": len(verification_bytes)})
    verification_source = args.suite / "verification-final.json"
    if verification_source.exists():
        shutil.copy2(verification_source, args.out / verification_source.name)
    samples = reduce_samples(args.suite / "resources" / "samples.jsonl", args.device)
    rows = rows_from_results(args.suite, samples)
    profile = None
    if args.put_profile:
        profile_samples = reduce_samples(args.put_profile / "resources/samples.jsonl", args.device)
        profile_rows = rows_from_results(args.put_profile, profile_samples)
        profile_summary = json.loads((args.put_profile / "summary.json").read_text())
        if not profile_summary.get("ended") or profile_summary.get("valid") is not True or profile_summary.get("failed") or profile_summary.get("fatal"):
            raise RuntimeError("Supplemental PUT profile has not completed successfully")
        if len(profile_rows) != 9 or not all(r["valid"] and r["operation"] == "put" and r["concurrency"] == 8 for r in profile_rows):
            raise RuntimeError("Expected nine valid independent PUT resource observations")
        if {(r["product"],r["size"],r["repeat"]) for r in profile_rows} != {(p,s,0) for p in ["datasystem","mooncake","lmcache"] for s in [1024,65536,1048576]}:
            raise RuntimeError("Supplemental PUT profile identity set mismatch")
        times = [s["mono_ns"] for s in profile_samples]
        indices = set()
        for row in profile_rows:
            row.update(purpose="supplemental_resource_only", included_in_main_performance_repeats=False, observation_repetitions=1)
            indices.update(range(bisect.bisect_left(times, row["start_mono_ns"]), bisect.bisect_right(times, row["end_mono_ns"])))
            row["evidence"] = "results/put-resource-profile/" + Path(row["evidence"]).relative_to("results").as_posix()
        kept = []
        for index in sorted(indices):
            sample = profile_samples[index]
            kept.append({k: sample[k] for k in ["mono_ns", "prev_mono_ns", "sampling_ms", "server", "client"] if k in sample})
        profile = {"rows": profile_rows, "samples": kept, "summary": profile_summary,
                   "scope": "Nine independent single PUT resource observations at concurrency8; excluded from the primary three-repeat performance statistics."}
        for path in (args.put_profile / "cells").glob("*/put.json"):
            target = args.out / "results/put-resource-profile" / path.parent.name / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        fields = sorted(set().union(*(r.keys() for r in profile_rows)))
        with (args.out / "put-profile.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fields); writer.writeheader(); writer.writerows(profile_rows)
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
    payload = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "transport_profile": args.transport_profile, "rows": rows,
               "samples": samples, "summary": summary, "plan": plan, "builds": builds, "timings":timings,"io_device": args.device,"failure_history":history,"put_profile":profile,"tcp_verification":tcp_summary}
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
    rendered = rendered.replace("{{PROFILE_DOWNLOAD}}", '<a href="put-profile.csv" download>PUT 补充资源 CSV</a>' if profile else "")
    rendered = rendered.replace("{{OPERATIONS_LINK}}", '<a href="operations.jsonl" download>编排操作耗时 JSONL</a>' if args.operation_log else "")
    rendered = rendered.replace("{{VERIFICATION_LINK}}", '<a href="verification-final.json">最终完整性校验记录 ↗</a>' if verification_source.exists() else '<span>完整性校验待矩阵结束后执行</span>')
    rendered = rendered.replace("{{CELLS}}", f"{len(summary.get('completed', []))}/{len(plan)} 用例完成 · {len(summary.get('failed', []))} 用例待补跑 · {failure_history_text(history, tcp_verification)}")
    rendered = rendered.replace("{{RAW_EVIDENCE}}", '<a href="raw-evidence.tar.gz" download>原始监控、运行日志与会话证据</a>' if args.raw_evidence else '')
    build_rows = ""
    for b in builds:
        build_rows += "<tr>" + "".join("<td>" + html.escape(str(b.get(k, "待采集"))) + "</td>" for k in ["product", "tag", "sha", "python", "compiler", "build_exit", "smoke_exit"]) + "</tr>"
    rendered = rendered.replace("{{BUILD_ROWS}}", build_rows)
    if args.transport_profile == "tcp":
        rendered = rendered.replace("· 同节点 CPU 实验", "· 同节点统一 TCP 实验")
        rendered = rendered.replace("观察 CPU 原生路径的吞吐、延迟与资源。", "对象数据与控制均经 TCP，观察吞吐、延迟与资源。")
        original_scope = "本报告比较发布源码的本机 CPU 部署：DataSystem 使用本机 RPC 控制与 SHM 候选路由，Mooncake 使用 TCP，LMCache 使用 MP SHM + RPC。三条路径的端到端结果包含各自协议和适配成本；不由此推断跨机 RDMA、GPU KV 或推理 TTFT。"
        tcp_scope = '本报告比较统一 TCP 数据通路：DataSystem 关闭进程间共享内存通信，Mooncake 使用 classic TE TCP 并关闭 memcpy 捷径，LMCache 使用 MP pickle payload 经 ZMQ/TCP。控制和对象数据均走回环 TCP，不使用 RDMA/UB；各自事务与序列化成本仍不同。<a href="tcp-verification.json">TCP 协议与输入核验</a>和原始跟踪可供复核；<a href="../kvc-fair-20261020/index.html">原生 SHM/TCP 基线</a>独立保留。'
        if original_scope not in rendered:
            raise RuntimeError("TCP scope substitution requires a reviewed report template")
        rendered = rendered.replace(original_scope, tcp_scope)
        rendered = rendered.replace("prepare / commit 确认。双实例", "pickle 经 TCP，prepare / commit 确认；启动时全量触页。双实例")
        rendered = rendered.replace("三个产品自身均由锁定源码构建。", "三个产品自身均由锁定源码构建；本 TCP 矩阵复用这些制品并重新核对哈希，构建表中的原 smoke 与本次 TCP 协议门禁分别留证。")
        rendered = rendered.replace("<a href=\"docs/test-design.md\">用例与 Mermaid 时序 ↗</a>", '<a href="docs/tcp-plan.md">TCP 配置、协议门禁与 Mermaid 时序 ↗</a><a href="docs/test-design.md">共同用例设计 ↗</a>')
        rendered = rendered.replace('href="docs/validation.md"', 'href="docs/tcp-validation.md"')
    (args.out / "index.html").write_text(rendered)
    for name in ["report.js", "report.css"]:
        shutil.copy2(Path(__file__).with_name(name), args.out / name)
    vendor = Path(__file__).with_name("echarts.min.js")
    if not vendor.is_file():
        raise RuntimeError("Missing packaged ECharts dependency; use the complete scripts directory")
    shutil.copy2(vendor, args.out / vendor.name)
    scripts = Path(__file__).resolve().parent
    reproduction_archive(scripts, args.rfc, args.out)
    if args.operation_log:
        (args.out / "operations.jsonl").write_bytes(operation_ledger(args.operation_log, operation_cutoff, "report"))
    if args.raw_evidence:
        if not summary.get("ended"):
            raise RuntimeError("Raw evidence packaging requires a stopped suite")
        with tarfile.open(args.out / "raw-evidence.tar.gz", "w:gz", compresslevel=6) as archive:
            for path in sorted(args.suite.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError("Unexpected symlink in raw evidence: " + str(path))
                if path.is_file():
                    archive.add(path, arcname=str(Path(args.suite.name) / path.relative_to(args.suite)), recursive=False)
            if args.put_profile:
                for path in sorted(args.put_profile.rglob("*")):
                    if path.is_symlink():
                        raise RuntimeError("Unexpected symlink in supplemental evidence: " + str(path))
                    if path.is_file():
                        archive.add(path, arcname=str(Path("put-resource-profile") / path.relative_to(args.put_profile)), recursive=False)
    subprocess.run([sys.executable, str(Path(__file__).with_name("analyze_results.py")),
                    "--data", str(args.out / "data.json"), "--out", str(args.out)], check=True)
    finalize_artifacts(args.out)
    print(json.dumps({"valid":len(valid),"recorded":len(rows),"samples":len(samples),"out":str(args.out)}))


if __name__ == "__main__":
    main()
