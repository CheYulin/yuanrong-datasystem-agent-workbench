# UT / ST 全量跑测结论与整改计划

本文档基于一次 **全量 CMake 用例** 跑测（单元测试 `ds_ut`、系统测试 `ds_st`）的观测结果，用于识别 **耗时长、易失败、阻塞全绿** 的用例与改进方向。执行主机与路径以当时环境为准，复现时请对齐构建产物与依赖服务（etcd、IAM mock、日志目录等）。

### 逐用例「耗时 + 成败」完整表：当前是否具备？

**结论：目前不具备「全量 UT + 全量 ST」的完整矩阵；仅 ST 有一部分机器可读产物。**

| 范围 | 是否完整 | 说明 |
|------|----------|------|
| **`ds_ut`** | **否** | 一次全量在 **`InjectPointTest`** 处中断，**无** 全量逐条结果文件；另一次排除该套件后 **长时间运行且未统一 tee 落盘**，故本仓库 **未保存** 每个用例的耗时与成功情况。 |
| **`ds_st`** | **不完全** | 从单次远端日志解析出 **105** 条 `PASS`/`FAIL`（**93 PASS + 12 FAIL**），与日志中 **`Running 121 tests`** 不一致，**缺少约 16 条** 的正式结果行；进程末尾在 **ZmqTest** 相关失败处结束，**无** gtest 全局 `[  PASSED ] N tests` 汇总。 |

**已落盘数据（TSV）**：见 **[`runs/`](./runs/README.md)** — `20260416_remote_st_parsed.tsv`（及按耗时排序的 `20260416_remote_st_by_duration.tsv`）。列格式：`状态<TAB>毫秒<TAB>用例名`。

**若要下次得到完整表**：对 `ds_ut` / `ds_st` 均使用 `tee` 保存完整 stdout，并视需要开启 gtest XML/JUnit 输出（取决于 gtest 版本与链接方式）。

---

## 1. 执行摘要

| 维度 | 结论 |
|------|------|
| **UT 全量（无 filter）** | 在 **`InjectPointTest.TestTaskAbort`** 处失败；随后 **`TestMutilTask2` 触发进程 SIGABRT**，**后续用例未执行**。全量 UT **不可视为已完成**。 |
| **UT（排除 `InjectPointTest.*`）** | 曾启动 **619 cases** 作为绕行方案；跑测 **耗时极长**（量级可达 **1 小时+**），且存在 **日志洪泛**（如 log 性能类用例）导致日志体积膨胀。 |
| **ST 全量** | 单次日志中统计到 **12 条 `[ FAILED ]`**；可解析 **105** 条逐用例结果，**非** 全量 121 条；日志含 **二进制片段**，需 `grep -a` 或 `strings` 解析。 |
| **慢测集中区** | **IAM 重试**、**死亡测试**、**Timer 多定时器**、**RocksReplica**、**EtcdStore 套件**、**ListenWorker 心跳/重连**、**ZmqTest 流式长超时** 等。 |

---

## 2. 单元测试（`ds_ut`）关键结论

### 2.1 阻塞性问题（必须先修）

1. **`InjectPointTest`**
   - **`TestTaskAbort`**：死亡测试期望与 **实际 SIGABRT / failure_handler 输出** 不一致，断言失败。
   - **后续用例**：在失败路径上出现 **主进程 SIGABRT**（与 `TestMutilTask2` 等相关），导致 **整进程退出**，全量 UT **无法跑完**。
   - **风险特征**：gtest 对 **death test + 多线程** 有明确警告（fork 与线程组合不安全），易 **超时 / 不稳定**。
   - **整改方向**：
     - 将死亡测试与 **多线程环境** 解耦（单测进程、独立二进制、`EXPECT_DEATH` 使用方式、gtest 文档建议）。
     - 失败路径 **不得** 让 **主测试进程** abort；注入失败应 **可预期、可恢复**。
     - 短期可将 **`InjectPointTest` 从默认全量** 中隔离（单独 target / 标签 / CI job），避免阻塞主门禁。

### 2.2 高耗时与稳定性敏感（优先优化或拆分）

以下用例在片段日志中表现为 **数秒～数十秒级**，显著拉长全量 UT 时间：

| 类型 | 示例 | 说明 |
|------|------|------|
| IAM / Tenant | `TenantAuthManagerTest.YuanIamServerRetryFailed`（约 **17s** 级） | 注入失败 + 重试退避，**固定等待** 拉长总时长。 |
| IAM / Tenant | `YuanIamServerRetry` / `YuanIamServerWithAkRetry`（约 **2s** 级） | 重试节奏可评估是否可 **缩短** 或在 UT 中 **mock 时钟**。 |
| AK/SK | `AkSkTest.AkSkTimeout`（约 **2s**） | 超时类测试天然偏慢。 |
| Timer | `TimerTest.MultiTimer`（约 **4～5s**） | 多定时器真实等待。 |
| 死亡测试 | `FailureHandlerDeathTest`、`LogMessageDeathTest`（约 **0.4～1.2s**） | 依赖 fork；与线程并存时不稳定。 |
| Rocks / 副本 | `RocksReplicaTest.*`（片段中可达 **数秒**） | IO/同步路径，全量时累积明显。 |

### 2.3 日志与可观测性风险

- 部分用例（如 **log 性能** 相关）在跑测中产生 **海量重复日志行**，导致：
  - 本地/CI **磁盘与解析** 压力；
  - 远端全量跑测 **难以归档**、**难 grep**。
- **整改方向**：默认关闭极限压测日志、限制行数、或改为 **采样/聚合** 断言。

---

## 3. 系统测试（`ds_st`）关键结论

### 3.1 本次观测到的失败用例（用于整改 backlog）

下列名称来自单次全量 ST 日志中的 **`[ FAILED ]`** 行（共 **12** 条），**环境（etcd/TLS/日志目录/并发）** 未满足时易复现：

- `ListenWorkerRediscoverTest.TestRediscoverSuccessClearsSwitched`
- `EtcdStoreTest.TestRetrieveCrossVersionEvent`
- `EtcdStoreTest.TestTransactionCompareKeyVersion`
- `EtcdStoreTest.TestBatchPutKeyValue`
- `EtcdStoreTest.TestCAS`
- `EtcdStoreTest.TestCASCompareValue`
- `EtcdSslTest.TestCreateSessionWithTls`
- `EtcdSslWithPassphraseTest.TestCreateSessionWithTls`
- `GrpcSessionTest.TestPutClusterTableWithoutLeaseId`
- `KVCacheLogPerformanceTest.TestMultiClients`
- `LoggingFreeTest.FreelogWhenUsingAsyncLog`
- `ZmqTest.ZmqFdEventCrash3`

另在日志中观察到 **`ZmqTest` 与 `FileExist(LOG_DIR)`** 相关断言（期望日志目录存在），属 **路径/环境前置** 类问题，与 **并发跑 ST、工作目录假设** 叠加时易 flake。

### 3.2 高耗时用例（全量 ST 时间主要消耗）

从日志中按 **单用例 OK 行耗时** 粗筛（**≥ 约 2s** 量级），下列 **拉长整轮 ST** 且依赖 **真实等待 / 外部组件**：

| 量级 | 示例 |
|------|------|
| **~40s+** | `ZmqTest.LEVEL2_StreamGreetingWithTimeout2` |
| **~30s+** | `EtcdStoreTest.LEVEL2_TestPutLease2`、`LEVEL1_TestPutLease3`、`ZmqTest.LEVEL2_StreamGreetingWithTimeout1`、`EtcdStoreTest.LEVEL1_TestWatchEvents2` |
| **~15～20s** | `ListenWorkerTest.TestRPCHeartheat`、`ZmqTest.MultiThreadSendBigBuf`、`EtcdStoreTest.TestWatchEvents3`、`ZmqTest.Restart` 等 |
| **~10～15s** | 多条 **ListenWorker / Etcd watch / retrieve** |
| **~6～8s** | `ZmqMetricsFaultTest.ServerKilled_GwRecreateDetectsPeerCrash`（故障注入 + 等待） |

**EtcdStoreTest** 在日志中曾出现 **整 suite 累计约数百秒** 量级，是 **ST 总时长与不稳定** 的核心来源之一。

### 3.3 风险归纳

- **Etcd / gRPC / TLS**：强依赖 **本地 etcd、证书、网络栈**；CI 未统一 provision 时 **失败集中**。
- **ListenWorker / Router**：心跳、重连、rediscover，**时间长**且对 **端口、进程残留** 敏感。
- **ZMQ**：长超时流式用例、crash 类用例、日志目录与 **fd/并发** 相关用例，**环境敏感**。

---

## 4. 与 Bazel 的关系（非 CMake 全量）

当前仓库 **Bazel 与 WORKSPACE/Bzlmod 迁移** 仍存在问题（如 Bazel 9 与 `native.cc_*`、protobuf 工具链等），**全量验证仍以 CMake `ds_ut`/`ds_st` 为准**。Bazel 门禁修复应 **单独立项**，不阻塞本文 UT/ST 整改项跟踪。

---

## 5. 建议整改阶段（可纳入迭代计划）

### 阶段 A：止血（ unblock 全量 UT ）

1. 修复或隔离 **`InjectPointTest`**（死亡测试与 abort 级联），保证 **`ds_ut` 无 filter 可跑完**。
2. 明确 **全量 UT 默认是否包含** 死亡测试 / 注入类长耗时用例；若不包含，用 **标签或独立 job** 固化。

### 阶段 B：缩短与稳定 ST

1. **Etcd / SSL / gRPC** 失败项：统一 **测试前置条件**（脚本、容器、文档），或 **mock 降级** 非关键路径。
2. **ZmqTest** 日志目录、`ZmqFdEventCrash3`：**确定性目录**、与并发 ST **隔离工作副本**。
3. 对 **>30s** 单用例：评估 **缩短超时**、拆分场景或 **单独 nightly**。

### 阶段 C：可观测性与 CI 策略

1. 全量跑测 **输出纯文本**（避免日志中二进制导致 `grep` 失效）。
2. 产出 **慢测 Top N**、**FAILED 列表** 的固定脚本，挂到 CI artifact。
3. **IAM / Rocks / Log perf** 等长耗时 UT：**分类门禁**（PR 跑子集，nightly 全量）。

---

## 6. 待办清单（跟踪用）

| ID | 项 | 类型 | 优先级 |
|----|----|------|--------|
| T1 | 修复 `InjectPointTest` 死亡测试与后续 SIGABRT，恢复 **无 filter 全量 UT** | UT / 阻塞 | P0 |
| T2 | 评估 `InjectPointTest` 与 **多线程** 的隔离方案（独立进程 / 标签） | UT / 稳定性 | P0 |
| T3 | 收敛 IAM `TenantAuthManagerTest` 长耗时（mock 时间或缩短退避） | UT / 时长 | P1 |
| T4 | 收敛 log 性能类用例 **日志洪泛** | UT / 可维护性 | P1 |
| T5 | 逐项分析 ST **12 个 FAILED**（etcd/TLS/grpc/zmq/log）根因与前置条件 | ST / 失败 | P0 |
| T6 | `ZmqTest` `LOG_DIR` / 路径类断言：**文档 + 测试夹具** 统一 | ST / flake | P1 |
| T7 | 长耗时 ST（Etcd / ListenWorker / Zmq 流式）**分级**：PR vs nightly | ST / CI | P2 |
| T8 | Bazel 构建与依赖迁移 **单独跟踪**（不混入 CMake 整改） | 构建 | P2 |

---

## 7. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04-16 | 初稿：基于全量 `ds_ut`/`ds_st` 跑测结论整理 |
| 2026-04-17 | 补充「数据完整性」说明；ST 部分结果落盘至 `runs/*.tsv` |
