#!/usr/bin/env python3
from pathlib import Path

index = Path("/var/www/html/index.html")
text = index.read_text(encoding="utf-8")
entry = (
    '  { t:"Workbench Agent Skills 竞品与选型", '
    'p:"research/workbench-agent-skills-competitive-guide-20260619.html", '
    'd:"2026-06-19", c:"research", g:"agent", '
    'e:"wb-verify/perf/html/docs 四 Skill 验证记录 + 与 ops/通用 Skill/DS Skill 对比与选型", '
    'tg:["Agent","Skills","Workbench","竞品","验证"] },\n'
)
if "workbench-agent-skills-competitive-guide-20260619.html" in text:
    print("already registered")
else:
    needle = "research/lmcache-competitive-analysis.html"
    idx = text.find(needle)
    if idx == -1:
        raise SystemExit("needle not found")
    line_start = text.rfind("\n", 0, idx) + 1
    index.write_text(text[:line_start] + entry + text[line_start:], encoding="utf-8")
    print("registered sidebar entry")
