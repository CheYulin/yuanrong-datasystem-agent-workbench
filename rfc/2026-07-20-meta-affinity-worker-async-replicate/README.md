# Meta-Affinity Worker Async Replicate — 剩余工作量与方案

**Status**: Draft  
**仓库**: yuanrong-datasystem  
**基线**: `main/master` @ `8e09318ef`（2026-07-20）  
**实现分支**: `feature/meta-affinity-write` @ `3f4a93c9`  
**MR**: [!1151](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1151)  
**关联 RFC**: [2026-06-29-meta-affinity-write](../2026-06-29-meta-affinity-write/README.md)  
**Started**: 2026-07-20

## 目标

在 **最新 `main/master`** 上，用 CodeGraph CLI 量化 **Worker 侧 async replicate**（同节点 Publish 后异步 colocate primary 到 meta owner）的完成度与剩余代码工作量，并给出可落地的 Phase 1.5 方案。

> 本 RFC **仅聚焦 Worker async replicate**；Client 直写 meta owner、ring source 统一、Get RPC perf 等见父 RFC。

## 结论摘要

| 维度 | 状态 |
|------|------|
| **master 主干** | **0 行** meta-affinity 代码（功能未合入） |
| **feature 分支 Phase 1** | **核心链路已实现**（~505 LOC 生产 + ~103 LOC UT + ~111 LOC ST） |
| **binary Publish 主路径** | Publish → PublishObject → ScheduleMetaAffinityReplicate → DataMigrator → ReplacePrimary **已贯通** |
| **距「binary 路径生产可用」** | **~3–5 人日**（MR 合入 + 可观测/关停/失败策略） |
| **距「全 DataFormat 覆盖」** | **+3–5 人日**（HETERO 等 + ST） |

## 文档

| 文档 | 说明 |
|------|------|
| [design.md](./design.md) | **模块设计**：As-Is（master）/ To-Be（合入后）、组件职责、挂接点 |
| [codegraph-analysis.md](./codegraph-analysis.md) | CodeGraph CLI 证据：调用链、影响面、master vs feature diff |
| [work-breakdown.md](./work-breakdown.md) | **WBS + 人日估算**、优先级、验收标准 |
| [as-is-to-be-sequences.md](./as-is-to-be-sequences.md) | 同节点 async replicate 时序（含失败/重试 To-Be） |

## Worktree / CodeGraph

```bash
# 基线（已创建）
git -C yuanrong-datasystem fetch main master
git -C yuanrong-datasystem worktree list | rg main-master-latest

# CodeGraph（main/master @ 8e09318ef）
codegraph init  yuanrong-datasystem/.worktrees/main-master-latest
codegraph index yuanrong-datasystem/.worktrees/main-master-latest

# 实现对照（feature/meta-affinity-write）
codegraph index yuanrong-datasystem/.worktrees/meta-affinity-write
codegraph callees ExecuteMetaAffinityReplicate --path yuanrong-datasystem/.worktrees/meta-affinity-write
```

## 阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | binary Publish async replicate 核心（Manager/Executor/Publish 挂接） | **Done**（feature 分支） |
| P0.5 | MR !1151 合入 master + CI 绿 | **Pending** |
| P1 | 生产化：metric、graceful stop、失败可观测 | **Draft** |
| P2 | 非 BINARY（HETERO）挂接 + ST | **Deferred** ([#9](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/9)) |
