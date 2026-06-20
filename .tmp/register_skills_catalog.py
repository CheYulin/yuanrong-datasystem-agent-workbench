#!/usr/bin/env python3
"""Register skill intro pages in xqyun index.html sidebarPages."""
from __future__ import annotations

from pathlib import Path

INDEX = Path("/var/www/html/index.html")

ENTRIES = [
    (
        "Agent Skills 全览（10 Skill 介绍）",
        "research/skills-catalog-overview-20260619.html",
        "Workbench 4 + Datasystem 6 逐 Skill 说明、命令表与导航",
        ["Agent", "Skills", "全览", "Workbench", "Datasystem"],
    ),
    ("Skill · wb-verify", "research/skill-wb-verify-20260619.html", "Smoke/UT/ST 与专项测试门禁", ["wb-verify", "验证"]),
    ("Skill · wb-perf-research", "research/skill-wb-perf-research-20260619.html", "探索性 perf 与 lock baseline", ["wb-perf-research", "perf"]),
    ("Skill · wb-html-publish", "research/skill-wb-html-publish-20260619.html", "yche.me HTML git 发布", ["wb-html-publish", "yche.me"]),
    ("Skill · wb-docs", "research/skill-wb-docs-20260619.html", "报告脚本与工作簿交付", ["wb-docs", "文档"]),
    ("Skill · ds-dev-loop", "research/skill-ds-dev-loop-20260619.html", "产品开发闭环与自检", ["ds-dev-loop", "verify"]),
    ("Skill · ds-infra-engineering", "research/skill-ds-infra-engineering-20260619.html", "基础设施工程门禁", ["ds-infra-engineering"]),
    ("Skill · ds-pr-flow", "research/skill-ds-pr-flow-20260619.html", "PR 评审与 GitCode 创建", ["ds-pr-flow", "PR"]),
    ("Skill · ds-log-analysis", "research/skill-ds-log-analysis-20260619.html", "KVCache 日志 HTML 报告", ["ds-log-analysis", "日志"]),
    ("Skill · rdma-ucx-perf-debug", "research/skill-rdma-ucx-perf-debug-20260619.html", "RDMA/UCX 性能诊断 SOP", ["RDMA", "UCX"]),
    ("Skill · ds-refresh-docs", "research/skill-ds-refresh-docs-20260619.html", "在线中文文档刷新 PR", ["ds-refresh-docs", "文档"]),
]

text = INDEX.read_text(encoding="utf-8")
block_lines = []
for title, path, desc, tags in ENTRIES:
    if path in text:
        print(f"skip exists: {path}")
        continue
    tg = ",".join(f'"{t}"' for t in tags)
    block_lines.append(
        f'  {{ t:"{title}", p:"{path}", d:"2026-06-19", c:"research", g:"skills", '
        f'e:"{desc}", tg:[{tg}] }},'
    )

if not block_lines:
    print("nothing to add")
    raise SystemExit(0)

block = "\n".join(block_lines) + "\n"
for needle in (
    "research/workbench-agent-skills-competitive-guide-20260619.html",
    "research/lmcache-competitive-analysis.html",
):
    idx = text.find(needle)
    if idx != -1:
        line_start = text.rfind("\n", 0, idx) + 1
        text = text[:line_start] + block + text[line_start:]
        INDEX.write_text(text, encoding="utf-8")
        print(f"registered {len(block_lines)} entries")
        break
else:
    raise SystemExit("insert anchor not found")
