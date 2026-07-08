# RFC: Datasystem Skills Migration and Verification

- **Status**: Draft
- **Started**: 2026-07-09
- **Owner Repo**: `yuanrong-datasystem-agent-workbench`
- **Landing Repo**: `yuanrong-datasystem/.skills/`

---

## 目标

把 workbench 中已经验证过方向的 `wb-*` skills 迁移为 `yuanrong-datasystem` 仓内开箱即用的 `ds-*` skills，并把现有 datasystem skill 残留目录统一归类、合并或归档。

迁移完成后，datasystem 仓应提供一套开发者和 Agent 都能使用的验证面：

- 自动能跑：有 build/wheel/cache/远端配置时直接执行。
- 手工能复现：每个自动入口都有等价命令。
- 失败能定位：缺网络、缺产物、缺节点、源码失败、测试失败分类明确。
- 证据可引用：PR review、PR flow、daily/perf 报告都引用统一 evidence。

## 本目录文件

| 文件 | 说明 |
|------|------|
| [design-and-story.md](design-and-story.md) | Skill 迁移 Story、架构、现有 skill 关系、验证矩阵 |

## 迁移范围

| Workbench 能力 | Datasystem 落点 | 处理方式 |
|------|------|------|
| `wb-build` | `ds-build` | 已有雏形，补齐开箱即用和真实远端验证 |
| `wb-dev` | `ds-dev` | 已有雏形，补齐 smoke/UT/ST 前置诊断和真实验证 |
| `wb-daily` | `ds-daily` | 已有雏形，从 dry-run 推进到 full quality gate |
| `wb-perf` | `ds-perf` | 新增，吸收 bench、perf、log/rdma debug 能力 |
| `wb-docs` | `ds-docs` | 新增或补齐，生成验证报告、PR evidence、commit draft |
| `wb-html-publish` | workbench 保留 | datasystem 只提供 report handoff，不绑定私有发布站 |
| `scripts/harness/*` | `ds-harness` | 统一 profile、节点、证据、per-skill verify |

## 现有 Datasystem Skill 关系

| 目录 | 当前状态 | 目标处理 |
|------|------|------|
| `ds-harness` | Active | 统一编排底座 |
| `ds-build` | Active | 构建与构建诊断 |
| `ds-dev` | Active | PR 前开发验证闭环 |
| `ds-daily` | Active | 全量日构与质量趋势 |
| `ds-pr-review` | Promote | 补 `SKILL.md`，读取 harness evidence |
| `ds-pr-flow` | Merge target | 合并 create/comment/reply/PR description |
| `ds-create-pr` | Merge | 并入 `ds-pr-flow` |
| `ds-pr-comment-proc` | Merge | 并入 `ds-pr-flow` |
| `ds-dev-loop` | Merge | 并入 `ds-dev` profile |
| `ds-log-analysis` | Merge | 并入 `ds-perf` |
| `ds-infra-engineering` | Merge | 并入 `.repo_context` + `ds-dev` |
| `rdma-ucx-perf-debug` | Merge | 并入 `ds-perf` |
| `ds-refresh-docs` | Merge | 并入 `ds-docs` |

## 验收口径

迁移后的 skill 不能只靠 dry-run 标记 OK。每个正式 skill 至少需要：

1. Contract tests 通过。
2. `verify_skill.sh --skill <name> --dry-run` 通过。
3. 有一次真实执行结论，结论可以是 PASS，也可以是明确分类的前置条件失败。
4. 真实远端验证在私有节点 overlay 可用时执行，并产出 `summary.json`、`steps.jsonl`、相关日志。
5. `SKILL.md` 写清自动入口、手工入口、pass/fail 语义和 evidence 路径。

## 相关文档

- [`../2026-06-19-dsbench-kvtest-research/`](../2026-06-19-dsbench-kvtest-research/README.md) — bench 能力进入 `wb-perf` 的先例。
- [`../../.skills/wb-build/SKILL.md`](../../.skills/wb-build/SKILL.md)
- [`../../.skills/wb-dev/SKILL.md`](../../.skills/wb-dev/SKILL.md)
- [`../../.skills/wb-daily/SKILL.md`](../../.skills/wb-daily/SKILL.md)
- [`../../.skills/wb-perf/SKILL.md`](../../.skills/wb-perf/SKILL.md)
- [`../../extract/for-datasystem/README.md`](../../extract/for-datasystem/README.md)
