# Workbench Index

当前定位与约束见 [`README.md`](README.md) 和 [`AGENTS.md`](AGENTS.md)。

## 内容入口

| 入口 | 用途 |
|------|------|
| [`rfc/README.md`](rfc/README.md) | 新问题分析、bugfix 方案、设计与验证记录；新专题统一进入这里 |
| [`docs/`](docs/) | 可复用文档与操作指南 |
| [`scripts/README.md`](scripts/README.md) | 必要的已有辅助工具 |
| [`archive/README.md`](archive/README.md) | 历史 bugfix、workspace 和提取包 |

## 共享技能

正文位于 `.skills/`，Codex 从 `.agents/skills/` 发现，Claude Code 从 `.claude/skills/` 发现。入口指向同一份技能，详见 [`.skills/README.md`](.skills/README.md)。

| Skill | 用途 |
|-------|------|
| `wb-build` | 既有 CMake/Bazel 构建与耗时分析 |
| `wb-dev` | 按风险选择 lint、smoke、UT、ST 验证 |
| `wb-daily` | 明确需要时执行既有每日全量流程 |
| `wb-perf` | 性能定位与回归分析 |
| `wb-docs` | 文档、报告与工作簿材料 |
| `wb-html-publish` | 按明确发布需求维护 yche.me HTML |

## 已有脚本入口

按任务选用，不要求每个 RFC 执行完整流程：

```bash
python3 scripts/harness/ds_harness.py build --backend cmake --dry-run --json
python3 scripts/harness/ds_harness.py dev --profile dev.quick --dry-run --json
bash scripts/harness/verify_skill.sh --all --dry-run
```

具体参数、节点与验证边界见 [`scripts/harness/README.md`](scripts/harness/README.md)、[`scripts/harness/profiles.yaml`](scripts/harness/profiles.yaml) 和 [`docs/agent/scripts-map.md`](docs/agent/scripts-map.md)。新增脚本仅在任务明确需要时加入，并同步对应技能与文档索引。
