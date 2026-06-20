# Workbench Skills (tool-neutral)

Canonical skills for **Cursor**, **Codex**, and **Claude Code**.  
Do not duplicate workflow text under `.cursor/skills/` — use redirect stubs only.

| Tool | How to load |
|------|-------------|
| **Cursor** | `.cursor/skills/*/SKILL.md` (stub → here) + `.cursor/rules/skills-entry.mdc` |
| **Codex** | `agents/openai.yaml` per skill |
| **Claude Code** | `CLAUDE.md` / `AGENTS.md` → this directory |

## Skills (6)

Engineering execution skills:

| Skill | Node | When |
|-------|------|------|
| `wb-build` | **tiantiyun** | CMake/Bazel build plus timing, module long-tail, and optimization hints |
| `wb-dev` | **tiantiyun** | Daily developer loop: format check, smoke, UT, ST, and matrix gates |
| `wb-daily` | **tiantiyun** | Full daily quality run: smoke/UT/ST, coverage, perf regression, trend evidence |
| `wb-perf` | **tiantiyun** | Hotspot and regression research with perf/bpftrace/strace/metrics inputs |

Deliverable skills:

| Skill | Node | When |
|-------|------|------|
| `wb-html-publish` | **xqyun** | yche.me HTML via `/var/www/html` git |
| `wb-docs` | **tiantiyun** | Reports, workbook, commit drafts |

Datasystem product skills: sibling `yuanrong-datasystem/.skills/` (`ds-dev-loop` extends L1–L8 + self-check).

## Verification

```bash
# tiantiyun — TDD + harness dry-run/user-path checks (wb-build / wb-dev / wb-daily / wb-perf / docs / ds-*)
bash scripts/harness/run_skill_verification_remote.sh
bash scripts/harness/run_skill_verification_remote.sh --tests-only
bash scripts/harness/run_skill_verification_remote.sh --user-only

# xqyun — wb-html-publish only
bash scripts/harness/run_skill_html_verify_remote.sh

# local (WSL) — GitCode / commit message (ds-pr-flow)
bash scripts/run_skill_local_verification.sh

# 汇总 HTML 报告（构建/测试/性能/日志分节 + 耗时与成功率）
python3 scripts/harness/generate_skill_verification_report.py
# → results/skill_verification_summary_YYYYMMDD.html
```
