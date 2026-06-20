#!/usr/bin/env python3
"""Render workbench skill verification dashboard from results/skill_runs/manifest.json."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path


WORKBENCH = Path(__file__).resolve().parents[2]
RUNS = WORKBENCH / "results" / "skill_runs"


def load_manifest() -> dict:
    path = RUNS / "manifest.json"
    if not path.is_file():
        return {"skills": [], "generated_at": None, "git_sha": "unknown"}
    return json.loads(path.read_text(encoding="utf-8"))


def load_skill_summary(evidence_dir: str) -> dict:
    path = WORKBENCH / evidence_dir / "summary.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def verdict_class(v: str) -> str:
    return {"PASS": "pass", "WARN": "warn", "FAIL": "fail"}.get(v, "unknown")


def render(manifest: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    pass_n = warn_n = fail_n = 0
    for entry in manifest.get("skills", []):
        v = entry.get("verdict", "UNKNOWN")
        if v == "PASS":
            pass_n += 1
        elif v == "WARN":
            warn_n += 1
        elif v == "FAIL":
            fail_n += 1
        detail = load_skill_summary(entry.get("evidence_dir", ""))
        metrics = detail.get("metrics", {})
        timing = metrics.get("step_timing", {})
        timing_txt = ", ".join(
            f"{k}={v.get('elapsed_min')}m" for k, v in sorted(timing.items())
        ) or "—"
        rows.append(
            f"<tr class='{verdict_class(v)}'>"
            f"<td>{html.escape(entry.get('skill', ''))}</td>"
            f"<td><strong>{html.escape(v)}</strong></td>"
            f"<td>{html.escape(entry.get('git_sha', ''))}</td>"
            f"<td>{html.escape(timing_txt)}</td>"
            f"<td><code>{html.escape(entry.get('evidence_dir', ''))}</code></td>"
            f"</tr>"
        )
    total = len(manifest.get("skills", []))
    rate = round(100.0 * pass_n / total, 1) if total else 0.0
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>Workbench Skill Dashboard</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;line-height:1.5}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:.5rem .75rem;text-align:left}}
.pass td:nth-child(2){{color:#0a7}}
.warn td:nth-child(2){{color:#a70}}
.fail td:nth-child(2){{color:#c33}}
.meta{{color:#555;font-size:.9rem}}
</style>
</head>
<body>
<h1>Workbench Skill Verification Dashboard</h1>
<p class="meta">生成 {now} · manifest {html.escape(str(RUNS / 'manifest.json'))}</p>
<p><strong>通过率 {rate}%</strong> — PASS {pass_n} / WARN {warn_n} / FAIL {fail_n} / 共 {total}</p>
<table>
<thead><tr><th>Skill</th><th>Verdict</th><th>Git SHA</th><th>Step timing</th><th>Evidence</th></tr></thead>
<tbody>
{''.join(rows) if rows else '<tr><td colspan="5">无 skill_runs 数据 — 先运行 verify_skill.sh</td></tr>'}
</tbody>
</table>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Output HTML path (default: results/skill_dashboard_YYYYMMDD.html)")
    parser.add_argument("--publish-copy", help="Also write copy under htmls/ops/ for yche.me")
    args = parser.parse_args()

    manifest = load_manifest()
    doc = render(manifest)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = Path(args.output) if args.output else WORKBENCH / "results" / f"skill_dashboard_{stamp}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(out)

    if args.publish_copy:
        pub = Path(args.publish_copy)
        pub.parent.mkdir(parents=True, exist_ok=True)
        pub.write_text(doc, encoding="utf-8")
        print(pub)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
