# URMA 源芯片亲和优先与 RR 消融策略

| 属性 | 值 |
|---|---|
| Status | Implemented and Mock-Validated |
| 创建 | 2026-08-21 |
| DataSystem 基线 | `main/master` `c4daf0b591ef431cc0a849deaa408ad35c29e688` |
| DataSystem 分支 | `codex/urma-affinity-first-policy` |
| DataSystem 提交 | `6b09ca78e9c8387987b6281de88d25fb960bf859` |
| DataSystem PR | [openeuler/yuanrong-datasystem!2146](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2146) |
| 前置能力 | PR 2095 的源芯片 inflight WR 计数、阈值反馈、Client/Worker 配置同步和 URMA Mock ST |

## 目标

当前源芯片选择先生成 Round Robin 候选，只有两个芯片的 inflight WR 深度差严格大于阈值时，
才覆盖候选并选择较低深度芯片。本 RFC 将候选策略独立为可配置的两种模式：

- `0 = round_robin`：保持现有行为，作为消融基线与快速回滚策略；
- `1 = affinity_first`：默认优先使用发起内存所在的亲和芯片，只有深度差严格大于阈值时才溢出到另一芯片。

选择策略不改变目的端芯片亲和，不改变 `ub_numa_rr_type` 的选择粒度语义，也不引入锁、CAS 预占、
时间采样或同步的全局配额。目标是在一段时间内避免单芯片持续排队、另一芯片饿死，同时尽量减少
跨 CPU HCCS 流量。

## 交付物

- [详细设计](detailed-design.md)
- [TDD 实现计划](implementation-plan.md)
- [用例与验收矩阵](test-cases.md)

本目录只保存 RFC。DataSystem 的源代码与测试变更位于独立 worktree；URMA Mock 只证明配置传播、
选芯、WR 生命周期和读写正确性，真实 HCCS 带宽、P99 1.5 ms 与 PMax 5 ms 仍需硬件消融实验验收。

## 实现验证

- Tiantiyun CMake `-j80` 全量构建通过，复用共享第三方缓存；Bazel 未执行，仅完成 BUILD/CMake 闭包核对；
- focused UT 16/16、相邻 NUMA URMA Mock ST 2/2、双策略独立进程 E2E 2/2，总计 20/20；
- 双策略 E2E 各重复三轮，总计 6/6；
- `git diff --check`、JSON 解析、18 个 module metadata 和敏感模式扫描通过；
- PR source 相对最新 `main/master` 保持一个提交，fork 推送已校验，冲突状态 clean。
