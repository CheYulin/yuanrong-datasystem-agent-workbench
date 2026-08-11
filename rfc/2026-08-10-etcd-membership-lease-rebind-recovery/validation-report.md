# Issue #1027 外部 ETCD 冷重加验证报告

## 1. 验证对象

| 项目 | 内容 |
|---|---|
| 基线 | `master` `604b00b52d2a` |
| 分支 | `codex/etcd-recovery-v0.9.2-rc12` |
| 环境 | 配置的 Tiantiyun 验证节点，CMake `URMA_MOCK=ON` |
| 三方件 | 复用 `/home/ds-thirdparty-cache`；Bazel 使用其 repository cache |
| 并发 | 空闲时 `-j80`；发现其他构建时 `-j40` |

## 2. 修改量与意图

| 类别 | 新增 | 删除 | 意图 |
|---|---:|---:|---|
| 源码 | 67 | 16 | 区分 ACTIVE recovery 与 missing-local cold rejoin；复用 cleanup gate；串行化 voluntary exit 并保留聚合 timeout |
| 测试 | 231 | 0 | 3 条 UT 看护分支、顺序、重试、admission 与退出竞态；1 条 ST 看护单 Worker ETCD/TCP 故障恢复 |
| 合计 | 298 | 16 | 7 个文件，无协议、配置、持久化格式或 public SDK 变化 |

## 3. TDD 证据

| 阶段 | Case | RED | GREEN |
|---|---|---|---|
| Controller | `MissingLocalRecoveringMembershipColdRejoinsBeforeScaleOut` | rejoin handler 未调用，1.06s 失败 | 0.07s；`K_NOT_READY` 时不 scale-out，放行后成功 |
| Engine cleanup | `ColdRejoinCleansWhileIsolatedBeforePublishingReady` | 方法缺失导致目标编译失败，10s | 0.05s；隔离和 gate 先于 READY，失败保持 admission off |
| Exit race | `ColdRejoinSerializesVoluntaryExitWithinTimeout` | 无互斥时 exit 越过 gate；普通 mutex 时 20ms 预算被忽略，204ms 失败 | 0.08s；timed mutex 按 deadline 返回，RPC 使用剩余预算 |
| Cluster ST | `LEVEL1_WorkerEtcdReconnectColdRejoinsAndRestoresMetadataAccess` | Worker 重绑为 RECOVERING 后 39.97s 仍未重新 ACTIVE | 22.16s；恢复为 2 ACTIVE，跨 Worker Set/Get 成功 |

## 4. Pre-squash 验证结果

| 类型 | 数量 | 结果 | 墙钟/逐 Case |
|---|---:|---|---|
| CMake full build | 目标全集 | PASS | 558s（首次）；最终增量 98s |
| Topology/coordination UT | 116 | 116 PASS | 1.37s 总墙钟 |
| 新增 ETCD recovery ST | 1 | 1 PASS | 22.16s |
| Coordinator regression ST | 3 | 3 PASS | 28.88s / 17.00s / 33.63s，79.65s 总墙钟 |
| Bazel source + ST target | 2 targets | PASS | 首次 684s；pre-squash 增量 54s |
| clang-format | 7 changed files | PASS | 仅检查/格式化修改行，`git diff --check` PASS |
| clang-tidy | 2 production `.cpp` | PASS | clang 22；抑制 compile DB 的 linker-only 参数后无本次源码错误 |

验证目标：

- CMake：`cluster_topology_contract_ut`、`ds_st_kv_cache`、`ds_st_coordinator_backend_manual`。
- Bazel：`//src/datasystem/cluster:cluster_topology`、`//tests/st/client/kv_cache:kv_client_etcd_dfx_test`，`--config=urma_mock`。
- clang-tidy 未完整扫描测试 `.cpp`：单个大测试 translation unit 超过可接受验证时间；测试由 CMake/Bazel 编译覆盖，不为历史公共头告警扩大修改范围。

## 5. 措施二一致性

| 要求 | 证据 | 结论 |
|---|---|---|
| coordinator/topology 是唯一权威 | missing/ACTIVE 分支只读取权威 topology | 满足 |
| 被删除后先关 admission | `RequireMembershipRejoinOnce()` 在 cleanup gate 前执行 | 满足 |
| cleanup 成功后才 READY | gate error 直接返回；UT 验证 RECOVERING 与 admission off | 满足 |
| READY 后仍等待 ACTIVE topology | exact resync 后复用既有 scale-out；ST 等待 2 ACTIVE | 满足 |
| voluntary exit 不触发 destructive rejoin | timed transition mutex 线性化 exit intent 与 cleanup gate | 满足 |
| 不改变旧身份数据语义 | ST 仅验证恢复后的新业务，不断言旧 key 保留 | 满足 |

## 6. 覆盖边界

已覆盖动态注入的单 Worker lease/peer RPC 故障，Worker0 在 Worker1 注入清除前完成 Set/Get；未执行系统级
iptables/TCP 黑洞测试。未改变 peer hashring 路由纠偏、拓扑仲裁协议、cleanup 范围、RPC/proto 或持久化格式。

## 7. 最终交付

| 项目 | 结果 |
|---|---|
| Pre-squash backup | `codex/backup-etcd-recovery-1027-pre-squash-20260811` |
| Squash commit | `d74c327ad339bdb60f4160ece64a178bcb17100a`，相对 `master` 恰好 1 commit |
| Tree 一致性 | squash commit 与 backup tree 均为 `4375a02a3e2ac8f822aaea758a001d83c995a51b` |
| Exact-HEAD CMake | PASS，6s |
| Exact-HEAD UT | 116/116 PASS，1.38s |
| Exact-HEAD feature ST | 1/1 PASS，21.45s |
| Exact-HEAD coordinator ST | 3/3 PASS，31.78s / 17.31s / 33.32s |
| Exact-HEAD Bazel | 2/2 targets PASS，19s |

PR：https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1981

Issue #1027 已回填根因、修复方案和验证数据。

## 8. PR 门禁结果

| 门禁项 | 结果 | 证据 |
|---|---|---|
| 总任务 | PASS | [trigger #9459](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/trigger/job/yuanrong-datasystem/9459/console)，1637.77s |
| x86_64 | PASS | [x86 #9594](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/x86-64/job/yuanrong-datasystem/9594/console)，191.458s |
| aarch64 | PASS | [aarch64 #9530](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/aarch64/job/yuanrong-datasystem/9530/console)，1444.692s |
| Bazel x86 | PASS | `Test_Datasystem_Bazel_x86 #5483` |
| code/license/SCA | PASS | trigger #9459 三项均为 SUCCESS |

aarch64 门禁的 4433 条 LLT 全部通过；171 条 level1 ST 首轮有 1 条既有 Stream ST
`StreamDfxTopoTest.TestWorkerRestartThenClosePubSub` 失败，同一门禁自动单例重跑 27.35s 通过，最终任务成功。
本 PR 新增 `KVClientEtcdSingleWorkerReconnectTest.WorkerEtcdReconnectColdRejoinsAndRestoresMetadataAccess`
首轮通过，耗时 23.37s。该 Stream ST 不在本 PR 修改文件与调用路径内，且单例重跑通过，因此未为其扩大修改范围。

## 9. OS 挂起/恢复 ST 加固验收

2026-08-11 将原 inject-point 故障替换为真实 Worker 子进程 `SIGSTOP`/`SIGCONT`。验证基线为
`main/master` `a222c258897725588962f33a1239855b4e2f5e35`；修复提交 rebase 后为 `ab8d19afd`，测试改动未提交时
分别同步到独立 RED/GREEN worktree。Tiantiyun 构建复用 `/home/ds-thirdparty-cache`，启用 `-U on`
（URMA Mock），发现同机有其他任务后统一使用 `-j40`，所有长任务由 tmux 执行并保存 exit marker。

| 验证项 | 数量 | 结果 | 墙钟/逐 Case |
|---|---:|---|---|
| RED CMake full test build | 目标全集 | PASS | `-j40` 增量收敛 129s |
| RED focused ST | 1 | 预期失败 | 34.87s；挂起请求错误、`<6s` 和进程存活断言通过，恢复后 Worker 持续 `Not ready` |
| GREEN CMake full test build | 目标全集 | PASS | 652s，`-j40` |
| GREEN focused ST | 1 | 1 PASS | 24.86s（CTest 24.95s） |
| 新增特性 UT | 3 | 3 PASS | 0.07s / 0.06s / 0.08s；总墙钟 0.41s |
| 相邻 ETCD DFX ST | 6 | 5 PASS / 1 基线同现 | 100.70s；失败项见下文 |
| Bazel source build | 7 targets / 5429 actions | PASS | build 421s，总计 443s，`-j40` |
| clang-format / diff check | 1 个 ST 文件 | PASS | 无历史文件格式扩散 |

focused ST 覆盖以下链路：Worker1 基线 Set/Get 成功；`SIGSTOP` 后进程仍存活但面向 Worker1 的新请求在
2,000ms request timeout 约束下返回错误，断言上限为 6s；权威 topology 删除 Worker1 后 Worker0 仍可
Set/Get；`SIGCONT` 后 Worker1 完成本地清理、重新 READY/ACTIVE，并恢复 Worker1 Set、Worker0 Get。
`TearDown()` 在异常退出路径兜底发送 `SIGCONT`，避免残留暂停进程。

相邻回归中 `KVClientEtcdDfxTestAdjustNodeTimeout.TestRestartDuringEtcdCrash` 在修复分支连续两次失败，耗时
41.36s / 43.88s；在纯 `main/master` RED worktree 同样于第 346 行失败，耗时 44.01s。三次均为
`StartWorkerAndWaitReady({1})` 的 health 文件等待窗口耗尽，日志显示 Worker 的 reconciliation 在测试发送
SIGTERM 后才收敛。该问题在无本 PR 源码的基线同现，因此本次不通过放宽既有测试时序扩大修改范围。

覆盖边界：`SIGSTOP` 会同时冻结 Worker 业务、ETCD lease 与 peer RPC，比仅 TCP 黑洞更宽；本 case 证明
“进程存活但完全不响应”可恢复，不单独证明仅某一条网络链路故障的行为。
