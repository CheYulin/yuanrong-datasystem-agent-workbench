# Workbench Skills (tool-neutral)

Canonical skills for **Cursor**, **Codex**, and **Claude Code**.  
Do not duplicate workflow text under `.cursor/skills/` — use redirect stubs only.

| Tool | How to load |
|------|-------------|
| **Cursor** | `.cursor/skills/*/SKILL.md` (stub → here) + `.cursor/rules/skills-entry.mdc` |
| **Codex** | `.agents/skills/<name>` → `../../.skills/<name>`；`agents/openai.yaml` 为展示元数据 |
| **Claude Code** | `.claude/skills/<name>` → `../../.skills/<name>`；约束见 `CLAUDE.md` / `AGENTS.md` |

技能目录使用相对符号链接，正文只维护一份。Codex 用 `$wb-docs`，Claude Code 用 `/wb-docs`。Linux/WSL checkout 可直接使用；Windows 原生 checkout 需支持符号链接，否则按 `.skills/<name>/SKILL.md` 手工读取。命令从仓库根目录执行。

本仓主要承载 `docs/`、`rfc/`、必要的 `scripts/`；新专题和 bugfix 记录进入 `rfc/`，历史材料见 `archive/`。以下技能为既有工具入口，按需求使用。

规范：[Codex](https://learn.chatgpt.com/docs/build-skills)、[Claude Code](https://code.claude.com/docs/en/skills)。

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
