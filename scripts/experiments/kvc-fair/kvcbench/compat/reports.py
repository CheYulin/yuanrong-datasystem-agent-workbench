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
from kvcbench.platform.artifacts import finalize_artifacts, operation_ledger, version_zip_href
from kvcbench.report.package import reproduction_archive
from kvcbench.platform.files import copy_file
from kvcbench.core.result_state import report_completion
from kvcbench.report.model import compact_payload, configuration_profile
from kvcbench.collect.history import deadline_diagnostic_cases, history_evidence, failed_attempt_evidence
from kvcbench.platform.archives import verify_tar_inputs
from kvcbench.collect.audits.postprocessing import verify_deployment_evidence, verify_resource_evidence
from kvcbench.collect.audits.gate_inputs import verify_gate_evidence, verify_tcp_evidence
from kvcbench.collect.audits.runtime import verify_datasystem_isolation, verify_runtime_inputs


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            checksum.update(block)
    return checksum.hexdigest()


def verify_source_build_provenance(stage, runtime_records, builds):
    from build_metadata import PRODUCTS
    from kvcbench.collect.audits.runtime import verify_source_build_provenance as verify
    return verify(stage, runtime_records, builds, products=PRODUCTS)


def render(args):
    cutoff = time.time_ns()
    suite, rfc = args.suite.resolve(strict=True), args.rfc.resolve(strict=True)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh empty report directory; existing deliveries are not overwritten")
    scripts = Path(__file__).resolve().parents[2]
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
