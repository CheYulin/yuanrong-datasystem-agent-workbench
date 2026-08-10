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
| Bazel source + ST target | 2 targets | PASS | 首次 684s；最终增量结果待补 |
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

最终 squash commit、精确 HEAD CMake/Bazel 复验和 PR 门禁链接在 squash 后补充。
