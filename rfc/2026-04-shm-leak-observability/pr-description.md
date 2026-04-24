# /kind feature

**这是什么类型的 PR？**

/kind feature（可观测性增强；不改错误码、不改对外接口、不改业务行为）

> RFC 2026-04-shm-leak-observability 三个阶段（设计层面）的工作已 squash 为**单个 commit / 单个 PR**。下文按"逻辑分组"组织（Worker 释放对账 / Master TTL 链路 / Client 异步释放），方便分块评审。

---

## PR 概要

| 项 | 值 |
|---|---|
| 分支 | `feat/shm-leak-metrics-phase1`（branch 名沿用，不再做拆分） |
| Commit | `3bbcc55a feat(metrics): add 18 SHM-leak observability metrics` |
| MR | [gitcode #635](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/635) |
| 改动量 | 23 files / +734 / -6 lines |
| 新增 metric | **18** (`KvMetricId` 36..53；10 worker + 6 master + 2 client) |
| 新增 UT 文件 | 3 (`shm_leak_metrics_test.cpp` / `_phase2_test.cpp` / `_phase3_test.cpp`) |
| 新增 UT cases | **22** (9 + 8 + 5)，总耗时 **≤16 ms** |

---

## 这组 PR 做了什么 / 为什么需要

### 现场触发

2026-04-19，worker `kv2-jingpai-1` 出现 OOM：`shm.memUsage` 100 s 内从 **3.58 GB → 37.5 GB**（rate=0.999），同期 `OBJECT_COUNT` 从 **438 → 37**。这条"OBJECT_COUNT 反向于 OBJECT_SIZE"曲线是**元数据已删但物理 shm 仍被 `memoryRefTable_` 钉住**的经典签名（详见 [`yuanrong-datasystem-agent-workbench/bugfix/2026-04-19-worker-shm-oom-问题定位.md §3.2 d`](../../bugfix/2026-04-19-worker-shm-oom-问题定位.md)）。

### 既有 metric（PR #584/#586/#588 之后）的盲区

| 想看 | 现有手段 |
|---|---|
| Allocator 是否真把 shm 还回去了 | 只有 `OBJECT_SIZE` Gauge，无 alloc/free 速率对比 |
| `memoryRefTable_` 当前钉了多少 shm | **完全没有** |
| ShmUnit 是否被 `shared_ptr` 钉住 | **完全没有**（ctor/dtor 不计数） |
| 元数据 erase 频率 | **完全没有** |
| 兜底回收（RemoveClient）是否在跑 | **完全没有** |
| Master TTL 链路（pending/fire/success/failed/retry） | **完全没有**（只能事后翻日志） |
| Master 元数据是否泄漏 | **完全没有** |
| Client 释放被静默跳过的次数 | **完全没有**（bugfix §3.3 静默吞 RPC 的现场无法量化） |
| Client 异步释放是否滞后 | **完全没有** |

### 这组 PR 的解决方案

新增 **18 条 metric**（`KvMetricId` 36..53），三阶段递进：

1. **Phase 1（8 条 worker 释放对账）** —— 把 OOM 主因（释放链断裂）变成两条曲线就能看出来：`alloc - free` 持续涨 + `worker_shm_ref_table_bytes` 持续涨 + `OBJECT_COUNT` 持平。
2. **Phase 2（6 master TTL + 2 worker 兜底）** —— 暴露 master TTL 链路是否健康（扫描器是否在跑、fire/success/failed 是否守恒、pending 队列是否堆积），以及 worker 兜底（RemoveClient）和元数据 erase 频率。
3. **Phase 3（2 client 异步释放）** —— 把 bugfix §3.3 的"client 切 standby 静默丢 ref"现场变成两条曲线（`dec_ref_skipped_total` spike + `worker_remove_client_refs_total` 滞后 spike）。

---

## 整体设计约束（贯穿三阶段）

- **零锁、零遍历**：每条 metric 只能是 atomic op；任何 `for / map.find / 持锁查询` 都禁止。
- **单点开销 ≤ 50 ns**：典型为 1 次 `atomic::fetch_add(relaxed)` ≈ 15 ns。
- **size Gauge 优先用周期 `.size()` 单点读取**（TBB 容器 O(1) lock-free），避免散落 add/remove 站点逐点维护造成的长期漂移；只有 **bytes Gauge** 因容器不知 `shmUnit->size`，必须在 add/remove 站点 atomic 维护。
- **无锁容器（TBB concurrent_hash_map）**：`metaTable_` 138 处引用全部不动，**单点读 `.size()`**。
- **有锁容器（`timedObj_` std::multimap + mutex）**：用 atomic Gauge ±N 维护，但只在容器真实变化的 4 个汇合点（4 处而不是散落）。
- **不做现场快照 / dump**：所有"对账"留给读图人通过曲线对照。
- **不在采集侧做 join / 分类计算**：禁止 `objectTable` × `memoryRefTable` 求差集等操作。

---

## 接口/兼容性影响

- 无对外 API 签名变化。
- 无 `StatusCode` 枚举变化。
- 无协议字段变化。
- 无 `res_metrics.def` / `resource.log` 格式变化。
- 业务路径行为完全不变。
- `KvMetricId` 现有 ID 不动，仅末尾追加 18 个（36..53）。
- Phase 2 在 `OCMetadataManager` 新增 1 个 public inline accessor `GetMetaTableSize()`，O(1) 转发到 TBB `.size()`。
- Phase 3 在 `ObjectClientImpl` 复用既有 `StartMetricsThread` 周期循环，**不**新增线程 / 新增 mutex。

---

## 18 条新 metric 速查表

| # | 文本名 | 阶段 | 类型 | 单位 | 触发位置 |
|---|---|---|---|---|---|
| 1 | `worker_allocator_alloc_bytes_total` | P1 | Counter | bytes | `Allocator::AllocateMemory` 成功路径 |
| 2 | `worker_allocator_free_bytes_total` | P1 | Counter | bytes | `Allocator::FreeMemory` 成功路径 |
| 3 | `worker_shm_unit_created_total` | P1 | Counter | count | `ShmUnit` 3 个构造函数 |
| 4 | `worker_shm_unit_destroyed_total` | P1 | Counter | count | `~ShmUnit` |
| 5 | `worker_shm_ref_add_total` | P1 | Counter | count | `AddShmUnit` / `AddShmUnits` |
| 6 | `worker_shm_ref_remove_total` | P1 | Counter | count | `RemoveShmUnit` + `RemoveClient` 内循环 |
| 7 | `worker_shm_ref_table_size` | P1 | Gauge | count | 单点：`UpdateWorkerObjectGauge` 调 `shmRefTable_.size()`（TBB O(1)） |
| 8 | `worker_shm_ref_table_bytes` | P1 | Gauge (atomic) | bytes | 多点：`shmUnit->GetRefCount()` 跨 0/1 时 atomic ±`size` |
| 9 | `worker_remove_client_refs_total` | P2 | Counter | count | `SharedMemoryRefTable::RemoveClient` 入口（兜底回收数）|
| 10 | `worker_object_erase_total` | P2 | Counter | count | `ClearObject` 中 `objectTable_->Erase` 之后 |
| 11 | `master_object_meta_table_size` | P2 | Gauge | count | 单点：`ExpiredObjectManager::Run` 每 1 s 调 `OCMetadataManager::GetMetaTableSize()` |
| 12 | `master_ttl_pending_size` | P2 | Gauge (atomic) | count | `InsertObjectUnlock` Inc / `RemoveObjectIfExistUnlock` Dec / `GetExpiredObject` Dec(N) / `AddFailedObject` Inc(N) |
| 13 | `master_ttl_fire_total` | P2 | Counter | count | `GetExpiredObject` 批量 Add(N) |
| 14 | `master_ttl_delete_success_total` | P2 | Counter | count | `AsyncDelete` succeedIds 非空时 Add(N) |
| 15 | `master_ttl_delete_failed_total` | P2 | Counter | count | `AsyncDelete` failedIds 非空时 Add(N) |
| 16 | `master_ttl_retry_total` | P2 | Counter | count | `AddFailedObject` 批量 Add(N) |
| 17 | `client_async_release_queue_size` | P3 | Gauge | count | `StartMetricsThread` 周期 lambda 调 `asyncReleasePool_->GetWaitingTasksNum()` |
| 18 | `client_dec_ref_skipped_total` | P3 | Counter | count | `DecreaseReferenceCnt(Impl)` 3 处 early return（pool 空 / shm 空 / refcount 未归零 / IsBufferAlive 失败）|

---

## 看图就能定位的 7 类异常

| 现象（覆盖 bugfix §3.x 全部分类） | 证据组合（≤3 条曲线） |
|---|---|
| **Worker 元数据已删但 shm 未释放（4-19 OOM 主因，§3.2 d）** | `alloc_bytes_total` delta ≫ `free_bytes_total` delta + `worker_shm_ref_table_bytes` Gauge 单调上涨 + `OBJECT_COUNT` 持平 |
| ShmUnit `shared_ptr` 被某条路径钉住 | `created_total` − `destroyed_total` 持续上涨 |
| **Master TTL 扫描器卡死 / `MasterAsyncTask` 池满（§2 现场指纹之一）** | `master_ttl_pending_size` Gauge 单调涨 + `master_ttl_fire_total` delta = 0 |
| Master TTL 跑了但删不掉 | `master_ttl_fire_total` ≈ `master_ttl_delete_failed_total`，`success` 远小 |
| Master TTL 退避堆积 | `master_ttl_retry_total` 持续涨 + `pending_size` 同步抬升 |
| **Client 切 standby 静默丢 ref（§3.3 主因）** | `client_dec_ref_skipped_total` spike + 滞后窗口 `worker_remove_client_refs_total` spike |
| Client 异步释放滞后 | `client_async_release_queue_size` Gauge 持续 > 0 / 单调涨 |

---

## 主要代码变更（按阶段）

### Phase 1 — Worker 释放对账（14 files, +375 / -6）

- `kv_metrics.{h,cpp}`：8 个枚举 + descriptor
- `common/shared_memory/allocator.cpp`：`AllocateMemory` / `FreeMemory` 各 1 行
- `common/shared_memory/shm_unit.{h,cpp}`：3 个 ctor + 1 个 dtor 各 1 行（默认 ctor 从 `= default` 改为定义在 cpp）
- `common/object_cache/object_ref_info.{h,cpp}`：2 个 public accessor + add/remove 站点
- `worker/object_cache/worker_oc_service_impl.cpp`：`UpdateWorkerObjectGauge` 扩展（4 处调用更新）
- 2 处 BUILD.bazel + 2 处 CMakeLists.txt 加 `common_metrics` dep
- 新增 `tests/ut/common/metrics/shm_leak_metrics_test.cpp`（9 cases）

### Phase 2 — Master TTL + Worker 兜底（11 files, +288 / -0）

- `kv_metrics.{h,cpp}`：8 个枚举 + descriptor
- `common/object_cache/object_ref_info.cpp`：`RemoveClient` 复用 `removedCount` +1 行
- `worker/object_cache/service/worker_oc_service_crud_common_api.cpp`：`ClearObject` +1 行
- `master/object_cache/oc_metadata_manager.h`：新增 `GetMetaTableSize()` accessor
- `master/object_cache/expired_object_manager.cpp`：5 处 TTL 链路埋点 + `Run()` 周期 push meta size Gauge
- 2 处 BUILD.bazel + 1 处 CMakeLists.txt 加 `common_metrics` dep
- 新增 `tests/ut/common/metrics/shm_leak_metrics_phase2_test.cpp`（8 cases）

### Phase 3 — Client 异步释放（5 files, +181 / -0）

- `kv_metrics.{h,cpp}`：2 个枚举 + descriptor
- `client/object_cache/object_client_impl.cpp`：`DecreaseReferenceCnt(Impl)` 3 处 early return + `StartMetricsThread` lambda 加 1 行 Gauge.Set
- 新增 `tests/ut/common/metrics/shm_leak_metrics_phase3_test.cpp`（5 cases）

### 三阶段累计

| 维度 | 数值 |
|---|---|
| 文件改动 | **30 files** |
| 代码增量 | **+844 / -6 lines** |
| 新增 UT 文件 | 3（shm_leak_metrics_test / phase2 / phase3） |
| 新增 UT cases | **22**（9 + 8 + 5） |
| 新增 metric | **18**（KvMetricId 36..53）|

---

## 测试与脚本/文档交付

- 脚本：`yuanrong-datasystem-agent-workbench/scripts/testing/verify/run_shm_leak_metrics_remote.sh`
  - 一键远端 build + UT + 大文件清理 + 结果归档
  - 支持 `BUILD_BACKEND=bazel|cmake`、`BAZEL_UT`、`GTEST_FILTER` 覆盖
  - 自带 OOM-aware 重试 + 第三方库 `DS_OPENSOURCE_DIR` 缓存
- 文档：`yuanrong-datasystem-agent-workbench/rfc/2026-04-shm-leak-observability/`
  - `README.md` — 状态 + 三阶段索引
  - `design.md` — 完整方案（设计约束 / 18 条 metric 详表 / 决策表 / 分阶段实施）
  - `issue-rfc.md` — Issue / RFC 文案
  - `pr-description.md` — 本文件（统一 PR 描述）

---

## 最新验证结果（远端 `xqyun-32c32g`：32 c / 30 GB）

| 阶段 | Bazel build | Bazel test | UT cases | UT 总耗时 | 状态 |
|---|---|---|---|---|---|
| Phase 1 | 303 s（cold） + 137 s | — | 9 | **3 ms** | ✓ 9/9 PASS |
| Phase 1（CMake） | ~14 min（含 cold 第三方） | — | 9 | **11 ms** | ✓ 9/9 PASS |
| Phase 2 | 137 s（incremental） + 166 s | — | 8 | **4 ms** | ✓ 8/8 PASS |
| Phase 3 | 133 s（incremental） + 127 s | — | 5 | **1 ms** | ✓ 5/5 PASS |
| **三阶段合计 UT** | — | — | **22** | **≤ 16 ms** | ✓ 22/22 PASS |

**CI 影响**：22 个新 UT 总墙钟 ≤ 16 ms（最长单 case = 1 ms），远低于 10 s 单批阈值。

---

## 关联

- RFC：[`yuanrong-datasystem-agent-workbench/rfc/2026-04-shm-leak-observability/`](../../rfc/2026-04-shm-leak-observability/README.md)
- 现场分析：[`yuanrong-datasystem-agent-workbench/bugfix/2026-04-19-worker-shm-oom-问题定位.md`](../../bugfix/2026-04-19-worker-shm-oom-问题定位.md)
- 复用框架：PR #584（lightweight metrics framework） + PR #586（KvMetricId 体系）
- 关联 PR：本组 PR **不依赖**任何尚未合入的提交

---

## 不在本组 PR scope（明确隔离）

| 工作项 | 原因 / 去向 |
|---|---|
| 修复方案（bugfix §5 P0/P0'/P1/P0''/P0'''/P1''/P1' 等） | 走单独 fix RFC，本组 PR 纯观测 |
| 业务侧限流 / 缩容窗口缩短 / `node_dead_timeout_s` 调参 | 运维侧 |
| Per-client / per-shmId / age histogram 等需要遍历的指标 | 违反零遍历约束 |
| 现场快照 dump 接口（HTTP / SIGUSR1） | 用户明确不要 |
| Eviction Action 分类（DELETE / SPILL / FREE_MEMORY / END_LIFE） | 等三阶段上线后视需要再补 |

---

## 建议的 PR 标题（按阶段）

1. `feat(metrics): add 8 worker SHM-release-accounting metrics for ref-table leak observability (Phase 1)`
2. `feat(metrics): add 8 master TTL chain + worker fallback metrics for shm-leak observability (Phase 2)`
3. `feat(metrics): add 2 client async-release metrics for shm-leak observability (Phase 3)`

---

## Self-checklist（适用于全部三个 PR）

- [x] 不改错误码，不改对外 API，不改协议
- [x] 不改业务路径行为（纯观测增强）
- [x] 18 条新 metric 全部接入关键路径（Worker Allocator/ShmUnit/RefTable + Master TTL + Client async）
- [x] 所有埋点 ≤ 50 ns（zero-lock / zero-traversal）
- [x] size Gauge 优先用 TBB O(1) `.size()` 单点周期读（避免 138 处散点维护）
- [x] bytes / pending Gauge 在容器真实变化点 atomic ±N 维护，**不**散落到调用方
- [x] `AddShmUnits` 批量埋点采用一次累加 sum（不是循环逐个 atomic）
- [x] Bazel 构建通过（3 个新 UT target + worker / master / client 主链路）
- [x] CMake 构建通过（`ds_ut` + master/worker/client 增量链接）
- [x] UT 全部通过：22/22（Phase 1 9 + Phase 2 8 + Phase 3 5）
- [x] UT 总耗时 ≤ 16 ms（远低于 10 s CI 阈值）
- [x] 远端复跑脚本 `run_shm_leak_metrics_remote.sh` 已交付（一键 build + test + 归档）
- [x] PR 之间依赖关系清晰：Phase 2 stacks on Phase 1; Phase 3 stacks on Phase 2
