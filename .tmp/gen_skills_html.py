#!/usr/bin/env python3
"""Generate skill intro HTML pages for yche.me (research/)."""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "skills-html"
OUT.mkdir(exist_ok=True)

HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="/assets/css/site.css">
    <style>
      .skill-badge {{ display:inline-block; padding:0.15rem 0.55rem; border-radius:4px; font-size:0.75rem; font-weight:600; margin-right:0.4rem; }}
      .wb {{ background:#dbeafe; color:#1e40af; }}
      .ds {{ background:#d1fae5; color:#065f46; }}
      .meta {{ color:#656d76; font-size:0.9rem; margin-bottom:1.2rem; }}
      pre {{ background:#f6f8fa; padding:0.75rem 1rem; border-radius:6px; overflow-x:auto; font-size:0.85rem; }}
      table {{ width:100%; border-collapse:collapse; margin:0.8rem 0; font-size:0.9rem; }}
      th, td {{ border:1px solid #d0d7de; padding:0.45rem 0.65rem; text-align:left; }}
      th {{ background:#f6f8fa; }}
      .nav-back {{ margin-bottom:1rem; font-size:0.9rem; }}
    </style>
</head>
<body>
<div class="content-area">
"""

FOOT = """
</div>
<script src="/assets/js/site.js"></script>
</body>
</html>
"""

HUB = "skills-catalog-overview-20260619.html"

SKILLS: list[dict] = [
    {
        "id": "wb-verify",
        "repo": "wb",
        "file": "skill-wb-verify-20260619.html",
        "title": "wb-verify · 远端验证与测试门禁",
        "summary": "在 tiantiyun 跑 smoke/UT/ST 及 KV executor、URMA 日志等专项门禁。",
        "when": "改代码后声称「已验证」、合入前跑 harness、匹配 verify_matrix 最低级别。",
        "triggers": "跑 smoke、远端验证、validate_kv_executor、URMA 日志校验",
        "node": "tiantiyun-80c128g",
        "tdd": "5/5 contract tests · 2026-06-19",
        "commands": [
            ("Smoke", "bash scripts/testing/verify/smoke/run_smoke_remote.sh"),
            ("UT", "bash scripts/testing/verify/ut/run_ut_remote.sh"),
            ("ST", "bash scripts/testing/verify/st/run_st_remote.sh"),
            ("KV executor", "bash scripts/testing/verify/validate_kv_executor.sh --skip-build <build>"),
            ("URMA/TCP 日志", "bash scripts/testing/verify/validate_urma_tcp_observability_logs.sh <log_dir>"),
        ],
        "workflow": [
            "rsync/build datasystem 到 tiantiyun（如需）",
            "按 verify_matrix.yaml 选 minimum 级别",
            "记录命令、节点、退出码与产物路径",
            "xqyun 仅用于 sync/HTML，非默认 verify 节点",
        ],
        "related": [("wb-perf-research", "探索 perf，非 gate"), ("ds-dev-loop", "产品仓合并目标（后续）")],
        "source": "yuanrong-datasystem-agent-workbench/.cursor/skills/wb-verify/",
    },
    {
        "id": "wb-perf-research",
        "repo": "wb",
        "file": "skill-wb-perf-research-20260619.html",
        "title": "wb-perf-research · 探索性性能研究",
        "summary": "锁竞争 baseline、executor 曲线、bpftrace、URMA 索引刷新。",
        "when": "单次 perf 实验、对比两次 baseline、定位锁/executor 开销。",
        "triggers": "锁 baseline、executor perf、bpftrace、perf 对比",
        "node": "tiantiyun-80c128g",
        "tdd": "5/5 contract tests · 2026-06-19",
        "commands": [
            ("锁竞争 ST", "bash scripts/analysis/perf/run_kv_concurrent_lock_perf.sh"),
            ("Baseline 采集", "bash scripts/analysis/perf/collect_client_lock_baseline.sh"),
            ("Baseline 对比", "bash scripts/analysis/perf/compare_client_lock_baseline.sh RUN_A RUN_B"),
            ("Executor 曲线", "python3 scripts/analysis/perf/kv_executor_perf_analysis.py --build-dir <build>"),
            ("bpftrace 工作流", "bash scripts/analysis/perf/run_kv_lock_ebpf_workflow.sh"),
            ("URMA 索引", "python3 scripts/development/code-index/refresh_urma_index_db.py"),
        ],
        "workflow": [
            "确认 tiantiyun 上已有 build",
            "跑脚本，产物放 results/（gitignore）",
            "报告 baseline 目录与 improved/regressed/flat 结论",
            "≥3 次固定 SOP 后可提议合并进 datasystem .skills",
        ],
        "related": [("wb-verify", "默认 merge gate"), ("rdma-ucx-perf-debug", "RDMA 专精诊断")],
        "source": "yuanrong-datasystem-agent-workbench/.cursor/skills/wb-perf-research/",
    },
    {
        "id": "wb-html-publish",
        "repo": "wb",
        "file": "skill-wb-html-publish-20260619.html",
        "title": "wb-html-publish · yche.me 结论页发布",
        "summary": "通过 xqyun /var/www/html git 发布结论级 HTML，禁止本地 htmls/ 副本。",
        "when": "竞品分析、验证报告、可检索结论页；注册 sidebarPages。",
        "triggers": "发 yche.me、发布 HTML、注册门户、竞品页",
        "node": "xqyun-32c32g → /var/www/html",
        "tdd": "3/3 contract tests · 2026-06-19",
        "commands": [
            ("git pull/status/push", "bash scripts/development/sync/publish_htmls_git.sh pull|status|push"),
            ("远端编辑", "ssh xqyun-32c32g 'cd /var/www/html && …'"),
            ("HTTP 验证", 'curl -sI "https://yche.me/<path>.html" | head -1'),
        ],
        "workflow": [
            "读远端 CLAUDE.md + assets/template.html",
            "kebab-case 文件名 + 日期，写入对应分类目录",
            "index.html sidebarPages 注册",
            "commit + push；代码主张标 CG-OK / CG-STALE / NARRATIVE",
        ],
        "related": [("wb-docs", "Markdown/脚本产物"), ("ds-log-analysis", "日志 HTML 报告（产品仓）")],
        "source": "yuanrong-datasystem-agent-workbench/.cursor/skills/wb-html-publish/",
    },
    {
        "id": "wb-docs",
        "repo": "wb",
        "file": "skill-wb-docs-20260619.html",
        "title": "wb-docs · 报告与工作簿交付",
        "summary": "从日志生成 perf Markdown、Bugfix↔FEMA 覆盖 HTML、commit 草稿。",
        "when": "需要可复现表格/报告，而非单次对话摘要。",
        "triggers": "gen_kv_perf_report、FEMA 覆盖、commit message 草稿、workbook",
        "node": "本地或 CI",
        "tdd": "3/3 contract tests · 2026-06-19",
        "commands": [
            ("KV perf 报告", "python3 scripts/metrics/gen_kv_perf_report.py <worker.log> …"),
            ("Bugfix FEMA HTML", "python3 scripts/analysis/generate_bugfix_fema_report.py"),
            ("Commit 草稿", "bash scripts/development/git/generate_commit_message.sh"),
        ],
        "workflow": [
            "读 docs/observable/workbook/README.md 假设",
            "跑脚本，输出路径写入 RFC/报告",
            "结论级 HTML → wb-html-publish",
        ],
        "related": [("wb-html-publish", "yche.me 发布"), ("ds-log-analysis", "KV 日志 HTML")],
        "source": "yuanrong-datasystem-agent-workbench/.cursor/skills/wb-docs/",
    },
    {
        "id": "ds-dev-loop",
        "repo": "ds",
        "file": "skill-ds-dev-loop-20260619.html",
        "title": "ds-dev-loop · 产品开发闭环",
        "summary": "matrix 驱动 rsync/build/verify + 完成前自检；合并原 ds-self-verify。",
        "when": "改 datasystem 源码、commit/PR 前、跑 tiantiyun 验证。",
        "triggers": "跑 smoke、远端验证、build and verify、完成前自检、self verify",
        "node": "tiantiyun-80c128g（verify）；xqyun（sync/HTML）",
        "tdd": "datasystem .skills/ds-dev-loop/tests",
        "commands": [
            ("矩阵", "workbench scripts/harness/verify_matrix.yaml"),
            ("自检", ".repo_context/playbooks/upkeep/ai-self-verification.md"),
            ("Sync", "workbench scripts/build/rsync_datasystem_remote_bazel.sh"),
        ],
        "workflow": [
            "git diff 匹配 change_types",
            "sync + build + 跑 minimum/recommended 级别",
            "自检 playbook 通过后再声称完成",
        ],
        "related": [("wb-verify", "workbench 侧同名脚本执行"), ("ds-pr-flow", "PR 前 review/create")],
        "source": "yuanrong-datasystem/.skills/ds-dev-loop/",
    },
    {
        "id": "ds-infra-engineering",
        "repo": "ds",
        "file": "skill-ds-infra-engineering-20260619.html",
        "title": "ds-infra-engineering · 基础设施工程门禁",
        "summary": "实现/调试/重构/设计问答走 .repo_context 工程原则与专题 playbook。",
        "when": "动 worker/client/master/common、并发/恢复/性能敏感路径。",
        "triggers": "实现 datasystem、重构、infra engineering、性能/并发改动",
        "node": "源码仓 + .repo_context",
        "tdd": "datasystem .skills/ds-infra-engineering/tests",
        "commands": [
            ("原则", ".repo_context/modules/overview/engineering-principles.md"),
            ("工作流", ".repo_context/playbooks/features/infra-engineering-workflow.md"),
        ],
        "workflow": [
            "读 AGENTS.md → .repo_context/index.md",
            "分类路径：前台/元数据/传输/后台/测试/文档",
            "选最小相关 module doc + playbook",
        ],
        "related": [("rdma-ucx-perf-debug", "RDMA perf 专精"), ("ds-dev-loop", "改完验证")],
        "source": "yuanrong-datasystem/.skills/ds-infra-engineering/",
    },
    {
        "id": "ds-pr-flow",
        "repo": "ds",
        "file": "skill-ds-pr-flow-20260619.html",
        "title": "ds-pr-flow · PR 评审与创建",
        "summary": "合并 ds-pr-review + ds-create-pr：GitCode 评审发布与模板合规开 PR。",
        "when": "review diff/PR、创建 GitCode MR/PR。",
        "triggers": "review、代码评审、创建PR、开PR、create pull request",
        "node": "GitCode API + 本地脚本",
        "tdd": "datasystem .skills/ds-pr-flow/tests",
        "commands": [
            ("Review prepare", "python3 .skills/ds-pr-review/scripts/review_pr.py prepare <PR>"),
            ("Review publish", "python3 .skills/ds-pr-review/scripts/review_pr.py publish …"),
            ("Create PR", "python3 .skills/ds-create-pr/scripts/create_pr.py …"),
        ],
        "workflow": [
            "Review：prepare → 人工/Agent 审 bundle → publish findings",
            "Create：模板 body 校验 → GitCode OpenAPI",
        ],
        "related": [("ds-dev-loop", "PR 前 verify"), ("ds-refresh-docs", "文档 PR 自动开")],
        "source": "yuanrong-datasystem/.skills/ds-pr-flow/",
    },
    {
        "id": "ds-log-analysis",
        "repo": "ds",
        "file": "skill-ds-log-analysis-20260619.html",
        "title": "ds-log-analysis · KVCache 日志 HTML 报告",
        "summary": "Access log（QPS/延迟/错误）与 Worker resource log 交互式 ECharts 报告。",
        "when": "现场日志定界、趋势分析、生成 HTML 报告。",
        "triggers": "日志分析、access log、resource log、KVCache report、P99",
        "node": "本地 python3",
        "tdd": "datasystem .skills/ds-log-analysis/tests",
        "commands": [
            ("Access log", "python3 scripts/generate_access_log_report.py <log_dir> …"),
            ("Resource log", "python3 scripts/generate_resource_report.py <log_dir> …"),
        ],
        "workflow": [
            "按场景选 access vs resource 脚本",
            "输出自包含 HTML（ECharts）",
            "结论页可再经 wb-html-publish 挂到 yche.me",
        ],
        "related": [("wb-docs", "perf Markdown"), ("wb-html-publish", "站点发布")],
        "source": "yuanrong-datasystem/.skills/ds-log-analysis/",
    },
    {
        "id": "rdma-ucx-perf-debug",
        "repo": "ds",
        "file": "skill-rdma-ucx-perf-debug-20260619.html",
        "title": "rdma-ucx-perf-debug · RDMA/UCX 性能诊断",
        "summary": "分层 perf 指标、BatchGet 路径、flush/submit、UCX 生命周期 teardown SOP。",
        "when": "RDMA 带宽/延迟异常、UCP flush、BatchGet 慢、UCX crash。",
        "triggers": "RDMA 性能、UCX 延迟、UCP flush、BatchGet 远端拉取",
        "node": "测试机 + worker 日志",
        "tdd": "datasystem .skills/rdma-ucx-perf-debug/tests",
        "commands": [
            ("Perf keys", "src/datasystem/common/perf/perf_point.def"),
            ("搜索", 'rg "perfkey::rdma|ucp_put_nbx" src tests'),
        ],
        "workflow": [
            "确认 workload shape 与 benchmark 有效性",
            "读 rdma/上层 perf 指标块",
            "分层判断瓶颈；必要时 worker scaling 对比",
            "清理临时指标，保留 UT",
        ],
        "related": [("wb-perf-research", "通用 perf 脚本"), ("ds-infra-engineering", "代码改动门禁")],
        "source": "yuanrong-datasystem/.skills/rdma-ucx-perf-debug/",
    },
    {
        "id": "ds-refresh-docs",
        "repo": "ds",
        "file": "skill-ds-refresh-docs-20260619.html",
        "title": "ds-refresh-docs · 在线中文文档刷新",
        "summary": "从 upstream master 构建 zh-cn 文档，同步 doc_pages 并自动开 GitCode PR。",
        "when": "更新/刷新/发布 openYuanrong 在线中文文档。",
        "triggers": "更新在线文档、refresh online docs、doc_pages",
        "node": "本地 + GitCode",
        "tdd": "datasystem .skills/ds-refresh-docs/tests",
        "commands": [
            ("一键刷新", "python3 .skills/ds-refresh-docs/scripts/refresh_online_docs.py"),
        ],
        "workflow": [
            "fetch upstream master → 构建 build_zh_cn",
            "rsync 到 doc_pages worktree",
            "push 分支 + ds-create-pr 开 PR",
        ],
        "related": [("ds-pr-flow", "PR 创建"), ("wb-html-publish", "yche.me 与官方 doc 分工")],
        "source": "yuanrong-datasystem/.skills/ds-refresh-docs/",
    },
]


def badge(repo: str) -> str:
    if repo == "wb":
        return '<span class="skill-badge wb">Workbench</span>'
    return '<span class="skill-badge ds">Datasystem</span>'


def render_skill(s: dict) -> str:
    cmd_rows = "".join(f"<tr><td>{a}</td><td><code>{c}</code></td></tr>" for a, c in s["commands"])
    wf = "".join(f"<li>{w}</li>" for w in s["workflow"])
    rel = "".join(
        f'<li><a href="/research/{next(x["file"] for x in SKILLS if x["id"]==rid)}">{rid}</a> — {note}</li>'
        if any(x["id"] == rid for x in SKILLS)
        else f"<li>{rid} — {note}</li>"
        for rid, note in s["related"]
    )
    return f"""{HEAD.format(title=s["title"])}
<p class="nav-back"><a href="/research/{HUB}">← Skills 全览</a></p>
<h1>{s["title"]}</h1>
<p class="meta">{badge(s["repo"])} <strong>Skill ID</strong> <code>{s["id"]}</code> · <strong>源文件</strong> <code>{s["source"]}</code> · <strong>TDD</strong> {s["tdd"]}</p>
<p>{s["summary"]}</p>

<h2>何时使用</h2>
<p>{s["when"]}</p>
<p><strong>典型触发语：</strong>{s["triggers"]}</p>
<p><strong>默认节点/环境：</strong>{s["node"]}</p>

<h2>命令与脚本</h2>
<table><thead><tr><th>用途</th><th>命令</th></tr></thead><tbody>{cmd_rows}</tbody></table>

<h2>工作流</h2>
<ol>{wf}</ol>

<h2>相关 Skill</h2>
<ul>{rel}</ul>
{FOOT}"""


def render_hub() -> str:
    wb = [s for s in SKILLS if s["repo"] == "wb"]
    ds = [s for s in SKILLS if s["repo"] == "ds"]
    def cards(items: list[dict]) -> str:
        return "".join(
            f"""<div style="margin:0.8rem 0;padding:0.9rem 1rem;border:1px solid #d0d7de;border-radius:8px;">
<h3 style="margin-top:0;"><a href="/research/{s["file"]}">{s["id"]}</a></h3>
<p>{s["summary"]}</p>
<p style="font-size:0.85rem;color:#656d76;">{s["when"][:80]}…</p>
</div>"""
            for s in items
        )
    return f"""{HEAD.format(title="Agent Skills 全览 · Workbench + Datasystem")}
<h1>Agent Skills 全览</h1>
<p class="meta"><strong>日期</strong> 2026-06-19 · <strong>合计</strong> 4 Workbench + 6 Datasystem canonical ·
<a href="/research/workbench-agent-skills-competitive-guide-20260619.html">竞品与选型指导</a></p>

<p>Skill 是可被 Agent 触发的<strong>可验证工作流</strong>：命令路径、节点、TDD contract 写在各仓 <code>.cursor/skills/</code> 或 <code>.skills/</code> 中。已删除 workbench <code>./ops</code>，请按各 Skill 页直接跑脚本。</p>

<h2>Workbench（验证阶段 — 先跑通再合并 DS）</h2>
{cards(wb)}

<h2>Datasystem（产品仓 canonical）</h2>
{cards(ds)}

<h2>已废弃别名（勿单独注册）</h2>
<table>
<tr><th>旧名</th><th>合并至</th></tr>
<tr><td><code>ds-self-verify</code></td><td><code>ds-dev-loop</code></td></tr>
<tr><td><code>ds-pr-review</code> / <code>ds-create-pr</code></td><td><code>ds-pr-flow</code></td></tr>
</table>

<h2>验证</h2>
<ul>
<li>Workbench: <code>bash scripts/run_skill_tests.sh</code></li>
<li>Datasystem: <code>bash scripts/run_skill_tests.sh</code>（含 workbench 测试）</li>
</ul>
{FOOT}"""


def main() -> None:
    (OUT / HUB).write_text(render_hub(), encoding="utf-8")
    for s in SKILLS:
        (OUT / s["file"]).write_text(render_skill(s), encoding="utf-8")
    print(f"Wrote {len(SKILLS)+1} files to {OUT}")


if __name__ == "__main__":
    main()
