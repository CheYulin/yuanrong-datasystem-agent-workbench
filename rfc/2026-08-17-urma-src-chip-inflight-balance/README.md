# URMA 源芯片 inflight WR 近似均衡

| 属性 | 值 |
|---|---|
| 创建 | 2026-08-17，基于 PR 2081 `8e4aa6967fef67ced6228b478924b33d0e703835` |
| 阶段 | 详细设计与实现 |
| 代码分支 | `codex/pr2081-inflight-balance` |
| 隔离 worktree | `yuanrong-datasystem/.worktrees/pr2081-inflight-balance` |

本 RFC 为 PR 2081 的后续性能优化：保留目的端 NUMA 芯片亲和，在源端两个芯片的
inflight WR 深度差超过阈值时，临时覆盖轮询结果并选择较空闲芯片。阈值默认 15，
可配置，Client 与 Worker 的普通 URMA 写路径同时生效。

- [详细设计](detailed-design.md)
- [实现计划](implementation-plan.md)

本目录只保存 RFC，不引入 workbench 执行脚本。源代码与测试变更位于上述独立 worktree。
