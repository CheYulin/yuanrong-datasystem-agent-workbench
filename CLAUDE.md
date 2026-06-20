# Claude Code Entry

This is the **yuanrong-datasystem-agent-workbench** companion repository for **yuanrong-datasystem** (Open Yuanrong DataSystem). Source code lives in the sibling repo; this repo carries scripts, docs, plans, and Agent workflows.

## Quick Start

1. Read [`AGENTS.md`](AGENTS.md) — roles, script/Skill conventions.
2. Read [`INDEX.md`](INDEX.md) — **6 workbench skills** in `.skills/` + `profiles.yaml`.
3. Read [`docs/agent/decision-tree.md`](docs/agent/decision-tree.md) — intent-based routing.
4. Read [`docs/agent/scripts-map.md`](docs/agent/scripts-map.md) — skill → script paths.
5. Read [`docs/agent/maintenance.md`](docs/agent/maintenance.md) — what to update after changes.

Canonical skills: `wb-build`, `wb-dev`, `wb-daily`, `wb-perf`, `wb-html-publish`, `wb-docs`.

Harness entry: `python3 scripts/harness/ds_harness.py {build|dev|daily|perf} --profile <id>`

## Cross-Repo Notes

- **`DATASYSTEM_ROOT`**: If the two repos are not sibling directories, set this env var to the absolute path of `yuanrong-datasystem`.
- **Source code changes**: Belong in `yuanrong-datasystem`. This repo is for scripts, docs, plans, and verification only.
- **Multi-root workspace**: Open [`datasystem-dev.code-workspace`](datasystem-dev.code-workspace) to load both repos in one Cursor/VS Code session.

## Rules

- Treat `docs/` and `scripts/` as the working memory layer; source code in `yuanrong-datasystem` is the final truth.
- After adding or changing scripts, docs, or verification flows, follow [`docs/agent/maintenance.md`](docs/agent/maintenance.md) to keep indexes in sync.
- If context in `docs/` is stale, fix it rather than working around it.
