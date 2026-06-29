# URMA Fake Backend

**Status**: In-Progress  
**仓库**: yuanrong-datasystem  
**PR**: [openeuler/yuanrong-datasystem#1129](https://gitcode.com/openeuler/yuanrong-datasystem/pull/1129)  
**分支**: `feat/urma-fake-r11-rebase`  
**Started**: 2026-06-29

## 基线

| 项 | 内容 |
|----|------|
| Source worktree | `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr-1129-urma-fake` |
| Source HEAD | `0899800baff320ea2849799aae37352c6442f07f` |
| 远端源码目录 | `/home/ds-pr1129-urma-fake-e03ccb54` |
| 远端构建目录 | `/home/ds-pr1129-urma-fake-e03ccb54-build` |
| 远端输出目录 | `/home/ds-pr1129-urma-fake-e03ccb54-output` |
| 参考网页 | [URMA Fake Backend 开发者学习指南](http://150.242.244.2/urma-fake-developer-guide-20260628.html) |
| PR 内设计记录 | `docs/source_zh_cn/design_document/urma_fake_validation_20260628.md` |

## 目标

1. **无 RNIC 环境验证 URMA 语义**：用共享内存和 UDS 模拟本节点内的远端 DMA 行为，使 Object/KV URMA ST/UT 能在 tiantiyun 和本地沙箱运行。
2. **业务路径尽量无感**：fake 行为收敛在 dlopen/ABI/fake backend 边界，业务逻辑继续以 `USE_URMA` 为主，避免把 fake 特化扩散到 Object/KV 代码。
3. **覆盖失败注入与 fallback**：支持 CQE error、async event、queue full、wait timeout、worker reconnect、fallback limiter 等路径，验证 URMA 失败后的 TCP fallback 语义。
4. **保留真实 URMA 约束**：fake 只验证语义正确性，不宣称性能等价；2 GiB、completion byte count、JFC/Jetty 生命周期等约束按真实路径解释。

## 文档

| 文档 | 说明 |
|------|------|
| [design.md](./design.md) | 主设计：背景、原则、As-Is/To-Be、模块边界、风险与非目标 |
| [as-is-to-be-sequences.md](./as-is-to-be-sequences.md) | 时序：dlopen、Register/Import、UDS fd transfer、PostSendWr、fallback |
| [issue-rfc.md](./issue-rfc.md) | 可直接转 issue 的 RFC 文案 |
| [pr-description.md](./pr-description.md) | PR1129 描述草稿与验证摘要 |
| [worktree-verify.md](./worktree-verify.md) | 本地/tiantiyun worktree、构建、UT/ST 验证命令 |
| [results.md](./results.md) | 当前回归结果与后续补充项 |

## 阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | RFC + 设计材料归档 | In-Progress |
| P1 | fake ABI/dlopen 后端接入 | Done |
| P2 | memfd + UDS import/export 数据面 | Done |
| P3 | CQE/AE/error injection/fallback 覆盖 | Done |
| P4 | Object/KV URMA ST + UT 远端验证 | Done |
| P5 | Codecheck/Review 收敛与门禁跟踪 | In-Progress |

## 当前验证快照

| 类别 | 结果 |
|------|------|
| `common_urma_fake` 构建 | PASS |
| `ds_ut datasystem_worker_bin` 构建 | PASS |
| URMA UT sweep | 75/75 PASS |
| Object URMA ST sweep | 68/68 PASS |
| KV URMA ST sweep | 11/11 PASS |

详见 [results.md](./results.md)。
