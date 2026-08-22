# URMA RR 基线上的机会式源芯片亲和策略

| 属性 | 值 |
|---|---|
| Status | Implemented and Mock-Validated |
| 创建 | 2026-08-21 |
| DataSystem 基线 | `main/master` `65f0f9f8ac06c4d342f595d946de400c9be96b4f` |
| DataSystem 分支 | `codex/urma-affinity-first-policy` |
| DataSystem 提交 | `f28439c52e2826e03893732f3b411b62f6a22c5c` |
| DataSystem PR | [openeuler/yuanrong-datasystem!2146](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2146) |
| 前置能力 | PR 2095 的源芯片 inflight WR 计数、阈值反馈、Client/Worker 配置同步和 URMA Mock ST |

## 目标

当前源芯片选择先生成 Round Robin 候选，只有两个芯片的 inflight WR 深度差严格大于阈值时，
才覆盖候选并选择较低深度芯片。本 RFC 在不牺牲该均衡基线的前提下增加机会式 NUMA 亲和：

- `0 = round_robin`：保持现有行为，作为消融基线与快速回滚策略；
- `1 = round_robin_with_affinity`：仍先生成 RR 候选；仅当亲和芯片接收本逻辑写的预计 WR 后仍不比
  RR 候选更忙时才覆盖候选；深度差严格大于阈值时仍无条件选择较空芯片。

选择策略不改变目的端芯片亲和，不改变 `ub_numa_rr_type` 的选择粒度语义，也不引入锁、CAS 预占、
时间采样或同步的全局配额。深度相等时保持 RR，避免同 key 并发读同时看到相同快照后扎堆；只利用
已经存在的空闲深度换取本地 NUMA 命中。

## 交付物

- [详细设计](detailed-design.md)
- [TDD 实现计划](implementation-plan.md)
- [用例与验收矩阵](test-cases.md)

本目录只保存 RFC。DataSystem 的源代码与测试变更位于独立 worktree；URMA Mock 只证明配置传播、
选芯、WR 生命周期和读写正确性，真实 HCCS 带宽、P99 1.5 ms 与 PMax 5 ms 仍需硬件消融实验验收。

## 实现验证

- Tiantiyun CMake `-j80` 全量构建通过，复用共享第三方缓存；Bazel 未执行，仅完成 BUILD/CMake 闭包核对；
- focused UT 19/19、相邻 NUMA URMA Mock ST 2/2、双策略独立进程 E2E 2/2，总计 23/23；
- 双策略 E2E 在策略修订后各重复三轮，总计 6/6；rebase 最新 `main/master` 后各追加一轮并通过；
- `git diff --check`、JSON 解析、18 个 module metadata 和敏感模式扫描通过；
- PR source 相对最新 `main/master` 保持一个提交，fork 推送已校验，冲突状态 clean。
