# URMA Send Jetty Lane Isolation

**Status**: Draft  
**仓库**: yuanrong-datasystem  
**Source thread**: `codex://threads/019ef988-2fd5-7a90-a515-39d08615361a`  
**Source worktree**: `pr-1129-urma-fake`  
**Source HEAD**: `a81561a899cf97bd3fbfe9d4d7d8dd55b61139c7`  
**分支**: `feature/urma-send-jetty-lane-isolation`  
**Worktree**: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/urma-send-jetty-lane-isolation`

## 目标

1. 最小化改造 `UrmaConnection` 模型，不拆 connection 边界。
2. send jetty lane 化，每个 WR 只占用一个 lane，lane queue depth = 1。
3. 保持目的端 recv jetty 逻辑不动。
4. AE/CQE jetty fault 按 lane 级恢复，重建 send jetty 后重新 import targetJetty。
5. 普通写路径保持 NUMA affinity 分支，`srcChipId/dstChipId` 不被 lane 选择改写。
6. fake URMA 支持本地/远端验证，优先覆盖 CQE `local_id` 到发送 jetty 的定位链路。

## 文档

| 文档 | 说明 |
|------|------|
| [design.md](./design.md) | 主设计：背景问题、As-Is/To-Be、生命周期、故障处理、修改点 |
| [as-is-to-be-sequences.md](./as-is-to-be-sequences.md) | Mermaid 时序：普通路径、CQE/AE 故障、NUMA、backpressure |
| [worktree-verify.md](./worktree-verify.md) | worktree、tiantiyun 构建、验证命令 |
| [pr-description.md](./pr-description.md) | 后续 PR/MR 描述草稿 |
| [results.md](./results.md) | 验证结果记录 |

## 阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | 新 worktree + fake CQE `local_id` RED/GREEN | Done |
| P1 | `UrmaConnection` send lane lazy pool + event release | In-Progress |
| P2 | AE/CQE lane 级 recreate + targetJetty reimport | In-Progress |
| P3 | fake/manager UT 覆盖 lane 分配、backpressure、故障幂等 | Pending |
| P4 | tiantiyun targeted ST + PR | Pending |

## Assumptions

- 新 worktree 从 `a81561a899cf97bd3fbfe9d4d7d8dd55b61139c7` 创建。
- 原 `pr-1129-urma-fake` 未提交改动不纳入新 worktree，除非后续显式 cherry-pick。
- `urma_send_jetty_lane_pool_size=200` 是进程级 lazy send lane 预算；目的端 recv jetty 不扩池。
