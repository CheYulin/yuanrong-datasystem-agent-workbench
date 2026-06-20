#!/usr/bin/env python3
"""Generate GitCode bugfix/issue ↔ FMEA coverage HTML report."""

from __future__ import annotations

import csv
import html
import json
import re
import subprocess
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
FEMA_CSV = REPO_ROOT / "yuanrong-datasystem-agent-workbench/workspace/archive/01-fema/fema-final.csv"
FEMA_MD = REPO_ROOT / "yuanrong-datasystem-agent-workbench/workspace/archive/01-fema/fema-analysis-filled.md"
GIT_REPO = REPO_ROOT / "yuanrong-datasystem"
OUTPUT = REPO_ROOT / "htmls/reliability/ds-gitcode-bugfix-fema-coverage.html"
API = "https://api.gitcode.com/api/v5/repos/openeuler/yuanrong-datasystem"

CATEGORIES = [
    ("URMA/UB", ["urma", "ub", "jetty", "jfs", "jfr", "cqe", "jfc", "transporttype"]),
    ("ZMQ/RPC", ["zmq", "rpc", "deadline", "timeout", "半开", "liveness"]),
    ("读路径", ["get", "mget", "pull", "exist", "查询", "读取", "remote get"]),
    ("写路径", ["set", "mset", "publish", "create", "写入", "put"]),
    ("扩缩容", ["缩容", "扩容", "scale", "reconcile", "对账", "migrate", "迁移"]),
    ("etcd/集群", ["etcd", "meta", "coordinator", "lease", "续租", "选主"]),
    ("内存/OOM", ["oom", "内存", "shm", "mmap", "out of memory", "大页", "hugepage"]),
    ("L2/OBS", ["二级缓存", "obs", "l2", "write_back", "write through"]),
    ("Worker", ["worker", "datasystem_worker", "procmon", "节点"]),
    ("Client/SDK", ["client", "kvclient", "sdk", "invoke"]),
    ("网络", ["网络", "闪断", "分区", "丢包", "建链"]),
    ("进程崩溃", ["coredump", "crash", "退出", "拉起", "挂死", "吊死", "busy"]),
]

PLANES = {
    "UB数据平面": {
        "label": "UB 数据平面（URMA/Transfer）",
        "categories": ["URMA/UB"],
        "keywords": ["urma", "ub", "jetty", "jfs", "jfr", "cqe", "jfc", "transporttype", "transfer_engine", "data-plane", "data plane"],
        "path_hints": ["transfer_engine", "urma", "jetty", "jfs", "jfr", "ub_"],
        "color": "#e74c3c",
    },
    "RPC控制平面": {
        "label": "RPC 控制平面（ZMQ/元数据通道）",
        "categories": ["ZMQ/RPC"],
        "keywords": ["zmq", "rpc", "stub", "deadline", "slow-log", "latency", "liveness", "半开", "keepalive"],
        "path_hints": ["rpc/zmq", "zmq_stub", "zmq_constants", "rpc_client", "etcd_keep_alive"],
        "color": "#4a90d9",
    },
}

PLANE_HOTSPOTS = {
    "UB数据平面": [
        {"symbol": "UrmaTransport::Connect", "desc": "URMA 建链", "node": "UrmaTransport"},
        {"symbol": "ConnectionManager::HasReadyConnection", "desc": "连接就绪", "node": "ConnMgr"},
        {"symbol": "UrmaSuccessRateTracker::Record", "desc": "成功率/切 Worker", "node": "Tracker"},
    ],
    "RPC控制平面": [
        {"symbol": "ZmqStubConnMgrImpl::AutoReconnect", "desc": "节点连接重连", "node": "ZmqMgr"},
        {"symbol": "CheckRpcLatencyAfterClientSend", "desc": "RPC 慢日志", "node": "SlowLog"},
        {"symbol": "ZmqStubConn::Init", "desc": "ZMQ Session 初始化", "node": "ZmqStub"},
    ],
}


def classify_plane(text: str, category: str = "", files: list[str] | None = None) -> str:
    t = (text or "").lower()
    fs = " ".join(files or []).lower()
    if category == "URMA/UB":
        return "UB数据平面"
    if category == "ZMQ/RPC":
        return "RPC控制平面"
    ub = any(k in t or k in fs for k in PLANES["UB数据平面"]["keywords"] + PLANES["UB数据平面"]["path_hints"])
    rpc = any(k in t or k in fs for k in PLANES["RPC控制平面"]["keywords"] + PLANES["RPC控制平面"]["path_hints"])
    if ub and rpc:
        return "双平面交叉"
    if ub:
        return "UB数据平面"
    if rpc:
        return "RPC控制平面"
    return "业务/其他平面"


def is_reliability_fix(text: str) -> bool:
    if re.search(r"fix|bugfix|修复|bug", text, re.I):
        return True
    return bool(re.search(r"failover|switch worker|urma.*fail|reconnect|procmon", text, re.I))


def is_noise_pr(subject: str) -> bool:
    return bool(re.search(r"revert|文档|docs? about", subject, re.I) and not re.search(r"bugfix|bug", subject, re.I))


DIM_FLOWS = [
    ("UB数据平面", ["urma", "ub", "jetty", "jfs", "jfr", "transfer_engine", "transporttype", "data-plane"]),
    ("RPC控制平面", ["zmq", "rpc", "stub", "slow-log", "deadline", "liveness", "半开"]),
    ("读路径", ["get", "mget", "pull", "exist", "查询", "读取"]),
    ("写路径", ["set", "mset", "publish", "put", "写入"]),
    ("扩缩容/迁移", ["缩容", "扩容", "scale", "migrate", "迁移", "reconcile", "对账"]),
    ("元数据协调", ["etcd", "meta", "location", "replica", "coordinator", "lease"]),
    ("运维部署", ["procmon", "deploy", "script", "benchmark", "service discovery"]),
]

DIM_COMPONENTS = [
    ("Client", ["client", "kvclient", "sdk", "pybind", "object_client"]),
    ("Worker", ["worker", "datasystem_worker", "procmon", "ds-worker"]),
    ("Master/OC", ["master", "oc_service", "metadata", "replica"]),
    ("transfer_engine", ["transfer_engine", "urma", "connection_manager"]),
    ("rpc/zmq", ["zmq", "rpc/", "zmq_stub", "rpc_client"]),
    ("etcd/集群", ["etcd", "coordinator", "keep_alive", "metastore"]),
    ("OBS/L2", ["obs", "l2", "二级缓存", "write_back"]),
]

DIM_FAULTS = [
    ("连接/建链", ["connect", "建链", "reconnect", "半开", "jetty", "jfs"]),
    ("超时/慢", ["timeout", "slow", "deadline", "latency", "超时", "slow-log"]),
    ("一致性/元数据", ["location", "migrate", "replica", "元数据", "get fail", "invalid"]),
    ("进程/崩溃", ["crash", "coredump", "挂死", "退出", "procmon", "busy"]),
    ("资源/OOM", ["oom", "memory", "shm", "mmap", "大页"]),
    ("协议/编码", ["encoding", "url", "protocol", "transporttype", "multipart"]),
]


def classify_dim(text: str, rules: list[tuple[str, list[str]]], default: str = "其他") -> str:
    t = (text or "").lower()
    best = (0, default)
    for name, kws in rules:
        score = sum(1 for k in kws if k in t)
        if score > best[0]:
            best = (score, name)
    return best[1] if best[0] > 0 else default


def tag_dims(text: str, category: str = "", files: list[str] | None = None) -> dict[str, str]:
    blob = f"{text} {' '.join(files or [])}"
    flow = classify_dim(blob, DIM_FLOWS)
    if flow == "其他":
        plane = classify_plane(blob, category, files)
        if plane in ("UB数据平面", "RPC控制平面"):
            flow = plane
    return {
        "flow": flow,
        "component": classify_dim(blob, DIM_COMPONENTS),
        "fault": classify_dim(blob, DIM_FAULTS),
        "plane": classify_plane(blob, category, files),
    }


def compute_browser_data(
    bug_issues: list[IssueItem],
    all_fix_prs: list[dict[str, Any]],
    pr_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    issues_out = []
    for bi in bug_issues:
        dims = tag_dims(f"{bi.title} {bi.body}", bi.category)
        issues_out.append(
            {
                "id": bi.number,
                "title": bi.title,
                "state": bi.state,
                "status": bi.fix_status,
                "url": bi.html_url,
                "prs": [p for p in bi.linked_prs if p in pr_map],
                "fema": bi.linked_fema[:2],
                "reporter_login": bi.reporter_login,
                "reporter_name": bi.reporter_name,
                "reporter_label": bi.reporter_label,
                **dims,
            }
        )
    prs_out = []
    for fx in all_fix_prs:
        dims = tag_dims(f"{fx['title']} {fx.get('summary','')}", fx["category"], fx.get("files"))
        prs_out.append(
            {
                "id": fx["pr"],
                "title": fx["title"],
                "date": fx["date"],
                "summary": fx["summary"],
                "issues": fx["issues"],
                "fix_kind": fx.get("fix_kind", "unknown"),
                "fix_kind_label": fx.get("fix_kind_label", FIX_KIND_LABELS["unknown"]),
                "source_count": fx.get("source_count", 0),
                "test_count": fx.get("test_count", 0),
                "author_login": fx.get("author_login", ""),
                "author_name": fx.get("author_name", ""),
                "author_label": fx.get("author_label", ""),
                "files": [f.split("/")[-1] for f in fx.get("files", [])[:6]],
                **dims,
            }
        )

    def aggregate(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"issues": 0, "open": 0, "prs": 0})
        for it in items:
            k = it.get(key, "其他")
            if "state" in it:
                counts[k]["issues"] += 1
                if it["state"] == "open":
                    counts[k]["open"] += 1
            else:
                counts[k]["prs"] += 1
        return [
            {"name": n, **v}
            for n, v in sorted(counts.items(), key=lambda x: -(x[1]["issues"] + x[1]["prs"]))
        ]

    return {
        "issues": issues_out,
        "prs": prs_out,
        "by_flow": aggregate(issues_out, "flow"),
        "by_component": aggregate(issues_out, "component"),
        "by_fault": aggregate(issues_out, "fault"),
        "prs_by_flow": aggregate(prs_out, "flow"),
        "prs_by_component": aggregate(prs_out, "component"),
        "prs_by_fault": aggregate(prs_out, "fault"),
    }


def compute_dim_summary(bug_issues: list[IssueItem], all_fix_prs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    summary: dict[str, list[dict[str, Any]]] = {"flow": [], "component": [], "fault": []}
    browser = compute_browser_data(bug_issues, all_fix_prs, {})
    for dim, items in [("flow", browser["by_flow"]), ("component", browser["by_component"]), ("fault", browser["by_fault"])]:
        pr_key = f"prs_by_{dim}"
        pr_map_cnt = {x["name"]: x["prs"] for x in browser[pr_key]}
        for row in items:
            summary[dim].append({**row, "prs": pr_map_cnt.get(row["name"], 0)})
    return summary


FEMA_CAT_MAP = {
    "URMA层": "URMA/UB",
    "URMA": "URMA/UB",
    "UB": "URMA/UB",
    "业务实例": "业务/API",
    "组件层": "组件/进程",
    "OS层": "OS/资源",
    "特殊": "特殊/一致性",
    "datasystem": "网络/通用",
    "client": "Client/SDK",
    "ds-worker": "Worker",
    "etcd": "etcd/集群",
}


@dataclass
class FemaCase:
    id: str
    source: str
    l1: str
    l2: str
    l3: str
    mode: str
    severity: str
    domain_note: str
    improvement: str
    category: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class IssueItem:
    number: int
    title: str
    state: str
    labels: list[str]
    body: str
    html_url: str
    category: str
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    linked_prs: list[int] = field(default_factory=list)
    linked_fema: list[str] = field(default_factory=list)
    fix_status: str = "open"
    reporter_login: str = ""
    reporter_name: str = ""
    reporter_label: str = ""


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def classify_text(text: str) -> str:
    t = (text or "").lower()
    for cat, kws in CATEGORIES:
        if any(k in t for k in kws):
            return cat
    return "其他"


def fetch_paginated(path: str, extra: dict[str, str] | None = None) -> list[dict[str, Any]]:
    page = 1
    items: list[dict[str, Any]] = []
    while True:
        params = {"page": page, "per_page": 100}
        if extra:
            params.update(extra)
        q = urllib.parse.urlencode(params)
        sep = "&" if "?" in path else "?"
        url = f"{API}{path}{sep}{q}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            batch = json.loads(resp.read())
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def extract_pull_author(pull: dict[str, Any]) -> dict[str, str]:
    user = pull.get("user") or {}
    login = (user.get("login") or "").strip()
    name = (user.get("name") or "").strip()
    if name and login and name.lower() != login.lower():
        label = f"{name} ({login})"
    else:
        label = login or name or "未知"
    key = login or name or "unknown"
    return {"author_login": key, "author_name": name or login, "author_label": label}


def extract_issue_reporter(issue: dict[str, Any]) -> dict[str, str]:
    user = issue.get("user") or {}
    login = (user.get("login") or "").strip()
    name = (user.get("name") or "").strip()
    if name and login and name.lower() != login.lower():
        label = f"{name} ({login})"
    else:
        label = login or name or "未知"
    key = login or name or "unknown"
    return {"reporter_login": key, "reporter_name": name or login, "reporter_label": label}


def build_pull_authors(pulls: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
    return {int(p["number"]): extract_pull_author(p) for p in pulls}


def author_cell(fx: dict[str, Any]) -> str:
    login = fx.get("author_login", "")
    label = fx.get("author_label", login or "未知")
    if login and login != "unknown":
        return f'<a href="https://gitcode.com/{esc(login)}">{esc(label)}</a>'
    return esc(label)


def reporter_cell(item: dict[str, Any] | IssueItem) -> str:
    if isinstance(item, IssueItem):
        login, label = item.reporter_login, item.reporter_label
    else:
        login = item.get("reporter_login", "")
        label = item.get("reporter_label", login or "未知")
    if login and login != "unknown":
        return f'<a href="https://gitcode.com/{esc(login)}">{esc(label)}</a>'
    return esc(label or "—")


def build_person_options(
    counts: Counter,
    labels: dict[str, str],
    skip_unknown: bool = True,
) -> str:
    opts = ""
    for login, cnt in counts.most_common():
        if skip_unknown and login in ("", "unknown"):
            continue
        opts += f'<option value="{esc(login)}">{esc(labels.get(login, login))} ({cnt})</option>'
    return opts


def load_fema_csv() -> list[FemaCase]:
    cases: list[FemaCase] = []
    with FEMA_CSV.open(encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), 1):
            mode = row.get("故障模式", "") or ""
            note = row.get("故障模式备注", "") or ""
            l1 = row.get("一级对象", "") or ""
            l2 = row.get("二级对象", "") or ""
            cat = classify_text(f"{l1} {l2} {mode} {note}")
            if l1 == "URMA层":
                cat = "URMA/UB"
            elif l1 == "组件层":
                cat = "组件/进程"
            elif l1 == "OS层":
                cat = "OS/资源"
            elif l1 == "业务实例":
                cat = "业务/API"
            cases.append(
                FemaCase(
                    id=f"F-FINAL-{i:03d}",
                    source="final",
                    l1=l1,
                    l2=l2,
                    l3=row.get("三级对象", "") or "",
                    mode=mode,
                    severity=row.get("严酷度", "") or "",
                    domain_note=note,
                    improvement=row.get("改进建议", "") or "",
                    category=cat,
                    keywords=re.findall(r"[\u4e00-\u9fffA-Za-z_]{2,}", mode),
                )
            )
    return cases


def load_fema_md(existing_modes: set[str]) -> list[FemaCase]:
    text = FEMA_MD.read_text(encoding="utf-8")
    cases: list[FemaCase] = []
    idx = 0
    for block in re.split(r"\n---\n", text):
        if "| 一级对象 |" not in block or "| 故障模式 |" not in block:
            continue
        lines = [ln.strip() for ln in block.splitlines() if ln.strip().startswith("|")]
        if len(lines) < 3:
            continue
        for ln in lines[2:]:
            if ln.startswith("| 故障预防") or ln.startswith("| ---"):
                continue
            cols = [c.strip() for c in ln.strip("|").split("|")]
            if len(cols) < 9:
                continue
            mode = cols[3]
            if not mode or normalize(mode) in existing_modes:
                continue
            idx += 1
            l1, l2, l3 = cols[0], cols[1], cols[2]
            note = cols[8] if len(cols) > 8 else ""
            cat = classify_text(f"{l1} {l2} {mode} {note}")
            cases.append(
                FemaCase(
                    id=f"F-FILL-{idx:03d}",
                    source="filled",
                    l1=l1,
                    l2=l2,
                    l3=l3,
                    mode=mode,
                    severity=cols[7] if len(cols) > 7 else "",
                    domain_note=note,
                    improvement="",
                    category=cat,
                    keywords=re.findall(r"[\u4e00-\u9fffA-Za-z_]{2,}", mode),
                )
            )
            existing_modes.add(normalize(mode))
    return cases


def git_pr_commits() -> dict[int, dict[str, Any]]:
    out = subprocess.check_output(
        ["git", "log", "main/master", "--format=%H|%ai|%s"],
        cwd=GIT_REPO,
        text=True,
    )
    pr_map: dict[int, dict[str, Any]] = {}
    for line in out.splitlines():
        if "|" not in line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        sha, date, subject = parts[0], parts[1], parts[2]
        m = re.search(r"!(\d+)", subject)
        if not m:
            continue
        pr = int(m.group(1))
        entry = pr_map.setdefault(pr, {"commits": [], "subjects": [], "dates": []})
        entry["commits"].append(sha[:10])
        entry["subjects"].append(subject)
        entry["dates"].append(date[:10])
    return pr_map


def month_key(iso: str) -> str:
    if not iso or len(iso) < 7:
        return ""
    return iso[:7]


def compute_trends(
    bug_issues: list[IssueItem],
    pr_map: dict[int, dict[str, Any]],
    pull_titles: dict[int, str],
) -> dict[str, Any]:
    bug_opened: Counter[str] = Counter()
    bug_closed: Counter[str] = Counter()
    cat_opened: dict[str, Counter[str]] = defaultdict(Counter)
    fix_prs: Counter[str] = Counter()
    fix_cat: dict[str, Counter[str]] = defaultdict(Counter)

    dim_keys = ("flow", "component", "fault")
    dim_opened: dict[str, dict[str, Counter[str]]] = {k: defaultdict(Counter) for k in dim_keys}
    dim_closed: dict[str, dict[str, Counter[str]]] = {k: defaultdict(Counter) for k in dim_keys}
    dim_fix: dict[str, dict[str, Counter[str]]] = {k: defaultdict(Counter) for k in dim_keys}

    for bi in bug_issues:
        cm = month_key(bi.created_at)
        dims = tag_dims(f"{bi.title} {bi.body}", bi.category)
        if cm:
            bug_opened[cm] += 1
            cat_opened[bi.category][cm] += 1
            for dk in dim_keys:
                dim_opened[dk][dims[dk]][cm] += 1
        if bi.state == "closed":
            cm_close = month_key(bi.closed_at or bi.updated_at)
            if cm_close:
                bug_closed[cm_close] += 1
                for dk in dim_keys:
                    dim_closed[dk][dims[dk]][cm_close] += 1

    seen_fix_prs: set[int] = set()
    for pr_num, info in pr_map.items():
        text = " ".join(info.get("subjects", [])) + " " + pull_titles.get(pr_num, "")
        if not re.search(r"fix|bugfix|修复|bug", text, re.I):
            continue
        if pr_num in seen_fix_prs:
            continue
        seen_fix_prs.add(pr_num)
        dates = info.get("dates") or []
        if not dates:
            continue
        cm = month_key(dates[0])
        if cm:
            fix_prs[cm] += 1
            fix_cat[classify_text(text)][cm] += 1
            pdims = tag_dims(text, classify_text(text))
            for dk in dim_keys:
                dim_fix[dk][pdims[dk]][cm] += 1

    months = sorted(set(bug_opened) | set(bug_closed) | set(fix_prs))
    if len(months) > 18:
        months = months[-18:]

    open_backlog: list[int] = []
    running = 0
    for m in months:
        running += bug_opened.get(m, 0) - bug_closed.get(m, 0)
        open_backlog.append(max(running, 0))

    top_cats = [c for c, _ in Counter(bi.category for bi in bug_issues if bi.state == "open").most_common(8)]
    cat_series = {cat: [cat_opened[cat].get(m, 0) for m in months] for cat in top_cats}

    cat_open_now = Counter(bi.category for bi in bug_issues if bi.state == "open")
    cat_total = Counter(bi.category for bi in bug_issues)

    fix_rate = []
    for m in months:
        closed = bug_closed.get(m, 0)
        fixes = fix_prs.get(m, 0)
        fix_rate.append(round(closed / fixes, 2) if fixes else 0)

    plane_bug_opened: dict[str, Counter[str]] = {
        "UB数据平面": Counter(),
        "RPC控制平面": Counter(),
    }
    plane_fix: dict[str, Counter[str]] = {
        "UB数据平面": Counter(),
        "RPC控制平面": Counter(),
    }
    for bi in bug_issues:
        plane = classify_plane(f"{bi.title} {bi.body}", bi.category)
        cm = month_key(bi.created_at)
        if cm and plane in plane_bug_opened:
            plane_bug_opened[plane][cm] += 1
    for pr_num, info in pr_map.items():
        text = " ".join(info.get("subjects", [])) + " " + pull_titles.get(pr_num, "")
        if not is_reliability_fix(text):
            continue
        plane = classify_plane(text, classify_text(text))
        dates = info.get("dates") or []
        if not dates or plane not in plane_fix:
            continue
        cm = month_key(dates[0])
        if cm:
            plane_fix[plane][cm] += 1

    def dim_top_categories(counters: dict[str, Counter[str]], n: int = 10) -> list[str]:
        totals = Counter({cat: sum(cm.values()) for cat, cm in counters.items()})
        return [c for c, _ in totals.most_common(n)]

    def dim_monthly(counters: dict[str, Counter[str]], cats: list[str]) -> dict[str, list[int]]:
        return {cat: [counters.get(cat, Counter()).get(m, 0) for m in months] for cat in cats}

    def dim_backlog(opened: dict[str, Counter[str]], closed: dict[str, Counter[str]], cats: list[str]) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for cat in cats:
            running = 0
            series: list[int] = []
            for m in months:
                running += opened.get(cat, Counter()).get(m, 0) - closed.get(cat, Counter()).get(m, 0)
                series.append(max(running, 0))
            out[cat] = series
        return out

    dims_trend: dict[str, Any] = {}
    for dk in dim_keys:
        cats = dim_top_categories(dim_opened[dk], 10)
        dims_trend[dk] = {
            "categories": cats,
            "opened": dim_monthly(dim_opened[dk], cats),
            "closed": dim_monthly(dim_closed[dk], cats),
            "fix_prs": dim_monthly(dim_fix[dk], cats),
            "backlog": dim_backlog(dim_opened[dk], dim_closed[dk], cats),
            "open_now": {
                cat: sum(1 for bi in bug_issues if bi.state == "open" and tag_dims(f"{bi.title} {bi.body}", bi.category)[dk] == cat)
                for cat in cats
            },
        }

    return {
        "months": months,
        "bug_opened": [bug_opened.get(m, 0) for m in months],
        "bug_closed": [bug_closed.get(m, 0) for m in months],
        "fix_prs": [fix_prs.get(m, 0) for m in months],
        "open_backlog": open_backlog,
        "fix_rate": fix_rate,
        "cat_series": cat_series,
        "cat_open_now": dict(cat_open_now.most_common(12)),
        "cat_total": dict(cat_total.most_common(12)),
        "plane_bug_opened": {p: [plane_bug_opened[p].get(m, 0) for m in months] for p in plane_bug_opened},
        "plane_fix_prs": {p: [plane_fix[p].get(m, 0) for m in months] for p in plane_fix},
        "dims": dims_trend,
        "recent_fix_by_cat": {
            cat: sum(cnt for cm, cnt in fix_cat[cat].items() if cm in months[-6:])
            for cat in sorted(fix_cat, key=lambda c: -sum(fix_cat[c].values()))
        },
    }


def cg_json(args: list[str]) -> dict[str, Any] | list[Any] | None:
    try:
        out = subprocess.check_output(
            ["codegraph", *args, "-j"],
            cwd=GIT_REPO,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return json.loads(out) if out.strip() else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def pr_changed_files(sha: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "show", "-m", "--first-parent", "--name-only", "--format=", sha],
            cwd=GIT_REPO,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return [
            f.strip()
            for f in out.splitlines()
            if f.strip() and not f.endswith(".md") and "/docs/" not in f and not f.startswith("docs/")
        ]
    except subprocess.CalledProcessError:
        return []


FIX_KIND_LABELS = {
    "source_only": "仅源码",
    "source_and_test": "源码+测试",
    "test_only": "仅测试",
    "config_only": "配置/构建",
    "other": "其他",
    "unknown": "未解析",
}
FIX_KIND_ORDER = ["source_only", "source_and_test", "test_only", "config_only", "other", "unknown"]


def classify_file_role(path: str) -> str:
    p = path.lower().replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    if p.startswith("tests/") or "/tests/" in p or p.startswith("test/"):
        return "test"
    if "_test." in base or base.endswith(("_test.cpp", "_test.cc", "_test.h")):
        return "test"
    if "mock" in base and base.endswith((".cpp", ".cc", ".h", ".hpp")):
        return "test"
    if p.startswith("src/") or "/src/" in p:
        return "source"
    if base.endswith((".cpp", ".cc", ".c", ".h", ".hpp", ".hh")):
        return "source"
    if any(x in p for x in ("cmake", "bazel/", "build.bazel", "cmakelists", ".yml", "scripts/", "deploy/")):
        return "config"
    return "other"


def classify_fix_scope(files: list[str]) -> dict[str, Any]:
    roles = [classify_file_role(f) for f in files]
    src_files = [f for f, r in zip(files, roles) if r == "source"]
    test_files = [f for f, r in zip(files, roles) if r == "test"]
    has_src = bool(src_files)
    has_test = bool(test_files)
    has_config = any(r == "config" for r in roles)
    if not files:
        kind = "unknown"
    elif has_src and has_test:
        kind = "source_and_test"
    elif has_src:
        kind = "source_only"
    elif has_test:
        kind = "test_only"
    elif has_config:
        kind = "config_only"
    else:
        kind = "other"
    return {
        "fix_kind": kind,
        "fix_kind_label": FIX_KIND_LABELS[kind],
        "source_count": len(src_files),
        "test_count": len(test_files),
        "source_files": src_files[:8],
        "test_files": test_files[:8],
    }


def compute_fix_scope_summary(all_fix_prs: list[dict[str, Any]], months: list[str]) -> dict[str, Any]:
    counts = Counter(fx["fix_kind"] for fx in all_fix_prs)
    month_idx = {m: i for i, m in enumerate(months)}
    monthly: dict[str, list[int]] = {k: [0] * len(months) for k in FIX_KIND_ORDER}
    for fx in all_fix_prs:
        m = (fx.get("date") or "")[:7]
        if m in month_idx:
            monthly[fx["fix_kind"]][month_idx[m]] += 1
    suspicious = [
        {
            "pr": fx["pr"],
            "title": fx["title"][:72],
            "date": fx["date"],
            "author_login": fx.get("author_login", ""),
            "author_label": fx.get("author_label", ""),
            "test_files": fx.get("test_files", [])[:4],
        }
        for fx in sorted(all_fix_prs, key=lambda x: x["date"], reverse=True)
        if fx["fix_kind"] == "test_only"
    ]
    author_counts = Counter(fx.get("author_login") or "unknown" for fx in all_fix_prs)
    author_labels = {fx.get("author_login") or "unknown": fx.get("author_label", "") for fx in all_fix_prs}
    authors = [
        {"login": login, "label": author_labels.get(login, login), "count": cnt}
        for login, cnt in author_counts.most_common()
    ]
    return {
        "counts": {k: counts.get(k, 0) for k in FIX_KIND_ORDER},
        "labels": FIX_KIND_LABELS,
        "monthly": monthly,
        "order": FIX_KIND_ORDER,
        "suspicious": suspicious,
        "authors": authors,
        "source_ratio": round(
            (counts.get("source_only", 0) + counts.get("source_and_test", 0)) / max(len(all_fix_prs), 1) * 100,
            1,
        ),
    }


def compute_all_fix_prs(
    pr_map: dict[int, dict[str, Any]],
    pull_titles: dict[int, str],
    pull_bodies: dict[int, str],
    pr_to_issues: dict[int, list[int]],
    pull_authors: dict[int, dict[str, str]],
) -> list[dict[str, Any]]:
    out = subprocess.check_output(
        ["git", "log", "main/master", "--format=%H|%ai|%s"],
        cwd=GIT_REPO,
        text=True,
    )
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for line in out.splitlines():
        if "|" not in line:
            continue
        sha, date, subject = line.split("|", 2)
        m = re.search(r"!(\d+)", subject)
        if not m:
            continue
        pr_num = int(m.group(1))
        if pr_num in seen:
            continue
        text = f"{subject} {pull_titles.get(pr_num, '')}"
        if not is_reliability_fix(text):
            continue
        if is_noise_pr(subject):
            continue
        seen.add(pr_num)
        files = pr_changed_files(sha)
        scope = classify_fix_scope(files)
        cat = classify_text(text)
        plane = classify_plane(text, cat, files)
        issues = sorted(set(pr_to_issues.get(pr_num, [])))
        author = pull_authors.get(pr_num, {})
        items.append(
            {
                "pr": pr_num,
                "date": date[:10],
                "title": pull_titles.get(pr_num) or re.sub(r"^!\d+\s*", "", subject),
                "category": cat,
                "plane": plane,
                "files": files,
                "file_count": len(files),
                "issues": issues,
                "sha": sha[:10],
                "summary": summarize_fix(text, cat),
                **author,
                **scope,
            }
        )
    return items


def compute_category_inventory(
    bug_issues: list[IssueItem],
    all_fix_prs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"bugs": [], "fix_prs": [], "open": 0, "closed": 0})
    for bi in bug_issues:
        b = buckets[bi.category]
        b["bugs"].append(bi)
        if bi.state == "open":
            b["open"] += 1
        else:
            b["closed"] += 1
    for fx in all_fix_prs:
        buckets[fx["category"]]["fix_prs"].append(fx)
    rows = []
    for cat, info in sorted(buckets.items(), key=lambda x: -len(x[1]["bugs"])):
        rows.append(
            {
                "category": cat,
                "bug_count": len(info["bugs"]),
                "open": info["open"],
                "closed": info["closed"],
                "fix_pr_count": len(info["fix_prs"]),
                "bugs": sorted(info["bugs"], key=lambda x: x.number, reverse=True),
                "fix_prs": sorted(info["fix_prs"], key=lambda x: x["date"], reverse=True),
            }
        )
    return rows


def compute_dual_plane_analysis(
    bug_issues: list[IssueItem],
    all_fix_prs: list[dict[str, Any]],
    categories: dict[str, dict[str, int]],
) -> dict[str, Any]:
    planes: dict[str, dict[str, Any]] = {}
    for plane_key, meta in PLANES.items():
        plane_bugs = [
            bi
            for bi in bug_issues
            if classify_plane(f"{bi.title} {bi.body}", bi.category) in (plane_key, "双平面交叉")
            or bi.category in meta["categories"]
        ]
        plane_prs = [
            fx
            for fx in all_fix_prs
            if fx["plane"] in (plane_key, "双平面交叉") or fx["category"] in meta["categories"]
        ]
        open_cnt = sum(1 for bi in plane_bugs if bi.state == "open")
        hotspots = []
        for hs in PLANE_HOTSPOTS.get(plane_key, []):
            info = analyze_hotspot(hs["symbol"], hs["desc"], hs["node"])
            info["plane"] = plane_key
            info["node"] = hs["node"]
            hint = hs["node"].lower()
            related = [
                f"!{x['pr']}"
                for x in plane_prs
                if any(hint in f.lower() for f in x.get("files", []))
                or (info["file_path"] and info["file_path"].split("/")[-1] in " ".join(x.get("files", [])))
            ]
            info["fix_prs"] = sorted(set(related))[:8]
            weaknesses: list[str] = []
            if open_cnt >= 10 and len(info["fix_prs"]) <= 2:
                weaknesses.append(f"Open Bug {open_cnt} 个，修复 PR 触达偏少")
            if info["callers"] >= 3 or info["impact_nodes"] >= 10:
                weaknesses.append(f"高扇出 callers={info['callers']} impact={info['impact_nodes']}")
            if info["affected_tests"] == 0:
                weaknesses.append("CodeGraph 无直接测试关联")
            info["weaknesses"] = weaknesses or ["结构风险可控，需运行时故障注入验证"]
            info["risk"] = "高" if len(weaknesses) >= 2 else "中" if weaknesses else "低"
            hotspots.append(info)
        planes[plane_key] = {
            "label": meta["label"],
            "color": meta["color"],
            "bug_total": len(plane_bugs),
            "open": open_cnt,
            "fix_pr_count": len(plane_prs),
            "bugs": sorted(plane_bugs, key=lambda x: x.number, reverse=True),
            "fix_prs": sorted(plane_prs, key=lambda x: x["date"], reverse=True),
            "hotspots": hotspots,
        }
    return {"planes": planes, "codegraph_stats": cg_status_stats()}


def build_mermaid_architecture(planes: dict[str, Any]) -> str:
    ub = planes.get("UB数据平面", {})
    rpc = planes.get("RPC控制平面", {})
    return f"""flowchart TB
  subgraph APP["业务层 Client / Worker"]
    C[ObjectClient / KvClient]
    W[Worker Service]
  end
  subgraph RPC["RPC 控制平面 · ZMQ"]
    direction TB
    R1[ZmqStubConnMgr] --> R2[ZmqStubConn]
    R2 --> R3[RPC Channel / 元数据]
    R3 --> W
  end
  subgraph UB["UB 数据平面 · URMA/Transfer"]
    direction TB
    U1[UrmaClient] --> U2[ConnectionManager]
    U2 --> U3[UrmaTransport / Jetty-JFS]
    U3 -.->|大数据传输| W
  end
  C --> R1
  C --> U1
  classDef weak fill:#fee2e2,stroke:#e74c3c
  class R1,R2,U2,U3 weak"""


def build_mermaid_plane_flow(plane_key: str, hotspots: list[dict[str, Any]], open_cnt: int) -> str:
    if plane_key == "UB数据平面":
        body = """flowchart LR
  A[Client UrmaClient] --> B[ConnectionManager]
  B --> C[UrmaTransport::Connect]
  C --> D[Jetty/JFS 数据通道]
  C -.->|故障| E[UrmaSuccessRateTracker]
  E --> F[Switch Worker !1086]"""
    else:
        body = """flowchart LR
  A[Client/SDK] --> B[ZmqStubConnMgr]
  B --> C[ZmqStubConn::Init]
  C --> D[RPC Send/Recv]
  D --> E[Worker 控制面]
  D -.->|慢/超时| F[CheckRpcLatency*]"""
    weak_nodes = [hs["node"] for hs in hotspots if hs.get("risk") in ("高", "中")][:3]
    note = " · ".join(weak_nodes) if weak_nodes else "暂无"
    return f"{body}\n  %% Open Bugs: {open_cnt} · 薄弱: {note}"


def summarize_fix(text: str, cat: str) -> str:
    t = text.lower()
    rules = [
        (r"urma|transporttype|jetty|jfs|failover|switch worker", "URMA 传输/故障切换与可观测性"),
        (r"zmq|rpc|serialization|slow.?log|latency|deadline", "ZMQ/RPC 连接、诊断与超时"),
        (r"migrate|migration|replica|location|scale|缩容|扩容", "扩缩容/迁移后元数据一致性"),
        (r"obs|l2|encoding|multipart|url", "L2/OBS 上传路径与编码"),
        (r"procmon|worker|benchmark|service.?discovery|pybind", "Worker/Client 运维与 SDK"),
        (r"get|mget|exist", "读路径可用性"),
        (r"set|mset|publish", "写路径可用性"),
        (r"etcd|keepalive|lease", "etcd/集群续租与协调"),
        (r"oom|memory|shm", "内存/OOM 资源保护"),
    ]
    for pat, desc in rules:
        if re.search(pat, t, re.I):
            return desc
    return f"{cat} 可靠性修复"


def analyze_hotspot(symbol: str, desc: str, file_hint: str) -> dict[str, Any]:
    callers_data = cg_json(["callers", "-l", "25", symbol]) or {}
    callees_data = cg_json(["callees", "-l", "25", symbol]) or {}
    impact_data = cg_json(["impact", "-d", "2", symbol]) or {}
    callers = callers_data.get("callers", []) if isinstance(callers_data, dict) else []
    callees = callees_data.get("callees", []) if isinstance(callees_data, dict) else []
    affected = impact_data.get("affected", []) if isinstance(impact_data, dict) else []
    query_hits = cg_json(["query", "-l", "3", symbol]) or []
    file_path = ""
    if isinstance(query_hits, list) and query_hits:
        file_path = query_hits[0].get("node", {}).get("filePath", "")
    test_hits = 0
    if file_path:
        aff = cg_json(["affected", file_path]) or {}
        test_hits = len(aff.get("affectedTests", [])) if isinstance(aff, dict) else 0
    return {
        "symbol": symbol,
        "desc": desc,
        "file_hint": file_hint,
        "file_path": file_path,
        "callers": len(callers),
        "callees": len(callees),
        "impact_nodes": impact_data.get("nodeCount", len(affected)) if isinstance(impact_data, dict) else 0,
        "caller_samples": [f"{c.get('name')} ({c.get('filePath', '').split('/')[-1]})" for c in callers[:4]],
        "affected_tests": test_hits,
    }


def cg_status_stats() -> dict[str, int]:
    try:
        out = subprocess.check_output(["codegraph", "status"], cwd=GIT_REPO, text=True)
        stats: dict[str, int] = {}
        for key, pat in [("files", r"Files:\s+([\d,]+)"), ("nodes", r"Nodes:\s+([\d,]+)"), ("edges", r"Edges:\s+([\d,]+)")]:
            m = re.search(pat, out)
            if m:
                stats[key] = int(m.group(1).replace(",", ""))
        return stats
    except subprocess.CalledProcessError:
        return {"files": 0, "nodes": 0, "edges": 0}


def extract_refs(text: str) -> tuple[list[int], list[int]]:
    prs = {int(x) for x in re.findall(r"merge_requests?/(\d+)", text, re.I)}
    prs |= {int(x) for x in re.findall(r"\b!(\d+)\b", text)}
    prs |= {int(x) for x in re.findall(r"\bPR\s*#?(\d+)\b", text, re.I)}
    issues = {int(x) for x in re.findall(r"(?:closes|close|fix(?:es)?|resolve(?:s)?)\s*#?(\d+)", text, re.I)}
    issues |= {int(x) for x in re.findall(r"issues?/(\d+)", text, re.I)}
    issues |= {int(x) for x in re.findall(r"(?:相关|关联|见)\s*#(\d+)", text)}
    return sorted(prs), sorted(issues)


def token_overlap(a: str, b: str) -> int:
    ta = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z_]{3,}", (a or "").lower()))
    tb = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z_]{3,}", (b or "").lower()))
    stop = {"fix", "bug", "the", "and", "for", "client", "worker", "datasystem"}
    ta -= stop
    tb -= stop
    return len(ta & tb)


def score_issue_pr_link(
    issue: IssueItem,
    pr_num: int,
    pr_title: str,
    pr_body: str,
    issue_pr_from_pr: bool,
    pr_issue_from_issue: bool,
    explicit_close: bool,
) -> tuple[int, str, str]:
    """Return (score 0-100, link_type, impact_hint)."""
    if explicit_close:
        return 100, "explicit_close", "强关联·PR声明修复"
    if issue_pr_from_pr:
        return 95, "pr_cites_issue", "强关联·PR引用Issue"
    if pr_issue_from_issue:
        return 90, "issue_cites_pr", "强关联·Issue引用PR"
    if re.search(rf"#\s*{issue.number}\b", pr_title):
        return 85, "pr_title_ref", "较强·PR标题含Issue号"
    it = f"{issue.title} {issue.body}"
    pt = f"{pr_title} {pr_body}"
    idims = tag_dims(it, issue.category)
    pdims = tag_dims(pt, classify_text(pt))
    overlap = token_overlap(issue.title, pr_title)
    dim_match = sum(1 for k in ("flow", "component", "fault") if idims[k] == pdims[k] and idims[k] != "其他")
    score = 20 + overlap * 8 + dim_match * 12
    if classify_text(it) == classify_text(pt):
        score += 10
    if score >= 55:
        return min(score, 75), "semantic_strong", "语义关联·多维匹配"
    if score >= 35:
        return score, "semantic_weak", "弱关联·仅部分匹配"
    return 0, "none", ""


def build_issue_pr_links(
    bug_issues: list[IssueItem],
    all_fix_prs: list[dict[str, Any]],
    pull_bodies: dict[int, str],
    pull_titles: dict[int, str],
    pr_to_issues: dict[int, list[int]],
    pr_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    pr_meta = {fx["pr"]: fx for fx in all_fix_prs}
    links: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()

    for issue in bug_issues:
        issue_text = f"{issue.title}\n{issue.body}"
        _, issue_prs = extract_refs(issue_text)
        for pr_num in set(issue.linked_prs + issue_prs):
            if pr_num not in pr_map:
                continue
            pr_body = pull_bodies.get(pr_num, "")
            pr_title = pull_titles.get(pr_num, "")
            explicit = bool(
                re.search(rf"(?:closes|fix(?:es)?|resolve(?:s)?)\s*#?{issue.number}\b", pr_body, re.I)
            )
            pr_cites = issue.number in pr_to_issues.get(pr_num, [])
            issue_cites = pr_num in issue_prs or pr_num in issue.linked_prs
            score, link_type, hint = score_issue_pr_link(
                issue, pr_num, pr_title, pr_body, pr_cites, issue_cites, explicit
            )
            if score < 35:
                continue
            key = (issue.number, pr_num)
            if key in seen:
                continue
            seen.add(key)
            fx = pr_meta.get(pr_num, {})
            idims = tag_dims(issue_text, issue.category)
            if issue.state == "open" and score >= 70:
                impact = "high" if explicit or pr_cites else "medium"
            elif issue.state == "open":
                impact = "medium"
            elif score >= 70:
                impact = "resolved"
            else:
                impact = "low"
            if issue.state == "open" and score >= 70 and pr_num in pr_map:
                impact = "partial" if not explicit else "high"
            links.append(
                {
                    "issue": issue.number,
                    "pr": pr_num,
                    "score": score,
                    "type": link_type,
                    "impact": impact,
                    "hint": hint,
                    "issue_state": issue.state,
                    "issue_title": issue.title[:80],
                    "issue_reporter": issue.reporter_label,
                    "issue_reporter_login": issue.reporter_login,
                    "pr_title": (pr_title or fx.get("title", ""))[:80],
                    "pr_author": fx.get("author_label", ""),
                    "pr_author_login": fx.get("author_login", ""),
                    "flow": idims["flow"],
                    "component": idims["component"],
                    "fault": idims["fault"],
                }
            )

    # second pass: semantic discovery for open issues without strong links
    for issue in bug_issues:
        if issue.state != "open":
            continue
        if any(l["issue"] == issue.number and l["score"] >= 70 for l in links):
            continue
        it = f"{issue.title} {issue.body}"
        idims = tag_dims(it, issue.category)
        candidates: list[dict[str, Any]] = []
        for fx in all_fix_prs:
            pr_num = fx["pr"]
            if (issue.number, pr_num) in seen:
                continue
            pt = f"{fx.get('title', '')} {fx.get('summary', '')}"
            pdims = tag_dims(pt, fx.get("category", ""), fx.get("files"))
            if idims["flow"] != pdims["flow"] and idims["component"] != pdims["component"]:
                continue
            overlap = token_overlap(issue.title, fx.get("title", ""))
            if overlap < 2 and idims["fault"] != pdims["fault"]:
                continue
            score = 35 + overlap * 8 + (10 if idims["flow"] == pdims["flow"] else 0) + (6 if idims["component"] == pdims["component"] else 0)
            if score < 45:
                continue
            candidates.append(
                {
                    "issue": issue.number,
                    "pr": pr_num,
                    "score": min(score, 55),
                    "type": "semantic_discover",
                    "impact": "medium",
                    "hint": "推断关联·待人工确认",
                    "issue_state": issue.state,
                    "issue_title": issue.title[:80],
                    "issue_reporter": issue.reporter_label,
                    "issue_reporter_login": issue.reporter_login,
                    "pr_title": fx.get("title", "")[:80],
                    "pr_author": fx.get("author_label", ""),
                    "pr_author_login": fx.get("author_login", ""),
                    "flow": idims["flow"],
                    "component": idims["component"],
                    "fault": idims["fault"],
                }
            )
        for cand in sorted(candidates, key=lambda x: -x["score"])[:3]:
            seen.add((cand["issue"], cand["pr"]))
            links.append(cand)
    links.sort(key=lambda x: (-x["score"], x["issue"]))
    return links


def compute_knowledge_graph(
    bug_issues: list[IssueItem],
    all_fix_prs: list[dict[str, Any]],
    links: list[dict[str, Any]],
    fema_cases: list[FemaCase],
    issue_by_fema: dict[str, list[IssueItem]],
) -> dict[str, Any]:
    issue_map = {i.number: i for i in bug_issues}
    pr_ids = {fx["pr"] for fx in all_fix_prs}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def add_node(nid: str, name: str, cat: str, size: int, meta: dict | None = None) -> None:
        if nid in node_ids:
            return
        node_ids.add(nid)
        nodes.append({"id": nid, "name": name, "category": cat, "symbolSize": size, **(meta or {})})

    link_stats = Counter(l["type"] for l in links)
    impact_stats = Counter(l["impact"] for l in links)

    # dimension hub nodes (流程/组件/故障)
    hub_flows = Counter(l["flow"] for l in links if l["score"] >= 50)
    for flow, cnt in hub_flows.most_common(8):
        add_node(f"flow:{flow}", flow, "hub_flow", min(28 + cnt, 48), {"hub": "flow"})

    for link in links:
        if link["score"] < 40:
            continue
        iid = link["issue"]
        pid = link["pr"]
        iss = issue_map.get(iid)
        if not iss:
            continue
        iname = f"#{iid}"
        pname = f"!{pid}"
        isize = 22 if link["impact"] in ("high", "partial") else 16
        if iss.state == "open":
            isize += 8
        add_node(f"issue:{iid}", iname, "issue_open" if iss.state == "open" else "issue_closed", isize,
                 {"state": iss.state, "title": iss.title[:60], "flow": link["flow"],
                  "reporter": link.get("issue_reporter", ""), "reporter_login": link.get("issue_reporter_login", "")})
        add_node(f"pr:{pid}", pname, "fix_pr", 18 + min(link["score"] // 10, 8),
                 {"title": (link.get("pr_title") or "")[:60], "author": link.get("pr_author", ""),
                  "author_login": link.get("pr_author_login", "")})
        edges.append({
            "source": f"issue:{iid}",
            "target": f"pr:{pid}",
            "value": link["score"],
            "type": link["type"],
            "impact": link["impact"],
            "label": link["hint"],
            "flow": link["flow"],
        })
        if link["score"] >= 50:
            fid = f"flow:{link['flow']}"
            if fid in node_ids:
                edges.append({
                    "source": f"issue:{iid}",
                    "target": fid,
                    "value": 2,
                    "type": "in_flow",
                    "impact": "low",
                    "label": "",
                    "flow": link["flow"],
                })

    # FEMA bridge for high-score links
    for link in links[:80]:
        if link["score"] < 70:
            continue
        iss = issue_map.get(link["issue"])
        if not iss or not iss.linked_fema:
            continue
        for fid in iss.linked_fema[:1]:
            add_node(f"fema:{fid}", fid, "fema", 14, {})
            edges.append({
                "source": f"issue:{link['issue']}",
                "target": f"fema:{fid}",
                "value": 3,
                "type": "maps_fema",
                "impact": "low",
                "label": "",
                "flow": link["flow"],
            })

    orphans_open = [i for i in bug_issues if i.state == "open" and not any(l["issue"] == i.number for l in links if l["score"] >= 70)]
    unlinked_prs = [p for p in pr_ids if not any(l["pr"] == p for l in links if l["score"] >= 70)]

    audit_rows = sorted(links, key=lambda x: (-x["score"], x["issue"]))[:60]

    return {
        "nodes": nodes,
        "edges": edges,
        "links": links,
        "audit": audit_rows,
        "stats": {
            "total_links": len(links),
            "strong_links": sum(1 for l in links if l["score"] >= 70),
            "weak_links": sum(1 for l in links if l["score"] < 70),
            "orphan_open_issues": len(orphans_open),
            "unlinked_fix_prs": len(unlinked_prs),
            "by_type": dict(link_stats),
            "by_impact": dict(impact_stats),
        },
        "orphans_open": [
            {
                "id": i.number,
                "title": i.title[:70],
                "flow": tag_dims(f"{i.title} {i.body}", i.category)["flow"],
                "reporter_login": i.reporter_login,
                "reporter_label": i.reporter_label,
            }
            for i in orphans_open
        ],
    }


def score_fema_match(issue: IssueItem, fema: FemaCase) -> int:
    text = f"{issue.title} {issue.body}".lower()
    score = 0
    for kw in fema.keywords:
        if len(kw) >= 2 and kw.lower() in text:
            score += 2
    if issue.category == fema.category:
        score += 3
    mode_core = re.sub(r"K_[A-Z_]+:?", "", fema.mode)
    for token in re.findall(r"[\u4e00-\u9fff]{2,}", mode_core):
        if token in issue.title:
            score += 4
    return score


def resolve_status(issue: IssueItem, pr_map: dict[int, dict[str, Any]]) -> str:
    if issue.state == "closed":
        return "resolved"
    merged = [p for p in issue.linked_prs if p in pr_map]
    if merged:
        return "partial"
    return "open"


def esc(s: Any) -> str:
    return html.escape(str(s or ""))


def badge(text: str, cls: str) -> str:
    return f'<span class="badge badge-{cls}">{esc(text)}</span>'


GITCODE_REPO = "https://gitcode.com/openeuler/yuanrong-datasystem"


def issue_link(num: int | str) -> str:
    n = int(num)
    return f'<a href="{GITCODE_REPO}/issues/{n}">#{n}</a>'


def pr_link(num: int | str) -> str:
    n = int(num)
    return f'<a href="{GITCODE_REPO}/merge_requests/{n}">!{n}</a>'


def join_issue_links(nums: list[int], limit: int | None = None) -> str:
    if not nums:
        return "—"
    items = nums if limit is None else nums[:limit]
    return ", ".join(issue_link(n) for n in items)


def join_pr_links(nums: list[int], limit: int | None = None) -> str:
    if not nums:
        return "—"
    items = nums if limit is None else nums[:limit]
    return ", ".join(pr_link(n) for n in items)


def fix_kind_badge(kind: str, label: str) -> str:
    cls = {
        "source_only": "yes",
        "source_and_test": "yes",
        "test_only": "partial",
        "config_only": "partial",
        "other": "partial",
        "unknown": "no",
    }.get(kind, "partial")
    return badge(label, cls)


def build_report(data: dict[str, Any]) -> str:
    stats = data["stats"]
    unresolved = data["unresolved"]
    matrix = data["matrix"]
    open_bugs = data["open_bugs"]
    p0_gaps = data["p0_gaps"]
    trends = data["trends"]
    dual_plane = data["dual_plane"]
    dim_summary = data["dim_summary"]
    browser = data["browser"]
    kg = data["knowledge_graph"]
    bug_issues = data["bug_issues"]
    all_fix_prs = data["all_fix_prs"]
    fix_scope = compute_fix_scope_summary(all_fix_prs, trends["months"])
    chart_json = json.dumps(trends, ensure_ascii=False)
    browse_json = json.dumps(browser, ensure_ascii=False)
    kg_json = json.dumps(kg, ensure_ascii=False)
    fix_scope_json = json.dumps(fix_scope, ensure_ascii=False)
    planes = dual_plane["planes"]
    ub = planes.get("UB数据平面", {})
    rpc = planes.get("RPC控制平面", {})
    cg = dual_plane["codegraph_stats"]
    kst = kg["stats"]
    mermaid_arch = build_mermaid_architecture(planes)
    mermaid_ub = build_mermaid_plane_flow("UB数据平面", ub.get("hotspots", []), ub.get("open", 0))
    mermaid_rpc = build_mermaid_plane_flow("RPC控制平面", rpc.get("hotspots", []), rpc.get("open", 0))

    def dim_table(rows: list[dict[str, Any]]) -> str:
        return "".join(
            f"<tr><td><strong>{esc(r['name'])}</strong></td><td>{r['issues']}</td>"
            f"<td>{r.get('open',0)}</td><td>{r.get('prs',0)}</td></tr>"
            for r in rows
        )

    gap_matrix = [r for r in matrix if r["status"] in ("gap", "open", "partial")]
    issue_map = {i.number: i for i in bug_issues}
    pr_meta = {fx["pr"]: fx for fx in all_fix_prs}
    matrix_rows = ""
    for row in gap_matrix:
        st_badge = {"covered": "yes", "partial": "partial", "gap": "no", "open": "no"}.get(row["status"], "partial")
        issues = join_issue_links(row["issues"])
        prs = join_pr_links(row["prs"])
        irep_labels = []
        irep_logins: list[str] = []
        for n in row["issues"][:5]:
            iss = issue_map.get(n)
            if iss and iss.reporter_login:
                irep_logins.append(iss.reporter_login)
                irep_labels.append(iss.reporter_label or iss.reporter_login)
        preps = []
        prep_logins: list[str] = []
        for n in row["prs"][:5]:
            fx = pr_meta.get(n)
            if fx and fx.get("author_login"):
                prep_logins.append(fx["author_login"])
                preps.append(fx.get("author_label", fx["author_login"]))
        irep_cell = ", ".join(dict.fromkeys(irep_labels)) or "—"
        prep_cell = ", ".join(dict.fromkeys(preps)) or "—"
        matrix_rows += (
            f"<tr data-issue-reporter='{esc(' '.join(sorted(set(irep_logins))))}' "
            f"data-pr-author='{esc(' '.join(sorted(set(prep_logins))))}'>"
            f"<td><code>{esc(row['id'])}</code></td><td>{esc(row['mode'][:48])}</td>"
            f"<td>{issues or '—'}</td><td>{irep_cell}</td><td>{prs or '—'}</td><td>{prep_cell}</td>"
            f"<td>{badge(row['status_label'], st_badge)}</td></tr>"
        )

    audit_rows = ""
    for lk in sorted(kg["links"], key=lambda x: (-x["score"], x["issue"])):
        ic = {"high": "no", "partial": "partial", "medium": "partial", "resolved": "yes", "low": "yes"}.get(lk["impact"], "partial")
        irep = lk.get("issue_reporter", "") or "—"
        prep = lk.get("pr_author", "") or "—"
        audit_rows += f"""<tr class="{'danger-row' if lk['impact']=='high' else 'warn-row' if lk['impact'] in ('partial','medium') else ''}"
          data-issue-reporter="{esc(lk.get('issue_reporter_login', ''))}" data-pr-author="{esc(lk.get('pr_author_login', ''))}">
          <td>{issue_link(lk['issue'])}</td>
          <td>{esc(irep)}</td>
          <td>{pr_link(lk['pr'])}</td>
          <td>{esc(prep)}</td>
          <td>{lk['score']}</td><td>{esc(lk['type'])}</td><td>{badge(lk['impact'], ic)}</td>
          <td>{esc(lk['flow'])}</td><td style="font-size:11px">{esc(lk['hint'])}</td></tr>"""

    orphan_rows = ""
    for o in kg.get("orphans_open", []):
        rep = o.get("reporter_label", "") or "—"
        orphan_rows += (
            f"<tr class='danger-row' data-issue-reporter='{esc(o.get('reporter_login', ''))}'>"
            f"<td>{issue_link(o['id'])}</td><td>{esc(rep)}</td><td>{esc(o['flow'])}</td>"
            f"<td>{esc(o['title'])}</td><td>{badge('无强关联', 'no')}</td></tr>"
        )

    fs_counts = fix_scope["counts"]
    fs_total = max(sum(fs_counts.values()), 1)
    author_counts = Counter(fx.get("author_login") or "unknown" for fx in all_fix_prs)
    author_labels = {fx.get("author_login") or "unknown": fx.get("author_label", "") for fx in all_fix_prs}
    author_options = build_person_options(author_counts, author_labels)
    reporter_counts = Counter(bi.reporter_login or "unknown" for bi in bug_issues)
    reporter_labels = {bi.reporter_login: bi.reporter_label for bi in bug_issues}
    reporter_options = build_person_options(reporter_counts, reporter_labels)

    fix_pr_rows = ""
    for fx in sorted(all_fix_prs, key=lambda x: x["date"], reverse=True):
        iss_nums = fx.get("issues", [])
        iss = join_issue_links(iss_nums)
        irep_labels = []
        irep_logins: list[str] = []
        for n in fx.get("issues", []):
            item = issue_map.get(n)
            if item and item.reporter_login:
                irep_logins.append(item.reporter_login)
                irep_labels.append(item.reporter_label or item.reporter_login)
        irep_cell = ", ".join(dict.fromkeys(irep_labels)) or "—"
        src_hint = ", ".join(f.split("/")[-1] for f in fx.get("source_files", [])[:3]) or "—"
        tst_hint = ", ".join(f.split("/")[-1] for f in fx.get("test_files", [])[:3]) or "—"
        row_cls = "warn-row" if fx["fix_kind"] == "test_only" else ""
        pr_auth = fx.get("author_login", "") or "unknown"
        fix_pr_rows += (
            f"<tr class='{row_cls}' data-pr-author='{esc(pr_auth)}' "
            f"data-issue-reporter='{esc(' '.join(sorted(set(irep_logins))))}'>"
            f"<td>{pr_link(fx['pr'])}</td>"
            f"<td>{esc(fx['date'])}</td><td>{author_cell(fx)}</td><td>{fix_kind_badge(fx['fix_kind'], fx['fix_kind_label'])}</td>"
            f"<td>{fx.get('source_count',0)}</td><td>{fx.get('test_count',0)}</td>"
            f"<td>{iss}</td><td>{esc(irep_cell)}</td><td style='font-size:11px'>{esc(fx['title'][:56])}</td>"
            f"<td style='font-size:10px;color:var(--muted)'>{esc(src_hint)}</td>"
            f"<td style='font-size:10px;color:var(--muted)'>{esc(tst_hint)}</td></tr>"
        )

    suspicious_rows = ""
    for s in fix_scope.get("suspicious", []):
        tf = ", ".join(f.split("/")[-1] for f in s.get("test_files", [])) or "—"
        pr_auth = s.get("author_login", "") or "unknown"
        suspicious_rows += (
            f"<tr class='warn-row' data-pr-author='{esc(pr_auth)}'><td>{pr_link(s['pr'])}</td>"
            f"<td>{esc(s['date'])}</td><td>{author_cell(s)}</td><td style='font-size:11px'>{esc(s['title'])}</td>"
            f"<td style='font-size:10px'>{esc(tf)}</td></tr>"
        )

    plane_block = ""
    for plane_key, mmd, pid in (("UB数据平面", mermaid_ub, "ub"), ("RPC控制平面", mermaid_rpc, "rpc")):
        pinfo = planes.get(plane_key, {})
        hs_rows = ""
        for hs in pinfo.get("hotspots", []):
            risk_cls = {"高": "no", "中": "partial", "低": "yes"}[hs["risk"]]
            hs_rows += f"<tr><td><code>{esc(hs['symbol'])}</code></td><td>{hs['callers']}/{hs['impact_nodes']}</td><td>{badge(hs['risk'], risk_cls)}</td></tr>"
        plane_block += f"""
<h3 id="plane-{pid}">{esc(pinfo.get('label', plane_key))}</h3>
<div class="summary-stat"><div class="stat err"><div class="num">{pinfo.get('open',0)}</div><div class="lbl">Open</div></div>
<div class="stat"><div class="num">{pinfo.get('fix_pr_count',0)}</div><div class="lbl">Fix PR</div></div></div>
<div class="card mermaid-wrap"><pre class="mermaid">{mmd}</pre></div>
<div class="card tbl-wrap"><table><tr><th>热点</th><th>Callers/Impact</th><th>风险</th></tr>{hs_rows}</table></div>"""

    open_rows = ""
    for item in unresolved.get("partial", []):
        prs = join_pr_links(item.get("prs", []))
        rep = item.get("reporter_label", "") or "—"
        open_rows += (
            f"<tr class='warn-row' data-issue-reporter='{esc(item.get('reporter_login', ''))}'>"
            f"<td>{issue_link(item['number'])}</td>"
            f"<td>{esc(rep)}</td><td>{esc(item['title'][:60])}</td><td>{prs}</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="alternate icon" href="/favicon.ico">
<title>Bugfix × FMEA × 关联知识图谱</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
:root {{ --bg:#f0f2f5; --card:#fff; --text:#1a1a2e; --muted:#57606a; --border:#e0e4e8; --accent:#4a90d9; --red:#e74c3c; --green:#27ae60; --nav:#1a1a2e; --ub:#e74c3c; --rpc:#4a90d9; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); line-height:1.55; }}
.topnav {{ position:sticky; top:0; z-index:100; background:var(--nav); padding:0 24px; height:52px; display:flex; align-items:center; gap:12px; box-shadow:0 2px 8px rgba(0,0,0,.25); }}
.topnav .logo {{ font-size:17px; font-weight:700; color:#fff; }} .topnav .logo a {{ color:#fff; text-decoration:none; }}
.topnav .topnav-links {{ margin-left:auto; display:flex; gap:6px; }}
.topnav .topnav-links a {{ color:rgba(255,255,255,.85); text-decoration:none; font-size:13px; padding:6px 12px; border-radius:6px; }}
.sidebar-toggle {{ padding:6px 12px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.28); color:#fff; border-radius:6px; cursor:pointer; font-size:13px; line-height:1; white-space:nowrap; }}
.sidebar-toggle:hover {{ background:rgba(255,255,255,.22); }}
.layout {{ display:flex; min-height:calc(100vh - 52px); }}
.sidebar {{ width:220px; flex-shrink:0; background:#f8f9fb; border-right:1px solid var(--border); position:sticky; top:52px; height:calc(100vh - 52px); overflow-y:auto; padding:8px 0; transition:width .2s ease, opacity .2s ease; }}
.layout.sidebar-hidden .sidebar {{ width:0; padding:0; border-right:none; overflow:hidden; opacity:0; pointer-events:none; }}
.sidebar .nav-h {{ padding:8px 16px 4px; font-size:10px; color:var(--muted); text-transform:uppercase; }}
.sidebar a {{ display:block; padding:7px 16px; color:var(--text); text-decoration:none; font-size:13px; border-left:3px solid transparent; white-space:nowrap; }}
.sidebar a.child {{ padding-left:28px; font-size:12px; color:var(--muted); }}
.sidebar a:hover {{ color:var(--accent); background:rgba(74,144,217,.06); border-left-color:var(--accent); }}
.main {{ flex:1; min-width:0; padding:18px 24px; max-width:100%; }}
section {{ margin-bottom:28px; scroll-margin-top:72px; }}
section h2 {{ font-size:19px; margin-bottom:10px; padding-bottom:6px; border-bottom:2px solid var(--border); }}
section h3 {{ font-size:15px; margin:12px 0 8px; }}
.card {{ background:var(--card); border-radius:8px; padding:14px; margin-bottom:10px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
.summary-stat {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(100px,1fr)); gap:8px; }}
.stat {{ background:#fff; border-radius:8px; padding:10px; text-align:center; border-top:3px solid var(--accent); }}
.stat .num {{ font-size:20px; font-weight:700; color:var(--accent); }} .stat .lbl {{ font-size:10px; color:var(--muted); }}
.stat.err {{ border-top-color:var(--red); }} .stat.err .num {{ color:var(--red); }}
.stat.ub {{ border-top-color:var(--ub); }} .stat.ub .num {{ color:var(--ub); }}
.stat.rpc {{ border-top-color:var(--rpc); }} .stat.rpc .num {{ color:var(--rpc); }}
.tbl-wrap {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; font-size:12px; }}
.tbl-pager {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:8px; font-size:12px; color:var(--muted); }}
.tbl-pager select {{ padding:4px 8px; border:1px solid var(--border); border-radius:6px; font-size:12px; }}
.tbl-pager button {{ padding:4px 10px; border:1px solid var(--border); background:#fff; border-radius:6px; cursor:pointer; font-size:12px; }}
.tbl-pager button:hover:not(:disabled) {{ border-color:var(--accent); color:var(--accent); }}
.tbl-pager button:disabled {{ opacity:.45; cursor:default; }}
.tbl-pager .tbl-page-info {{ margin:0 4px; }}
th {{ text-align:left; padding:6px 8px; background:#f8f9fa; border-bottom:2px solid var(--border); font-size:10px; color:#666; }}
td {{ padding:6px 8px; border-bottom:1px solid var(--border); }}
.danger-row td {{ background:#fef2f2 !important; }} .warn-row td {{ background:#fff7ed !important; }}
.badge {{ display:inline-block; padding:2px 7px; border-radius:10px; font-size:10px; font-weight:600; }}
.badge-yes {{ background:#dcfce7; color:#166534; }} .badge-no {{ background:#fee2e2; color:#991b1b; }} .badge-partial {{ background:#fef3c7; color:#92400e; }}
.alert {{ padding:9px 12px; border-radius:6px; font-size:12px; margin:8px 0; }}
.alert-info {{ background:#e0f2fe; border-left:4px solid #3b82f6; color:#075985; }}
.alert-warn {{ background:#fef3c7; border-left:4px solid #f59e0b; color:#92400e; }}
.alert-danger {{ background:#fee2e2; border-left:4px solid #ef4444; color:#991b1b; }}
.dim-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px; }}
.chart-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(380px,1fr)); gap:10px; }}
.chart-box {{ background:#fff; border-radius:8px; padding:10px; border:1px solid var(--border); }}
.chart {{ width:100%; height:300px; }} .chart.tall {{ height:420px; }} .chart.kg {{ height:min(72vh,720px); min-height:560px; width:100%; display:block; }}
.filter-bar {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:10px; font-size:12px; }}
.filter-bar select, .filter-bar input {{ padding:5px 8px; border:1px solid var(--border); border-radius:6px; font-size:12px; }}
.filter-bar button {{ padding:5px 10px; border:none; background:var(--accent); color:#fff; border-radius:6px; cursor:pointer; }}
.kg-legend {{ display:flex; flex-wrap:wrap; gap:8px; font-size:11px; margin-bottom:8px; }}
.kg-legend span {{ padding:2px 8px; border-radius:4px; }}
.mermaid-wrap pre {{ background:transparent; border:none; margin:0; }}
.appendix {{ margin-top:36px; padding-top:12px; border-top:2px dashed var(--border); }}
.appendix > h2 {{ font-size:16px; color:var(--muted); border-bottom-style:dashed; }}
.appendix details {{ margin-bottom:10px; }}
.appendix summary {{ cursor:pointer; font-size:14px; font-weight:600; padding:8px 0; color:var(--text); }}
.appendix summary:hover {{ color:var(--accent); }}
.reading-guide {{ font-size:12px; color:var(--muted); line-height:1.65; margin-bottom:10px; }}
.reading-guide strong {{ color:var(--text); }}
footer {{ text-align:center; color:var(--muted); font-size:11px; padding:14px; }}
</style>
</head>
<body>
<nav class="topnav">
  <div class="logo"><a href="ds-reliability-analysis.html">DataSystem 可靠性</a></div>
  <button type="button" id="sidebar-toggle" class="sidebar-toggle" aria-label="大纲导航" title="显示/隐藏大纲导航">◀ 隐藏大纲</button>
  <nav class="topnav-links"><a href="ds-fault-analysis.html">FMEA</a><a href="http://yche.me">yche.me</a></nav>
</nav>
<div class="layout" id="page-layout">
<aside class="sidebar" id="outline-sidebar">
  <a href="#summary">1. 执行摘要</a>
  <a href="#taxonomy">2. 三维分类</a>
  <a href="#fix-scope">3. PR 修复类型</a>
  <a href="#trending">4. 趋势分析</a>
  <a href="#trend-overview" class="child">4.1 总体趋势</a>
  <a href="#sec-trend-dim" class="child">4.2 分类趋势</a>
  <a href="#trend-plane" class="child">4.3 双平面趋势</a>
  <a href="#knowledge-graph">5. 关联知识图谱</a>
  <a href="#browser">6. 分类数据浏览</a>
  <a href="#appendix" class="child" style="margin-top:8px;color:var(--muted)">附录</a>
  <a href="#appendix-gaps" class="child">A. FMEA 缺口</a>
  <a href="#appendix-unresolved" class="child">B. 未彻底解决</a>
  <a href="#appendix-method" class="child">C. 方法说明</a>
  <a href="#appendix-dual-plane" class="child">D. 双平面薄弱点</a>
</aside>
<main class="main">

<section id="summary">
<h2>1. 执行摘要</h2>
<div class="card">
<div class="reading-guide">
  <strong>怎么读这份报告：</strong>§2 三维分类 → <strong>§3 PR 改源码还是测试</strong> → §4 趋势 → §5 知识图谱查 Issue↔PR 关联 → §6 明细浏览。双平面 CodeGraph 技术细节见附录 D。
</div>
<p style="font-size:12px;color:var(--muted)">main/master @ {esc(data['generated_at'])} · Issue↔PR 经显式引用 + 语义推断双层关联</p>
<div class="summary-stat">
  <div class="stat"><div class="num">{len(browser.get('issues',[]))}</div><div class="lbl">Bug Issues</div></div>
  <div class="stat"><div class="num">{len(all_fix_prs)}</div><div class="lbl">Fix PR</div></div>
  <div class="stat"><div class="num">{fix_scope['source_ratio']}%</div><div class="lbl">含源码修复</div></div>
  <div class="stat err"><div class="num">{kst['orphan_open_issues']}</div><div class="lbl">Open无强关联</div></div>
  <div class="stat ub"><div class="num">{ub.get('open',0)}</div><div class="lbl">UB Open</div></div>
  <div class="stat rpc"><div class="num">{rpc.get('open',0)}</div><div class="lbl">RPC Open</div></div>
</div>
<div class="alert alert-warn">关联审计：{kst['total_links']} 条边（强 {kst['strong_links']} / 弱 {kst['weak_links']}）；{kst['unlinked_fix_prs']} 个 Fix PR 无高置信 Issue 链。仅测试修复 {fs_counts.get('test_only',0)} 个（{round(fs_counts.get('test_only',0)/fs_total*100,1)}%）需关注是否真正修根因。</div>
</div>
</section>

<section id="taxonomy">
<h2>2. 三维分类体系</h2>
<div class="dim-grid">
  <div class="card tbl-wrap"><h3>流程</h3><table><tr><th>类</th><th>Issues</th><th>Open</th><th>PR</th></tr>{dim_table(dim_summary['flow'])}</table></div>
  <div class="card tbl-wrap"><h3>组件</h3><table><tr><th>类</th><th>Issues</th><th>Open</th><th>PR</th></tr>{dim_table(dim_summary['component'])}</table></div>
  <div class="card tbl-wrap"><h3>故障模式</h3><table><tr><th>类</th><th>Issues</th><th>Open</th><th>PR</th></tr>{dim_table(dim_summary['fault'])}</table></div>
</div>
</section>

<section id="fix-scope">
<h2>3. PR 修复类型（源码 vs 测试）</h2>
<div class="alert alert-info">
  基于 <code>git show --name-only</code> 变更文件自动分类：<code>src/</code> 下为<strong>源码</strong>，<code>tests/</code> 或 <code>*_test.*</code> 为<strong>测试</strong>。
  Issue/PR 提出人取自 GitCode <code>user.login</code>。
  「仅测试」PR 可能只加回归用例而未改生产代码，需人工复核。
</div>
<div class="summary-stat" style="margin-bottom:10px">
  <div class="stat"><div class="num">{fs_counts.get('source_only',0)}</div><div class="lbl">仅源码</div></div>
  <div class="stat"><div class="num">{fs_counts.get('source_and_test',0)}</div><div class="lbl">源码+测试</div></div>
  <div class="stat err"><div class="num">{fs_counts.get('test_only',0)}</div><div class="lbl">仅测试</div></div>
  <div class="stat"><div class="num">{fs_counts.get('config_only',0)}</div><div class="lbl">配置/构建</div></div>
</div>
<div class="chart-grid">
  <div class="chart-box"><div id="chart-fix-kind-pie" class="chart"></div></div>
  <div class="chart-box"><div id="chart-fix-kind-trend" class="chart"></div></div>
</div>
<h3>Fix PR 明细（{len(all_fix_prs)}）</h3>
<div class="filter-bar" style="margin-bottom:8px">
  <label>Issue提出人 <select id="fix-issue-reporter-filter"><option value="">全部</option>{reporter_options}</select></label>
  <label>PR提出人 <select id="fix-pr-author-filter"><option value="">全部</option>{author_options}</select></label>
</div>
<div class="card tbl-paginated" id="fix-pr-paginated" data-page-size="10">
<div class="tbl-wrap"><table id="fix-pr-table">
<tr><th>PR</th><th>日期</th><th>PR提出人</th><th>类型</th><th>源码</th><th>测试</th><th>Issue</th><th>Issue提出人</th><th>标题</th><th>源码文件</th><th>测试文件</th></tr>
{fix_pr_rows or '<tr><td colspan="11">无</td></tr>'}
</table></div>
</div>
<h3>仅测试修复（待复核，{len(fix_scope.get('suspicious',[]))}）</h3>
<div class="filter-bar" style="margin-bottom:8px">
  <label>PR提出人 <select id="fix-susp-pr-author-filter"><option value="">全部</option>{author_options}</select></label>
</div>
<div class="card tbl-paginated" id="fix-suspicious-paginated" data-page-size="10">
<div class="tbl-wrap"><table id="fix-suspicious-table">
<tr><th>PR</th><th>日期</th><th>PR提出人</th><th>标题</th><th>测试文件</th></tr>
{suspicious_rows or '<tr><td colspan="5">无</td></tr>'}
</table></div>
</div>
</section>

<section id="trending">
<h2>4. 趋势分析</h2>

<h3 id="trend-overview">4.1 总体趋势</h3>
<div class="chart-grid">
  <div class="chart-box"><div id="chart-bug-flow" class="chart"></div></div>
  <div class="chart-box"><div id="chart-backlog" class="chart"></div></div>
  <div class="chart-box"><div id="chart-fix-prs" class="chart"></div></div>
</div>

<h3 id="sec-trend-dim">4.2 分类趋势（流程 / 组件 / 故障）</h3>
<div class="card">
<div class="filter-bar">
  <label>维度 <select id="trend-dim-sel">
    <option value="flow">流程</option>
    <option value="component">组件</option>
    <option value="fault">故障模式</option>
  </select></label>
  <label>指标 <select id="trend-metric">
    <option value="opened">新增 Issue</option>
    <option value="closed">关闭 Issue</option>
    <option value="fix_prs">Fix PR</option>
    <option value="backlog">Open 积压</option>
  </select></label>
</div>
<div class="chart-box"><div id="chart-dim-trend" class="chart tall"></div></div>
<p id="trend-dim-meta" style="font-size:12px;color:var(--muted);margin-top:6px">—</p>
</div>

<h3 id="trend-plane">4.3 UB/RPC 双平面趋势</h3>
<div class="chart-grid">
  <div class="chart-box"><div id="chart-plane-bugs" class="chart"></div></div>
  <div class="chart-box"><div id="chart-plane-fix" class="chart"></div></div>
</div>
</section>

<section id="knowledge-graph">
<h2>5. 关联知识图谱</h2>
<div class="alert alert-info">
  节点：<span style="color:#e74c3c">■ Issue(Open)</span> · <span style="color:#27ae60">■ Issue(Closed)</span> · <span style="color:#4a90d9">■ Fix PR</span> · <span style="color:#8b5cf6">■ FMEA</span> · <span style="color:#f39c12">■ 流程Hub</span>。
  边粗细=关联置信度（显式 closes/fixes 最高）。
</div>
<div class="card">
<div class="filter-bar">
  <label>流程筛选 <select id="kg-flow"><option value="">全部</option></select></label>
  <label>最低置信度 <select id="kg-min-score"><option value="40">40+</option><option value="70" selected>70+ 强关联</option><option value="85">85+ 显式</option></select></label>
  <label>PR提出人 <select id="kg-author-filter"><option value="">全部</option>{author_options}</select></label>
  <label><input type="checkbox" id="kg-open-only"> 仅 Open Issue</label>
  <button type="button" id="kg-fit">适应画布</button>
  <button type="button" id="kg-reset">重置</button>
</div>
<div class="kg-legend">
  <span style="background:#fee2e2;color:#991b1b">高影响 Open</span>
  <span style="background:#fef3c7;color:#92400e">部分/推断</span>
  <span style="background:#dbeafe;color:#1e40af">Fix PR</span>
  <span style="background:#f3e8ff;color:#6b21a8">FMEA</span>
</div>
<div class="chart-box"><div id="chart-kg" class="chart kg"></div></div>
<p id="kg-detail" style="font-size:12px;color:var(--muted);margin-top:8px">点击节点查看详情</p>
</div>
<h3>关联审计表（{len(kg.get('links',[]))} 条，按置信度）</h3>
<div class="filter-bar" style="margin-bottom:8px">
  <label>Issue提出人 <select id="audit-issue-reporter-filter"><option value="">全部</option>{reporter_options}</select></label>
  <label>PR提出人 <select id="audit-pr-author-filter"><option value="">全部</option>{author_options}</select></label>
</div>
<div class="card tbl-paginated" id="audit-table-paginated" data-page-size="10">
<div class="tbl-wrap"><table id="audit-table">
<tr><th>Issue</th><th>Issue提出人</th><th>PR</th><th>PR提出人</th><th>分</th><th>类型</th><th>影响</th><th>流程</th><th>说明</th></tr>
{audit_rows or '<tr><td colspan="9">无</td></tr>'}
</table></div>
</div>
<h3>Open Issue 无强关联（{len(kg.get('orphans_open',[]))}）</h3>
<div class="filter-bar" style="margin-bottom:8px">
  <label>Issue提出人 <select id="orphan-issue-reporter-filter"><option value="">全部</option>{reporter_options}</select></label>
</div>
<div class="card tbl-paginated" id="orphan-table-paginated" data-page-size="10">
<div class="tbl-wrap"><table id="orphan-table"><tr><th>#</th><th>Issue提出人</th><th>流程</th><th>标题</th><th>判定</th></tr>{orphan_rows or '<tr><td colspan="5">无</td></tr>'}</table></div>
</div>
</section>

<section id="browser">
<h2>6. 分类数据浏览</h2>
<div class="card">
<div class="filter-bar">
  <label>维度 <select id="dim-type"><option value="flow">流程</option><option value="component">组件</option><option value="fault">故障</option></select></label>
  <label>分类 <select id="dim-value"><option value="">全部</option></select></label>
  <label>数据 <select id="data-type"><option value="issues">Issues</option><option value="prs">PRs</option></select></label>
  <label id="fix-kind-filter-wrap" style="display:none">修复类型 <select id="fix-kind-filter"><option value="">全部</option>
    <option value="source_only">仅源码</option><option value="source_and_test">源码+测试</option>
    <option value="test_only">仅测试</option><option value="config_only">配置/构建</option>
  </select></label>
  <label id="issue-reporter-filter-wrap">Issue提出人 <select id="issue-reporter-filter"><option value="">全部</option>{reporter_options}</select></label>
  <label id="pr-author-filter-wrap" style="display:none">PR提出人 <select id="pr-author-filter"><option value="">全部</option>{author_options}</select></label>
  <input id="kw-filter" type="search" placeholder="搜索…" style="min-width:160px">
</div>
<div class="chart-grid">
  <div class="chart-box"><div id="chart-browse-bar" class="chart"></div></div>
  <div class="chart-box"><div id="chart-browse-pie" class="chart"></div></div>
</div>
<p class="browse-meta" id="browse-meta" style="font-size:12px;color:var(--muted)">—</p>
<div class="tbl-paginated" id="browse-table-wrap" data-page-size="10">
<div class="tbl-wrap"><table><thead id="browse-head"><tr><th>ID</th><th>标题</th><th>流程</th><th>组件</th><th>故障</th><th id="browse-col-issue-reporter">Issue提出人</th><th id="browse-col-pr-author" style="display:none">PR提出人</th><th id="browse-col-extra">状态</th><th>关联</th></tr></thead><tbody id="browse-body"></tbody></table></div>
</div>
</div>
</section>

<section id="appendix" class="appendix">
<h2>附录</h2>

<details id="appendix-gaps" open>
<summary>A. FMEA 缺口（{len(gap_matrix)}）</summary>
<div class="filter-bar" style="margin-bottom:8px">
  <label>Issue提出人 <select id="gap-issue-reporter-filter"><option value="">全部</option>{reporter_options}</select></label>
  <label>PR提出人 <select id="gap-pr-author-filter"><option value="">全部</option>{author_options}</select></label>
</div>
<div class="card tbl-paginated" id="gap-table-paginated" data-page-size="10">
<div class="tbl-wrap"><table id="gap-table"><tr><th>FMEA</th><th>模式</th><th>Issues</th><th>Issue提出人</th><th>PRs</th><th>PR提出人</th><th>状态</th></tr>{matrix_rows}</table></div>
</div>
</details>

<details id="appendix-unresolved">
<summary>B. 未彻底解决（Open Issue 已有合入 PR，{len(unresolved.get('partial',[]))}）</summary>
<div class="filter-bar" style="margin-bottom:8px">
  <label>Issue提出人 <select id="unresolved-issue-reporter-filter"><option value="">全部</option>{reporter_options}</select></label>
</div>
<div class="card tbl-paginated" id="unresolved-table-paginated" data-page-size="10">
<div class="tbl-wrap"><table id="unresolved-table"><tr><th>Issue</th><th>Issue提出人</th><th>标题</th><th>已合入 PR</th></tr>{open_rows or '<tr><td colspan="4">无</td></tr>'}</table></div>
</div>
</details>

<details id="appendix-method">
<summary>C. 方法说明</summary>
<div class="card" style="font-size:12px;color:var(--muted)">
<ul style="padding-left:16px;line-height:1.7">
  <li>强关联：PR <code>closes/fixes #N</code>、<code>issues/N</code>、Issue <code>merge_requests/N</code>、PR 标题 <code>#N</code>。</li>
  <li>弱关联：流程/组件/故障三维 + 标题 token 重叠（score 35–74），标注「待人工确认」。</li>
  <li>影响：Open+强关联=high；Open+弱关联=medium；PR合入Issue仍Open=partial；Closed+强关联=resolved。</li>
  <li>修复类型：<code>src/</code>→源码；<code>tests/</code>、<code>*_test.*</code>→测试；其余按扩展名/路径推断。</li>
  <li>CodeGraph {cg.get('files',0):,} files；FMEA {stats['fema_total']} cases。</li>
</ul>
</div>
</details>

<details id="appendix-dual-plane">
<summary>D. 双平面薄弱点（CodeGraph 技术细节）</summary>
<div class="card mermaid-wrap"><pre class="mermaid">{mermaid_arch}</pre></div>
{plane_block}
<p style="font-size:12px;color:var(--muted);margin-top:8px">CodeGraph 扫描 {cg.get('files',0):,} 个源码文件；热点符号按 callers / impact 评估风险。</p>
</details>
</section>
<footer>Generated {esc(data['generated_at'])}</footer>
</main></div>
<script>
mermaid.initialize({{ startOnLoad:true, theme:'neutral', securityLevel:'loose' }});
(function initSidebarToggle() {{
  const layout = document.getElementById('page-layout');
  const btn = document.getElementById('sidebar-toggle');
  if (!layout || !btn) return;
  const KEY = 'ds-fema-outline-sidebar';
  function syncBtn(hidden) {{
    btn.textContent = hidden ? '☰ 大纲' : '◀ 隐藏大纲';
    btn.title = hidden ? '显示大纲导航' : '隐藏大纲导航';
  }}
  function setHidden(hidden, persist) {{
    layout.classList.toggle('sidebar-hidden', hidden);
    syncBtn(hidden);
    if (persist) localStorage.setItem(KEY, hidden ? 'hidden' : 'visible');
    window.dispatchEvent(new Event('resize'));
  }}
  setHidden(localStorage.getItem(KEY) === 'hidden', false);
  btn.addEventListener('click', () => setHidden(!layout.classList.contains('sidebar-hidden'), true));
}})();
const TREND = {chart_json};
const BROWSE = {browse_json};
const KG = {kg_json};
const FIX_SCOPE = {fix_scope_json};
const chartPalette = ['#e74c3c','#4a90d9','#67c23a','#f39c12','#8b5cf6','#06b6d4','#ec4899','#84cc16','#f97316','#6366f1','#14b8a6','#a855f7','#ef4444','#0ea5e9'];
const fixKindColors = {{source_only:'#27ae60', source_and_test:'#4a90d9', test_only:'#f39c12', config_only:'#94a3b8', other:'#cbd5e1', unknown:'#e2e8f0'}};
const dimColorMap = {{}};
(function initDimColors() {{
  let idx = 0;
  ['flow','component','fault'].forEach(dim => {{
    (BROWSE['by_'+dim] || []).forEach(item => {{
      if (dimColorMap[item.name] === undefined) {{
        dimColorMap[item.name] = chartPalette[idx++ % chartPalette.length];
      }}
    }});
  }});
  ['flow','component','fault'].forEach(dim => {{
    const cats = ((TREND.dims || {{}})[dim] || {{}}).categories || [];
    cats.forEach(c => {{
      if (dimColorMap[c] === undefined) {{
        dimColorMap[c] = chartPalette[idx++ % chartPalette.length];
      }}
    }});
  }});
}})();
function colorForCat(name) {{ return dimColorMap[name] || '#94a3b8'; }}

function createPagerBar(root, pageSize, onChange) {{
  let bar = root.querySelector('.tbl-pager');
  if (!bar) {{
    bar = document.createElement('div');
    bar.className = 'tbl-pager';
    bar.innerHTML = `<label>每页 <select class="tbl-page-size"><option value="10">10</option><option value="30">30</option><option value="50">50</option></select> 行</label>
      <span class="tbl-page-info"></span>
      <button type="button" class="tbl-prev">上一页</button>
      <button type="button" class="tbl-next">下一页</button>`;
    root.appendChild(bar);
  }}
  const sizeSel = bar.querySelector('.tbl-page-size');
  sizeSel.value = String(pageSize);
  sizeSel.onchange = () => onChange({{ size: +sizeSel.value, reset: true }});
  bar.querySelector('.tbl-prev').onclick = () => onChange({{ pageDelta: -1 }});
  bar.querySelector('.tbl-next').onclick = () => onChange({{ pageDelta: 1 }});
  return bar;
}}
function updatePagerUI(bar, page, pageSize, total) {{
  const maxPage = Math.max(1, Math.ceil(Math.max(total, 1) / pageSize));
  const start = total ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(page * pageSize, total);
  bar.querySelector('.tbl-page-info').textContent = total
    ? `第 ${{page}}/${{maxPage}} 页 · ${{start}}–${{end}} / 共 ${{total}} 条`
    : '无数据';
  bar.querySelector('.tbl-prev').disabled = page <= 1;
  bar.querySelector('.tbl-next').disabled = page >= maxPage;
  bar.style.display = total > pageSize ? '' : 'none';
}}
function tableAllDataRows(table) {{
  if (!table) return [];
  const rows = table.tBodies.length ? [...table.tBodies[0].rows] : [...table.rows];
  return rows.filter(r => !r.querySelector('th') && !r.querySelector('td[colspan]'));
}}
function tableDataRows(table) {{
  return tableAllDataRows(table).filter(r => r.dataset.filtered !== '1');
}}
class StaticTablePager {{
  constructor(root) {{
    this.root = root;
    root._tblPager = this;
    this.table = root.querySelector('table');
    this.pageSize = parseInt(root.dataset.pageSize || '10', 10);
    this.page = 1;
    this.bar = createPagerBar(root, this.pageSize, (cmd) => {{
      if (cmd.size) {{ this.pageSize = cmd.size; this.page = 1; }}
      if (cmd.reset) this.page = 1;
      if (cmd.pageDelta) this.page = Math.max(1, this.page + cmd.pageDelta);
      this.render();
    }});
    this.render();
  }}
  render() {{
    const allRows = tableAllDataRows(this.table);
    const rows = allRows.filter(r => r.dataset.filtered !== '1');
    allRows.forEach(r => {{ r.style.display = 'none'; }});
    const total = rows.length;
    const maxPage = Math.max(1, Math.ceil(Math.max(total, 1) / this.pageSize));
    this.page = Math.min(Math.max(1, this.page), maxPage);
    const start = (this.page - 1) * this.pageSize;
    rows.forEach((r, i) => {{ r.style.display = (i >= start && i < start + this.pageSize) ? '' : 'none'; }});
    updatePagerUI(this.bar, this.page, this.pageSize, total);
  }}
}}
function initStaticTablePagers() {{
  document.querySelectorAll('.tbl-paginated').forEach(el => {{
    if (el.id === 'browse-table-wrap') return;
    new StaticTablePager(el);
  }});
}}
function personSetMatch(datasetValue, selected) {{
  if (!selected) return true;
  return (datasetValue || '').split(/\\s+/).filter(Boolean).includes(selected);
}}
function applyPersonFiltersOnTable(table, prAuthor, issueReporter) {{
  if (!table) return;
  tableAllDataRows(table).forEach(tr => {{
    let ok = true;
    if (prAuthor) ok = ok && personSetMatch(tr.dataset.prAuthor || tr.dataset.author || '', prAuthor);
    if (issueReporter) ok = ok && personSetMatch(tr.dataset.issueReporter || '', issueReporter);
    tr.dataset.filtered = ok ? '' : '1';
  }});
  const wrap = table.closest('.tbl-paginated');
  if (wrap && wrap._tblPager) {{
    wrap._tblPager.page = 1;
    wrap._tblPager.render();
  }}
}}
function applyFixFilters() {{
  const prAuthor = document.getElementById('fix-pr-author-filter').value;
  const issueReporter = document.getElementById('fix-issue-reporter-filter').value;
  applyPersonFiltersOnTable(document.getElementById('fix-pr-table'), prAuthor, issueReporter);
  applyPersonFiltersOnTable(document.getElementById('fix-suspicious-table'), document.getElementById('fix-susp-pr-author-filter').value, '');
}}
function applyAuditFilters() {{
  applyPersonFiltersOnTable(
    document.getElementById('audit-table'),
    document.getElementById('audit-pr-author-filter').value,
    document.getElementById('audit-issue-reporter-filter').value
  );
}}
initStaticTablePagers();
['fix-pr-author-filter', 'fix-issue-reporter-filter', 'fix-susp-pr-author-filter'].forEach(id =>
  document.getElementById(id).addEventListener('change', applyFixFilters));
['audit-pr-author-filter', 'audit-issue-reporter-filter'].forEach(id =>
  document.getElementById(id).addEventListener('change', applyAuditFilters));
document.getElementById('orphan-issue-reporter-filter').addEventListener('change', () =>
  applyPersonFiltersOnTable(document.getElementById('orphan-table'), '', document.getElementById('orphan-issue-reporter-filter').value));
['gap-pr-author-filter', 'gap-issue-reporter-filter'].forEach(id =>
  document.getElementById(id).addEventListener('change', () => applyPersonFiltersOnTable(
    document.getElementById('gap-table'),
    document.getElementById('gap-pr-author-filter').value,
    document.getElementById('gap-issue-reporter-filter').value)));
document.getElementById('unresolved-issue-reporter-filter').addEventListener('change', () =>
  applyPersonFiltersOnTable(document.getElementById('unresolved-table'), '', document.getElementById('unresolved-issue-reporter-filter').value));

function lineSeries(n,d,c) {{ return {{ name:n, type:'line', smooth:true, data:d, lineStyle:{{width:2,color:c}}, itemStyle:{{color:c}}, areaStyle:{{opacity:.06,color:c}} }}; }}
echarts.init(document.getElementById('chart-bug-flow')).setOption({{
  title:{{text:'Bug 总体（月）',left:'center',textStyle:{{fontSize:13}}}},
  tooltip:{{trigger:'axis'}}, legend:{{top:28, data:['新增','关闭']}}, grid:{{top:64,left:44,right:16,bottom:28}},
  xAxis:{{type:'category',data:TREND.months}}, yAxis:{{type:'value'}},
  series:[lineSeries('新增',TREND.bug_opened,'#ee6666'), lineSeries('关闭',TREND.bug_closed,'#67c23a')]
}});
echarts.init(document.getElementById('chart-backlog')).setOption({{
  title:{{text:'Open 积压',left:'center',textStyle:{{fontSize:13}}}},
  tooltip:{{trigger:'axis'}}, legend:{{top:28, data:['积压']}}, grid:{{top:64,left:44,right:16,bottom:28}},
  xAxis:{{type:'category',data:TREND.months}}, yAxis:{{type:'value'}},
  series:[lineSeries('积压',TREND.open_backlog,'#f39c12')]
}});
echarts.init(document.getElementById('chart-fix-prs')).setOption({{
  title:{{text:'Fix PR 合入',left:'center',textStyle:{{fontSize:13}}}},
  tooltip:{{trigger:'axis'}}, legend:{{top:28, data:['Fix PR']}}, grid:{{top:64,left:44,right:16,bottom:28}},
  xAxis:{{type:'category',data:TREND.months}}, yAxis:{{type:'value'}},
  series:[lineSeries('Fix PR',TREND.fix_prs,'#4a90d9')]
}});
echarts.init(document.getElementById('chart-plane-bugs')).setOption({{
  title:{{text:'双平面 · 新增 Issue',left:'center',textStyle:{{fontSize:13}}}},
  tooltip:{{trigger:'axis'}}, legend:{{top:28, data:['UB','RPC']}}, grid:{{top:64,left:44,right:16,bottom:28}},
  xAxis:{{type:'category',data:TREND.months}}, yAxis:{{type:'value'}},
  series:[lineSeries('UB',(TREND.plane_bug_opened||{{}})['UB数据平面']||[],'#e74c3c'), lineSeries('RPC',(TREND.plane_bug_opened||{{}})['RPC控制平面']||[],'#4a90d9')]
}});
echarts.init(document.getElementById('chart-plane-fix')).setOption({{
  title:{{text:'双平面 · Fix PR',left:'center',textStyle:{{fontSize:13}}}},
  tooltip:{{trigger:'axis'}}, legend:{{top:28, data:['UB','RPC']}}, grid:{{top:64,left:44,right:16,bottom:28}},
  xAxis:{{type:'category',data:TREND.months}}, yAxis:{{type:'value'}},
  series:[lineSeries('UB',(TREND.plane_fix_prs||{{}})['UB数据平面']||[],'#e74c3c'), lineSeries('RPC',(TREND.plane_fix_prs||{{}})['RPC控制平面']||[],'#4a90d9')]
}});

const trendDimLabels = {{flow:'流程', component:'组件', fault:'故障模式'}};
const trendMetricLabels = {{opened:'新增 Issue', closed:'关闭 Issue', fix_prs:'Fix PR', backlog:'Open 积压'}};
let trendDimChart;
function renderDimTrend() {{
  const dim = document.getElementById('trend-dim-sel').value;
  const metric = document.getElementById('trend-metric').value;
  const block = (TREND.dims || {{}})[dim] || {{categories:[]}};
  const cats = block.categories || [];
  const metricData = block[metric] || {{}};
  const series = cats.map(c => lineSeries(c, metricData[c] || [], colorForCat(c)));
  if (!trendDimChart) trendDimChart = echarts.init(document.getElementById('chart-dim-trend'));
  trendDimChart.setOption({{
    title:{{text: trendDimLabels[dim] + ' · ' + trendMetricLabels[metric], left:'center', textStyle:{{fontSize:13}}}},
    tooltip:{{trigger:'axis'}},
    legend:{{top:28, type:'scroll', data:cats}},
    grid:{{top:72, left:48, right:16, bottom:28}},
    xAxis:{{type:'category', data:TREND.months}},
    yAxis:{{type:'value', minInterval: metric==='backlog'?1:undefined}},
    series
  }}, true);
  const openNow = block.open_now || {{}};
  const meta = cats.map(c => `${{c}}(当前Open ${{openNow[c]||0}})`).join(' · ');
  document.getElementById('trend-dim-meta').textContent = meta || '—';
}}
document.getElementById('trend-dim-sel').onchange = renderDimTrend;
document.getElementById('trend-metric').onchange = renderDimTrend;
renderDimTrend();

const catColors = {{
  issue_open:'#e74c3c', issue_closed:'#27ae60', fix_pr:'#4a90d9', fema:'#8b5cf6', hub_flow:'#f39c12'
}};
const linkByEdge = {{}};
KG.links.forEach(l => {{ linkByEdge['issue:'+l.issue+'->pr:'+l.pr] = l; }});
const openIssueIds = new Set(KG.nodes.filter(n => n.category === 'issue_open').map(n => n.id));
let kgChart;
let kgFitZoom = null;
let kgPaintQueued = false;
let kgResizeObs = null;

function kgContainerReady(el) {{
  if (!el) return false;
  const w = el.clientWidth || el.offsetWidth;
  const h = el.clientHeight || el.offsetHeight;
  return w >= 200 && h >= 200;
}}

function ensureKgChart(el) {{
  if (kgChart) {{
    const dom = kgChart.getDom();
    if (!dom || dom.offsetWidth < 200 || dom.offsetHeight < 200) {{
      kgChart.dispose();
      kgChart = null;
    }}
  }}
  if (!kgChart) kgChart = echarts.init(el, null, {{ renderer: 'canvas' }});
  return kgChart;
}}

function scheduleKgPaint() {{
  if (kgPaintQueued) return;
  kgPaintQueued = true;
  let tries = 0;
  const tick = () => {{
    tries++;
    const el = document.getElementById('chart-kg');
    if (kgContainerReady(el)) {{
      kgPaintQueued = false;
      filterKg(true);
      return;
    }}
    if (tries < 48) requestAnimationFrame(tick);
    else kgPaintQueued = false;
  }};
  requestAnimationFrame(tick);
}}

function setupKgResize() {{
  const el = document.getElementById('chart-kg');
  if (!el || kgResizeObs || typeof ResizeObserver === 'undefined') return;
  kgResizeObs = new ResizeObserver(() => {{
    if (!kgContainerReady(el)) return;
    if (kgChart) {{
      kgChart.resize();
      if (!kgChart.getOption()?.series?.length) filterKg(true);
    }} else {{
      scheduleKgPaint();
    }}
  }});
  kgResizeObs.observe(el);
}}

function kgZoomFor(n) {{
  if (n <= 15) return 0.95;
  if (n <= 30) return 0.82;
  if (n <= 55) return 0.72;
  if (n <= 90) return 0.56;
  if (n <= 150) return 0.44;
  return 0.34;
}}

function filterKg(forcePaint) {{
  const flow = document.getElementById('kg-flow').value;
  const minScore = +document.getElementById('kg-min-score').value;
  const openOnly = document.getElementById('kg-open-only').checked;
  const authorLogin = document.getElementById('kg-author-filter').value;

  let edges = KG.edges.filter(e => {{
    if (e.type !== 'in_flow' && (e.value || 0) < minScore) return false;
    if (flow && e.flow !== flow) return false;
    if (openOnly) {{
      const issueId = e.source.startsWith('issue:') ? e.source : (e.target.startsWith('issue:') ? e.target : null);
      if (!issueId || !openIssueIds.has(issueId)) return false;
    }}
    return true;
  }});

  if (authorLogin) {{
    const matchingPrIds = new Set(
      KG.nodes.filter(n => n.category === 'fix_pr' && n.author_login === authorLogin).map(n => n.id)
    );
    if (!matchingPrIds.size) {{
      edges = [];
    }} else {{
      const allowed = new Set(matchingPrIds);
      for (let pass = 0; pass < 12; pass++) {{
        edges.forEach(e => {{
          if (allowed.has(e.source)) allowed.add(e.target);
          if (allowed.has(e.target)) allowed.add(e.source);
        }});
      }}
      edges = edges.filter(e => {{
        if (!allowed.has(e.source) || !allowed.has(e.target)) return false;
        if (e.source.startsWith('pr:') && !matchingPrIds.has(e.source)) return false;
        if (e.target.startsWith('pr:') && !matchingPrIds.has(e.target)) return false;
        return true;
      }});
    }}
  }}

  const nodeSet = new Set();
  edges.forEach(e => {{ nodeSet.add(e.source); nodeSet.add(e.target); }});

  let rawNodes = KG.nodes.filter(n => {{
    if (!nodeSet.has(n.id)) return false;
    if (flow && n.category === 'hub_flow' && n.id !== 'flow:' + flow) return false;
    return true;
  }});

  if (flow) {{
    const hubId = 'flow:' + flow;
    if (!rawNodes.some(n => n.id === hubId)) {{
      const hub = KG.nodes.find(n => n.id === hubId);
      if (hub && rawNodes.length) rawNodes.push(hub);
    }}
  }}

  const dense = rawNodes.length > 50;
  const sizeScale = dense ? 0.85 : 1.0;
  const graphNodes = rawNodes.map(n => ({{
    id: n.id,
    name: n.id,
    displayName: n.name,
    category: n.category,
    symbolSize: Math.max(12, Math.round((n.symbolSize || 18) * sizeScale)),
    title: n.title,
    author: n.author || '',
    author_login: n.author_login || '',
    reporter: n.reporter || '',
    reporter_login: n.reporter_login || '',
    flow: n.flow || (n.hub === 'flow' ? n.name : ''),
    state: n.state,
    itemStyle: {{ color: catColors[n.category] || '#999' }},
    label: {{ show: rawNodes.length <= 80 && (n.symbolSize || 18) >= 14, formatter: n.name, fontSize: dense ? 8 : 9 }}
  }}));
  const graphLinks = edges.map(e => ({{
    source: e.source,
    target: e.target,
    value: e.value,
    type: e.type,
    impact: e.impact,
    flow: e.flow,
    label: e.label || e.type || '',
    lineStyle: {{
      width: e.type === 'in_flow' ? 1 : Math.max(1, (e.value || 2) / 18),
      curveness: 0.12,
      color: e.impact === 'high' ? '#e74c3c' : e.impact === 'partial' ? '#f39c12' : '#94a3b8'
    }}
  }}));

  const el = document.getElementById('chart-kg');
  if (!graphNodes.length) {{
    if (kgContainerReady(el)) {{
      const emptyChart = ensureKgChart(el);
      emptyChart.clear();
      emptyChart.setOption({{
        title: {{ text: '当前筛选无节点', left: 'center', top: 'middle', textStyle: {{ color: '#94a3b8', fontSize: 14 }} }}
      }}, true);
    }}
    document.getElementById('kg-detail').textContent = '无匹配节点，请降低置信度、取消流程/PR提出人筛选';
    return;
  }}

  if (!kgContainerReady(el)) {{
    if (!forcePaint) scheduleKgPaint();
    return;
  }}

  const chart = ensureKgChart(el);
  chart.resize();

  const useForce = graphNodes.length > 90;
  const zoom = kgFitZoom != null ? kgFitZoom : kgZoomFor(graphNodes.length);
  try {{
    chart.setOption({{
      animation: false,
      tooltip: {{ formatter(p) {{
        if (p.dataType === 'edge') return (p.data.label || p.data.type || '') + ' 置信度 ' + p.data.value + (p.data.flow ? '<br>流程:' + p.data.flow : '');
        const d = p.data;
        return (d.displayName || d.name || '') + '<br>' + (d.title || '')
          + (d.reporter ? '<br>Issue提出人:' + d.reporter : '')
          + (d.author ? '<br>PR提出人:' + d.author : '')
          + '<br>流程:' + (d.flow || '—');
      }} }},
      series: [{{
        type: 'graph',
        layout: useForce ? 'force' : 'circular',
        circular: useForce ? undefined : {{ rotateLabel: true }},
        force: useForce ? {{ repulsion: dense ? 120 : 220, edgeLength: [40, 100], gravity: 0.08, friction: 0.6 }} : undefined,
        roam: true,
        draggable: true,
        zoom,
        center: ['50%', '50%'],
        scaleLimit: {{ min: 0.08, max: 5 }},
        data: graphNodes,
        links: graphLinks,
        emphasis: {{ focus: 'adjacency', lineStyle: {{ width: 4 }} }},
        lineStyle: {{ opacity: 0.82, curveness: 0.08 }}
      }}]
    }}, true);
    chart.resize();
  }} catch (err) {{
    document.getElementById('kg-detail').textContent = '图谱渲染异常: ' + (err.message || err);
    return;
  }}

  const flowHint = flow ? (' · 流程「' + flow + '」') : '';
  const authorHint = authorLogin ? (' · PR提出人「' + authorLogin + '」') : '';
  document.getElementById('kg-detail').textContent = '显示 ' + graphNodes.length + ' 节点 / ' + graphLinks.length + ' 边' + flowHint + authorHint + ' · 点击节点查看详情 · 滚轮缩放/拖拽';
  chart.off('click');
  chart.on('click', p => {{
    const det = document.getElementById('kg-detail');
    if (p.dataType === 'node') {{
      const auth = p.data.author ? (' · PR:' + p.data.author) : '';
      const rep = p.data.reporter ? (' · Issue:' + p.data.reporter) : '';
      det.textContent = (p.data.displayName || p.data.name || '') + auth + rep + ' · ' + (p.data.title || p.data.id || '');
    }} else det.textContent = (p.data.label || p.data.type || '') + ' · 置信度 ' + (p.data.value || '');
  }});
}}
const flows = [...new Set(KG.links.map(l => l.flow))].sort();
document.getElementById('kg-flow').innerHTML = '<option value="">全部</option>' + flows.map(f => `<option value="${{f}}">${{f}}</option>`).join('');
['kg-flow', 'kg-min-score', 'kg-open-only', 'kg-author-filter'].forEach(id => document.getElementById(id).addEventListener('change', () => {{ kgFitZoom = null; filterKg(true); }}));
document.getElementById('kg-fit').onclick = () => {{
  kgFitZoom = 0.55;
  filterKg(true);
}};
document.getElementById('kg-reset').onclick = () => {{
  document.getElementById('kg-flow').value = '';
  document.getElementById('kg-min-score').value = '70';
  document.getElementById('kg-open-only').checked = false;
  document.getElementById('kg-author-filter').value = '';
  kgFitZoom = null;
  filterKg(true);
}};
setupKgResize();
window.addEventListener('load', () => {{ scheduleKgPaint(); setTimeout(scheduleKgPaint, 200); setTimeout(scheduleKgPaint, 800); }});
const kgSection = document.getElementById('knowledge-graph');
if (kgSection && 'IntersectionObserver' in window) {{
  new IntersectionObserver((ents) => {{ if (ents.some(e => e.isIntersecting)) scheduleKgPaint(); }}).observe(kgSection);
}}
window.addEventListener('hashchange', () => {{ if (location.hash === '#knowledge-graph') scheduleKgPaint(); }});
scheduleKgPaint();

let browseBar, browsePie;
const browsePager = {{ page: 1, size: 10, bar: null }};
function aggKey() {{ return document.getElementById('dim-type').value; }}
function aggList() {{ return BROWSE['by_'+aggKey()]||[]; }}
function fillDimOptions() {{
  const sel = document.getElementById('dim-value');
  const cur = sel.value;
  sel.innerHTML = '<option value="">全部</option>' + aggList().map(r=>`<option value="${{r.name}}">${{r.name}} (${{r.issues}})</option>`).join('');
  if ([...sel.options].some(o=>o.value===cur)) sel.value=cur;
}}
function filteredRows() {{
  const dim=aggKey(), val=document.getElementById('dim-value').value, dtype=document.getElementById('data-type').value;
  const kw=(document.getElementById('kw-filter').value||'').toLowerCase();
  const fk=document.getElementById('fix-kind-filter').value;
  const prAuthor=document.getElementById('pr-author-filter').value;
  const issueReporter=document.getElementById('issue-reporter-filter').value;
  let rows = dtype==='issues' ? BROWSE.issues : BROWSE.prs;
  if (val) rows=rows.filter(r=>r[dim]===val);
  if (dtype==='prs' && fk) rows=rows.filter(r=>r.fix_kind===fk);
  if (dtype==='prs' && prAuthor) rows=rows.filter(r=>(r.author_login||'')===prAuthor);
  if (dtype==='issues' && issueReporter) rows=rows.filter(r=>(r.reporter_login||'')===issueReporter);
  if (kw) rows=rows.filter(r=>(r.title||'').toLowerCase().includes(kw));
  return rows;
}}
function renderBrowseTable() {{
  const allRows=filteredRows(), total=allRows.length, dtype=document.getElementById('data-type').value;
  const maxPage=Math.max(1, Math.ceil(Math.max(total,1)/browsePager.size));
  if (browsePager.page>maxPage) browsePager.page=maxPage;
  const start=(browsePager.page-1)*browsePager.size;
  const rows=allRows.slice(start, start+browsePager.size);
  document.getElementById('browse-meta').textContent=`筛选共 ${{total}} 条`;
  const extraHdr = document.getElementById('browse-col-extra');
  const issueRepHdr = document.getElementById('browse-col-issue-reporter');
  const prAuthHdr = document.getElementById('browse-col-pr-author');
  extraHdr.textContent = dtype==='issues' ? '状态' : '修复类型';
  document.getElementById('fix-kind-filter-wrap').style.display = dtype==='prs' ? '' : 'none';
  document.getElementById('issue-reporter-filter-wrap').style.display = dtype==='issues' ? '' : 'none';
  document.getElementById('pr-author-filter-wrap').style.display = dtype==='prs' ? '' : 'none';
  if (issueRepHdr) issueRepHdr.style.display = dtype==='issues' ? '' : 'none';
  if (prAuthHdr) prAuthHdr.style.display = dtype==='prs' ? '' : 'none';
  document.getElementById('browse-body').innerHTML = rows.length ? rows.map(r => {{
    if (dtype==='issues') {{
      const prs=(r.prs||[]).map(p=>`<a href="https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/${{p}}">!${{p}}</a>`).join(' ')||'—';
      const rep = r.reporter_label || r.reporter_login || '—';
      const repLink = r.reporter_login ? `<a href="https://gitcode.com/${{r.reporter_login}}">${{rep}}</a>` : rep;
      return `<tr class="${{r.state==='open'?'danger-row':''}}"><td><a href="${{r.url}}">#${{r.id}}</a></td><td>${{r.title}}</td><td>${{r.flow}}</td><td>${{r.component}}</td><td>${{r.fault}}</td><td>${{repLink}}</td><td style="display:none"></td><td>${{r.state}}</td><td>${{prs}}</td></tr>`;
    }}
    const fk = r.fix_kind_label || r.fix_kind || '—';
    const fkCls = r.fix_kind==='test_only' ? 'warn-row' : '';
    const cnt = `src:${{r.source_count||0}} tst:${{r.test_count||0}}`;
    const auth = r.author_label || r.author_login || '—';
    const authLink = r.author_login ? `<a href="https://gitcode.com/${{r.author_login}}">${{auth}}</a>` : auth;
    return `<tr class="${{fkCls}}"><td><a href="https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/${{r.id}}">!${{r.id}}</a></td><td>${{r.title}}</td><td>${{r.flow}}</td><td>${{r.component}}</td><td>${{r.fault}}</td><td style="display:none"></td><td>${{authLink}}</td><td>${{fk}} (${{cnt}})</td><td>${{r.date}}</td></tr>`;
  }}).join('') : '<tr><td colspan="9">无匹配数据</td></tr>';
  const wrap=document.getElementById('browse-table-wrap');
  if (!browsePager.bar) {{
    browsePager.bar=createPagerBar(wrap, browsePager.size, (cmd) => {{
      if (cmd.size) {{ browsePager.size=cmd.size; browsePager.page=1; }}
      if (cmd.reset) browsePager.page=1;
      if (cmd.pageDelta) browsePager.page=Math.max(1, browsePager.page+cmd.pageDelta);
      renderBrowseTable();
    }});
  }}
  updatePagerUI(browsePager.bar, browsePager.page, browsePager.size, total);
}}
function renderBrowseCharts() {{
  const issues=aggList(), names=issues.map(x=>x.name);
  const dimLabel = {{flow:'流程', component:'组件', fault:'故障模式'}}[aggKey()] || '';
  if (!browseBar) browseBar=echarts.init(document.getElementById('chart-browse-bar'));
  if (!browsePie) browsePie=echarts.init(document.getElementById('chart-browse-pie'));
  const barSeries = issues.map((x, idx) => ({{
    name: x.name,
    type: 'bar',
    data: names.map((n, i) => (i === idx ? x.issues : null)),
    itemStyle: {{ color: colorForCat(x.name) }},
    emphasis: {{ focus: 'series' }}
  }}));
  browseBar.setOption({{
    title: {{ text: dimLabel + ' · Issue 汇总', left: 'center', textStyle: {{ fontSize: 13 }} }},
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
    legend: {{ top: 28, type: 'scroll', data: names }},
    grid: {{ left: 90, right: 12, top: 72, bottom: 20 }},
    xAxis: {{ type: 'value' }},
    yAxis: {{ type: 'category', data: names, axisLabel: {{ fontSize: 10 }} }},
    series: barSeries
  }}, true);
  browseBar.off('click'); browseBar.on('click', p => {{ if(p.name){{ document.getElementById('dim-value').value=p.name; renderBrowse(); }} }});
  const val=document.getElementById('dim-value').value;
  const pie=(val?issues.filter(x=>x.name===val):issues).slice(0,12);
  browsePie.setOption({{
    title: {{ text: dimLabel + ' · 占比', left: 'center', textStyle: {{ fontSize: 13 }} }},
    tooltip: {{ trigger: 'item' }},
    legend: {{ type: 'scroll', orient: 'vertical', right: 0, top: 'middle', textStyle: {{ fontSize: 10 }} }},
    series: [{{ type: 'pie', radius: ['38%','62%'], center: ['40%','55%'],
      data: pie.map(x => ({{ name: x.name, value: x.issues, itemStyle: {{ color: colorForCat(x.name) }} }}))
    }}]
  }}, true);
}}
function renderBrowse() {{ browsePager.page=1; fillDimOptions(); renderBrowseCharts(); renderBrowseTable(); }}
document.getElementById('dim-type').onchange=()=>{{document.getElementById('dim-value').value='';browsePager.page=1;renderBrowse();}};
['dim-value','data-type','fix-kind-filter','pr-author-filter','issue-reporter-filter'].forEach(id=>document.getElementById(id).onchange=()=>{{browsePager.page=1;renderBrowse();}});
document.getElementById('kw-filter').oninput=()=>{{browsePager.page=1;renderBrowseTable();}};
renderBrowse();

const fkPieData = (FIX_SCOPE.order || []).map(k => ({{
  name: (FIX_SCOPE.labels||{{}})[k] || k,
  value: (FIX_SCOPE.counts||{{}})[k] || 0,
  itemStyle: {{ color: fixKindColors[k] || '#999' }}
}})).filter(d => d.value > 0);
const fkLegend = fkPieData.map(d => d.name);
echarts.init(document.getElementById('chart-fix-kind-pie')).setOption({{
  title: {{ text: 'Fix PR 修复类型分布', left: 'center', textStyle: {{ fontSize: 13 }} }},
  tooltip: {{ trigger: 'item' }},
  legend: {{ top: 28, type: 'scroll', data: fkLegend }},
  series: [{{ type: 'pie', radius: ['42%','68%'], center: ['50%','58%'], data: fkPieData }}]
}});
const fkTrendSeries = (FIX_SCOPE.order || []).filter(k => ((FIX_SCOPE.counts||{{}})[k]||0) > 0).map(k => lineSeries(
  (FIX_SCOPE.labels||{{}})[k] || k,
  (FIX_SCOPE.monthly||{{}})[k] || [],
  fixKindColors[k] || '#999'
));
const fkTrendLegend = fkTrendSeries.map(s => s.name);
echarts.init(document.getElementById('chart-fix-kind-trend')).setOption({{
  title: {{ text: '修复类型 · 月度趋势', left: 'center', textStyle: {{ fontSize: 13 }} }},
  tooltip: {{ trigger: 'axis' }},
  legend: {{ top: 28, type: 'scroll', data: fkTrendLegend }},
  grid: {{ top: 72, left: 48, right: 16, bottom: 28 }},
  xAxis: {{ type: 'category', data: TREND.months }},
  yAxis: {{ type: 'value', minInterval: 1 }},
  series: fkTrendSeries
}});

window.addEventListener('resize',()=>{{ document.querySelectorAll('.chart').forEach(el=>{{const c=echarts.getInstanceByDom(el);if(c)c.resize();}}); }});
</script>
</body>
</html>"""

def main() -> None:
    print("Loading FEMA...")
    fema_cases = load_fema_csv()
    existing = {normalize(c.mode) for c in fema_cases}
    fema_cases.extend(load_fema_md(existing))

    print("Fetching GitCode issues...")
    raw_issues = fetch_paginated("/issues", {"state": "all"})
    issues: list[IssueItem] = []
    for ri in raw_issues:
        labels = [l.get("name", "") if isinstance(l, dict) else str(l) for l in (ri.get("labels") or [])]
        title = ri.get("title", "") or ""
        body = ri.get("body", "") or ""
        is_bug = "bug" in [x.lower() for x in labels] or "[bug]" in title.lower()
        cat = classify_text(f"{title} {body}")
        prs, _ = extract_refs(f"{title}\n{body}")
        rep = extract_issue_reporter(ri)
        issues.append(
            IssueItem(
                number=int(ri["number"]),
                title=title,
                state=ri.get("state", "open"),
                labels=labels,
                body=body,
                html_url=ri.get("html_url", ""),
                category=cat,
                created_at=ri.get("created_at", "") or "",
                updated_at=ri.get("updated_at", "") or "",
                closed_at=ri.get("closed_at", "") or "",
                linked_prs=prs,
                reporter_login=rep["reporter_login"],
                reporter_name=rep["reporter_name"],
                reporter_label=rep["reporter_label"],
            )
        )

    print("Fetching PR metadata (sample for issue linking)...")
    pulls = fetch_paginated("/pulls", {"state": "all"})
    pr_to_issues: dict[int, list[int]] = defaultdict(list)
    pull_bodies: dict[int, str] = {}
    pull_authors = build_pull_authors(pulls)
    for p in pulls:
        num = int(p["number"])
        pull_bodies[num] = (p.get("body") or "") + "\n" + (p.get("title") or "")
        _, iss = extract_refs(pull_bodies[num])
        for i in iss:
            pr_to_issues[i].append(num)

    for issue in issues:
        issue.linked_prs = sorted(set(issue.linked_prs + pr_to_issues.get(issue.number, [])))

    print("Reading git PR commits...")
    pr_map = git_pr_commits()
    pull_titles = {int(p["number"]): (p.get("title") or "") for p in pulls}

    def pr_text(pr_num: int) -> str:
        title = pull_titles.get(pr_num, "")
        subjects = " ".join(pr_map.get(pr_num, {}).get("subjects", []))
        return f"{title} {subjects}".lower()

    def prs_for_fema(fema: FemaCase) -> list[int]:
        matched: list[tuple[int, int]] = []
        for pr_num in pr_map:
            text = pr_text(pr_num)
            if not any(k in text for k in ("fix", "bugfix", "修复", "bug")):
                continue
            score = 0
            if classify_text(text) == fema.category:
                score += 2
            for kw in fema.keywords:
                if len(kw) >= 2 and kw.lower() in text:
                    score += 2
            for token in re.findall(r"[\u4e00-\u9fff]{2,}", fema.mode):
                if token in text:
                    score += 3
            if score >= 3:
                matched.append((score, pr_num))
        matched.sort(reverse=True)
        return [p for _, p in matched[:5]]

    bug_issues = [i for i in issues if "bug" in [x.lower() for x in i.labels] or "[bug]" in i.title.lower()]

    for issue in issues:
        best: list[tuple[int, str]] = []
        for fema in fema_cases:
            s = score_fema_match(issue, fema)
            if s >= 4:
                best.append((s, fema.id))
        best.sort(reverse=True)
        issue.linked_fema = [x[1] for x in best[:3]]
        issue.fix_status = resolve_status(issue, pr_map)

    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"bugs": 0, "open": 0, "fema": 0, "prs": 0})
    fema_by_cat: dict[str, list[FemaCase]] = defaultdict(list)
    for f in fema_cases:
        fema_by_cat[f.category].append(f)
    for bi in bug_issues:
        categories[bi.category]["bugs"] += 1
        if bi.state == "open":
            categories[bi.category]["open"] += 1
        categories[bi.category]["prs"] += len([p for p in bi.linked_prs if p in pr_map])
    for cat, flist in fema_by_cat.items():
        categories[cat]["fema"] = len(flist)

    issue_by_fema: dict[str, list[IssueItem]] = defaultdict(list)
    for issue in issues:
        for fid in issue.linked_fema:
            issue_by_fema[fid].append(issue)

    matrix = []
    covered = 0
    gaps = 0
    for fema in fema_cases:
        related = issue_by_fema.get(fema.id, [])
        prs = sorted(set([p for i in related for p in i.linked_prs if p in pr_map] + prs_for_fema(fema)))
        open_related = [i for i in related if i.state == "open"]
        if prs and not open_related:
            status, label = "covered", "已修复"
            covered += 1
        elif prs and open_related:
            status, label = "partial", "部分修复"
        elif prs:
            status, label = "covered", "有PR证据"
            covered += 1
        elif related and not prs:
            status, label = "open", "有Issue无PR"
            gaps += 1
        else:
            imp = fema.improvement or ""
            if "P0" in imp or "P1" in imp or "busy loop" in fema.mode or "etcd" in fema.mode:
                status, label = "gap", "FMEA缺口"
                gaps += 1
            else:
                status, label = "gap", "待观察"
        matrix.append(
            {
                "id": fema.id,
                "mode": fema.mode,
                "category": fema.category,
                "severity": fema.severity,
                "issues": [i.number for i in related[:5]],
                "prs": prs[:5],
                "status": status,
                "status_label": label,
            }
        )
    matrix.sort(key=lambda x: ({"gap": 0, "open": 1, "partial": 2, "covered": 3}[x["status"]], x["id"]))

    open_bugs = []
    for bi in sorted(bug_issues, key=lambda x: x.number, reverse=True):
        if bi.state != "open":
            continue
        open_bugs.append(
            {
                "number": bi.number,
                "title": bi.title,
                "category": bi.category,
                "url": bi.html_url,
                "prs": [p for p in bi.linked_prs if p in pr_map],
                "fema": bi.linked_fema,
                "status": bi.fix_status,
            }
        )

    p0_gaps = []
    for fema in fema_cases:
        imp = fema.improvement or ""
        if not ("P0" in imp or "P1" in imp or "busy loop" in fema.mode or "挂死" in fema.mode or "etcd故障自愈" in imp):
            continue
        related = issue_by_fema.get(fema.id, [])
        prs = set([p for i in related for p in i.linked_prs if p in pr_map] + prs_for_fema(fema))
        if not prs:
            p0_gaps.append(
                {
                    "id": fema.id,
                    "mode": fema.mode,
                    "improvement": imp or "见 fema-analysis-filled 改进建议汇总",
                    "category": fema.category,
                }
            )

    partial = []
    for bi in bug_issues:
        if bi.state == "open" and any(p in pr_map for p in bi.linked_prs):
            partial.append(
                {
                    "number": bi.number,
                    "title": bi.title,
                    "url": bi.html_url,
                    "prs": [p for p in bi.linked_prs if p in pr_map],
                    "reporter_login": bi.reporter_login,
                    "reporter_label": bi.reporter_label,
                }
            )

    trends = compute_trends(bug_issues, pr_map, pull_titles)

    print("Collecting all fix PRs...")
    all_fix_prs = compute_all_fix_prs(pr_map, pull_titles, pull_bodies, pr_to_issues, pull_authors)

    print("Building browser payload...")
    browser = compute_browser_data(bug_issues, all_fix_prs, pr_map)
    dim_summary = compute_dim_summary(bug_issues, all_fix_prs)

    print("Running dual-plane CodeGraph review...")
    dual_plane = compute_dual_plane_analysis(bug_issues, all_fix_prs, dict(categories))

    print("Building issue↔PR links and knowledge graph...")
    links = build_issue_pr_links(
        bug_issues, all_fix_prs, pull_bodies, pull_titles, pr_to_issues, pr_map
    )
    knowledge_graph = compute_knowledge_graph(
        bug_issues, all_fix_prs, links, fema_cases, issue_by_fema
    )

    data = {
        "generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "stats": {
            "fema_total": len(fema_cases),
            "fema_final": sum(1 for f in fema_cases if f.source == "final"),
            "fema_fill": sum(1 for f in fema_cases if f.source == "filled"),
            "issues_total": len(issues),
            "open_bugs": len(open_bugs),
            "merged_prs": len(pr_map),
            "covered": covered,
            "gaps": gaps,
        },
        "categories": dict(categories),
        "matrix": matrix,
        "open_bugs": open_bugs,
        "p0_gaps": p0_gaps,
        "unresolved": {"partial": partial},
        "trends": trends,
        "all_fix_prs": all_fix_prs,
        "dual_plane": dual_plane,
        "browser": browser,
        "dim_summary": dim_summary,
        "knowledge_graph": knowledge_graph,
        "bug_issues": bug_issues,
        "pr_map": pr_map,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_report(data), encoding="utf-8")
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size // 1024} KB)")
    print(json.dumps(data["stats"], indent=2))


if __name__ == "__main__":
    main()
