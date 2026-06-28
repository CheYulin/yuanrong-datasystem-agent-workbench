# Client Direct Read Flow

**Status**: In-Progress  
**仓库**: yuanrong-datasystem  
**分支**: `feature/client-direct-read-flow`  
**Started**: 2026-06-21

## 目标

1. **Client 无本地 Worker 时直连读**：绕过 gateway，Client 自行查 meta、拉数据
2. **控制面逻辑复用**：Client / Worker 共享 meta phase 骨架与 redirect/moving 算法
3. **模块边界清晰**：Common 承载可证明的控制面算法；Client / Worker 只注入路由、传输、产品策略

## 文档

| 文档 | 说明 |
|------|------|
| [design.md](./design.md) | **模块设计**：现状分层、目标接口、迁移步骤（Rich MetaClient refactor 基线） |
| [urma-direct-read-design.md](./urma-direct-read-design.md) | **P4 URMA 设计**：复用 gateway URMA 栈、direct data transport、三层 fallback |
| [hash-ring-refresh-policy.md](./hash-ring-refresh-policy.md) | **HashRing 刷新**：事件驱动 vs 稳态缓存、版本协同、Client–Worker 待办 |
| [as-is-to-be-sequences.md](./as-is-to-be-sequences.md) | 时序：gate、ring refresh、moving、cutback（Done） |
| [meta-redirect-refactor-progress.md](./meta-redirect-refactor-progress.md) | redirect/moving 下沉 common 的进展与验证 |
| [verify-remote-logs.md](./verify-remote-logs.md) | 远端 tiantiyun 验证日志路径 |
| [perf-verification.md](./perf-verification.md) | **性能 A/B**：gateway vs direct，avg/p99/p99.99 |
| [results.md](./results.md) | 验证与 perf 实测记录 |

## 阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | Direct read 骨架 + ST 覆盖 | Done |
| P1 | redirect/moving → `query_meta_*_helper` | Done |
| P2 | Rich MetaClient + 单层重试 + Flow 合并统一 | **Done** |
| R2 | `GetClusterStateRspPb` 带 ring 版本；Client LoadFromWorker 使用 | **Done** |
| L1 | Direct read session `shared_ptr` + mutex；DirectReadFlow 持有 shared_ptr | **Done** |
| P3 | Worker HashRing 复用 `ReadOnlyHashRingView` | Deferred |
| P4 | URMA direct read + batch remote get | **In-Progress**（TCP+batch 已验；URMA ST 待 1129，见 [urma-direct-read-design.md](./urma-direct-read-design.md)） |
