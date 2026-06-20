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

Datasystem product skills: sibling `yuanrong-datasystem/.skills/` (`ds-dev-loop` extends harness profiles + self-check).

## Verification

```bash
# tiantiyun — per-skill verify (see profiles.yaml skill_verify)
bash scripts/harness/verify_skill.sh --skill wb-build
bash scripts/harness/verify_skill.sh --all --dry-run
bash scripts/harness/run_skill_verification_remote.sh   # TDD + tiantiyun skills

# xqyun — wb-html-publish
bash scripts/harness/verify_skill.sh --skill wb-html-publish

# local (WSL) — GitCode / commit message (ds-pr-flow)
bash scripts/run_skill_local_verification.sh

# dashboard HTML
python3 scripts/harness/render_skill_dashboard.py
```
