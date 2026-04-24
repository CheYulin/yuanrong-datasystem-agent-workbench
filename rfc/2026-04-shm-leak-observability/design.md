# Worker SHM 泄漏可观测增强 · 设计文档

**仓库**：`yuanrong-datasystem`
**依赖框架**：`datasystem::metrics`（PR #584） + `KvMetricId` 枚举体系（PR #586）
**关联背景**：[`yuanrong-datasystem-agent-workbench/bugfix/2026-04-19-worker-shm-oom-问题定位.md`](../../bugfix/2026-04-19-worker-shm-oom-问题定位.md)

---

## 一、问题与目标

### 1.1 现场指纹（来源：bugfix §2）

`kv2-jingpai-1` worker 在 100 秒内出现 OOM，关键证据：

| 时间 | objCount | shm.memUsage / OBJECT_SIZE | 平均对象大小 |
|---|---|---|---|
| 23:16:01 | 438 | 3.58 GB | ~8 MB |
| 23:16:31 | 309 | 13.37 GB | ~43 MB |
| 23:17:01 | 306 | 27.49 GB | ~90 MB |
| 23:17:31 | **37** | **37.50 GB** | **~1.01 GB** |

**反向曲线**：`OBJECT_COUNT` 从 438 降到 37，但 `OBJECT_SIZE` 从 3.58 GB 涨到 37.5 GB → `objectTable_->Erase` 在跑（删元数据），但 `Allocator::FreeMemory` 没被调用（物理 shm 没还）。

bugfix §3 已经定位 6 条主因 / 放大器（§3.2 d / §3.3 / §3.4 i-v / §3.4.1 / §3.4.2），但**当前可观测性不足以让现场快速复述这个结论**：

- `memoryRefTable_` 当前没有任何 metric 暴露大小 / 字节量；
- `Allocator::AllocateMemory` 与 `FreeMemory` 没有累计计数器，只能通过 `OBJECT_SIZE` Gauge 间接看；
- `ShmUnit` / `ShmOwner` 的构造析构没有计数（无法判断"shared_ptr 是否在某条路径上被钉住"）；
- master 侧 `ExpiredObjectManager::timedObj_` / `failedObjects_` 完全不可见，无法回答"TTL 到底在跑没跑、跑没跑成功"；
- client `asyncReleasePool_` 滞后 / `DecreaseReferenceCntImpl` 几处 early return 没有计数（bugfix §3.3 切 standby 漏 ref 的直接证据点）。

### 1.2 目标

新增 **18 条 metric**（10 worker + 6 master + 2 client），让现场凭 `Metrics Summary` 时间序列就能：

1. 判断 shm 物理释放是否同步于元数据删除；
2. 判断 ref 表是否单调上涨（孤儿 ref 累积）；
3. 判断 TTL 链路（fire / success / failed / retry / pending）健康度；
4. 判断 master 元数据是否泄漏；
5. 判断 client 异步释放是否滞后 / 是否被静默吞掉。

### 1.3 非目标

- **不修复任何 bug**（修复走单独 RFC）；
- **不改业务路径 / 不改错误码**；
- **不做现场快照、dump、per-client / per-shmId 拆分**；
- **不分类（label）**：用多个独立 Counter 替代 label 维度，避免引入 label hash 开销与认知复杂度。

---

## 二、设计约束（硬约束）

| 约束 | 说明 | 验证手段 |
|---|---|---|
| 零锁、零遍历 | 每条 metric 只能是 atomic op；禁止 `for / map.find / 持锁查询` | code review |
| 单点开销 ≤ 50 ns | 典型 `fetch_add`(15 ns)；含 bytes 累加最多 2 次 atomic | UT 微基准（参考 #588 同款方法） |
| 埋点位置 ≤ 12 处 | 每处 ≤ 2 行新增代码，集中在已有"加 / 删 / 分配 / 释放"动作旁 | code review |
| 不在采集侧做 join / 分类计算 | 任何"差值 / 孤儿数"都留给读图人通过曲线对照得出 | code review |
| 不引入新依赖 | 仅用 `metrics::Counter / Gauge` | code review |
| Gauge 用 atomic 维护，不读容器 `.size()` | 避免依赖 `unordered_map::size()` 是否 O(1) 与是否需要持锁 | code review |

---

## 三、Metrics 完整清单（18 条）

> ID 在 `KvMetricId` 枚举末尾追加；文本名按 `KV_METRIC_DESCS` 顺序对应。

### 3.1 Worker（10 条）

| # | `KvMetricId` 枚举 | 文本 metric 名 | 类型 | 单位 | 采集点 |
|---|---|---|---|---|---|
| 1 | `WORKER_ALLOCATOR_ALLOC_BYTES_TOTAL` | `worker_allocator_alloc_bytes_total` | Counter | bytes | `common/shared_memory/allocator.cpp::AllocateMemory` 成功路径，紧贴 `stats->AddUsage(bytes)` |
| 2 | `WORKER_ALLOCATOR_FREE_BYTES_TOTAL` | `worker_allocator_free_bytes_total` | Counter | bytes | `common/shared_memory/allocator.cpp::FreeMemory` 成功路径，紧贴 `stats->SubUsage(bytesFree)` |
| 3 | `WORKER_SHM_UNIT_CREATED_TOTAL` | `worker_shm_unit_created_total` | Counter | count | `common/shared_memory/shm_unit.cpp::ShmUnit::ShmUnit()` |
| 4 | `WORKER_SHM_UNIT_DESTROYED_TOTAL` | `worker_shm_unit_destroyed_total` | Counter | count | `common/shared_memory/shm_unit.cpp::ShmUnit::~ShmUnit()` |
| 5 | `WORKER_SHM_REF_ADD_TOTAL` | `worker_shm_ref_add_total` | Counter | count | `common/object_cache/object_ref_info.cpp::AddShmUnit / AddShmUnits` |
| 6 | `WORKER_SHM_REF_REMOVE_TOTAL` | `worker_shm_ref_remove_total` | Counter | count | `common/object_cache/object_ref_info.cpp::RemoveShmUnit` + `RemoveClient` 内部循环每删一个 +1 |
| 7 | `WORKER_SHM_REF_TABLE_SIZE` | `worker_shm_ref_table_size` | Gauge | count | **单点**：worker `RegisterCollectHandler` 周期 lambda 调 `shmRefTable_.size()`（TBB `concurrent_hash_map` 的 `.size()` 是 O(1) lock-free atomic counter）|
| 8 | `WORKER_SHM_REF_TABLE_BYTES` | `worker_shm_ref_table_bytes` | **Gauge (atomic)** | bytes | **多点**：同 5/6 处 atomic ±`shmUnit->size`；`AddShmUnits` 批量入表时**一次累加 `totalBytes = ∑shmUnit->size`**（**OOM 直接相关，最关键**） |
| 9 | `WORKER_REMOVE_CLIENT_REFS_TOTAL` | `worker_remove_client_refs_total` | Counter | count | `RemoveClient(clientId)` 入口，累加"本次清掉的 ref 数" |
| 10 | `WORKER_OBJECT_ERASE_TOTAL` | `worker_object_erase_total` | Counter | count | `worker/object_cache/service/worker_oc_service_crud_common_api.cpp::ClearObject` 中 `objectTable_->Erase` 之后 +1 |

### 3.2 Master（6 条）

| # | `KvMetricId` 枚举 | 文本 metric 名 | 类型 | 单位 | 采集点 |
|---|---|---|---|---|---|
| 11 | `MASTER_OBJECT_META_TABLE_SIZE` | `master_object_meta_table_size` | Gauge | count | **单点**：master 已有的 metric tick 周期 lambda 调 `metaTable_.size()`（`oc_metadata_manager.h:1239`，`TbbMetaTable = tbb::concurrent_hash_map`，O(1) lock-free）。**不**对 `globalRefTable_`（GIncreaseRef/GDecreaseRef）观测，**不**涉及 etcd 表 |
| 12 | `MASTER_TTL_PENDING_SIZE` | `master_ttl_pending_size` | Gauge / Gauge (atomic) | count | 优先用 `timedObj_` 的 `.size()`（如果是 `std::set` / TBB 容器，O(1) 单点读取）；如果存在并发安全顾虑，则改用 `InsertObject` +1 / `Run()` 出队 -1 的 atomic Gauge 维护 |
| 13 | `MASTER_TTL_FIRE_TOTAL` | `master_ttl_fire_total` | Counter | count | `Run()` 扫描循环里每命中一个到期对象（即将提交 `AsyncDelete`）+1 |
| 14 | `MASTER_TTL_DELETE_SUCCESS_TOTAL` | `master_ttl_delete_success_total` | Counter | count | `AsyncDelete → NotifyDeleteAndClearMeta` 成功完成后 +1 |
| 15 | `MASTER_TTL_DELETE_FAILED_TOTAL` | `master_ttl_delete_failed_total` | Counter | count | 同上失败路径 +1（不区分原因） |
| 16 | `MASTER_TTL_RETRY_TOTAL` | `master_ttl_retry_total` | Counter | count | `AddFailedObject` 入口（指数退避重排）+1 |

### 3.3 Client（2 条）

| # | `KvMetricId` 枚举 | 文本 metric 名 | 类型 | 单位 | 采集点 |
|---|---|---|---|---|---|
| 17 | `CLIENT_ASYNC_RELEASE_QUEUE_SIZE` | `client_async_release_queue_size` | Gauge | count | `client/object_cache/object_client_impl.cpp::StartMetricsThread` 周期 lambda 调一次 `asyncReleasePool_->GetWaitingTasksNum()`（`common/util/thread_pool.h:158`，已存在接口，仓库内已无锁使用） |
| 18 | `CLIENT_DEC_REF_SKIPPED_TOTAL` | `client_dec_ref_skipped_total` | Counter | count | `DecreaseReferenceCntImpl` 3 处 early return 各 +1（`pool_null` / `not_zero` / `dead_buffer`，**不分维度**统一一条 Counter） |

---

## 四、采集点代码定位（精确到现有行附近）

### 4.1 Worker 端

**(1) `Allocator::AllocateMemory` / `FreeMemory`**

参考 bugfix §1：
- `OBJECT_SIZE` 已在 `Allocator::FreeMemory` 调 `stats->SubUsage(bytesFree)` 处递减；`AllocateMemory` 处递增。
- 新增 metric **紧贴这两处**：
  ```cpp
  // AllocateMemory 成功路径
  METRIC_ADD(KvMetricId::WORKER_ALLOCATOR_ALLOC_BYTES_TOTAL, bytes);

  // FreeMemory 成功路径
  METRIC_ADD(KvMetricId::WORKER_ALLOCATOR_FREE_BYTES_TOTAL, bytesFree);
  ```

**(2) `ShmUnit::ShmUnit() / ~ShmUnit()`**

bugfix §1 / §3.2 b 已多处提到 `~ShmUnit → FreeMemory` 链。在 `common/shared_memory/shm_unit.cpp` 现有 vlog 行附近：
```cpp
ShmUnit::ShmUnit() {
    ...
    METRIC_ADD(KvMetricId::WORKER_SHM_UNIT_CREATED_TOTAL, 1);
}
ShmUnit::~ShmUnit() {
    ...
    METRIC_ADD(KvMetricId::WORKER_SHM_UNIT_DESTROYED_TOTAL, 1);
}
```

**(3) `SharedMemoryRefTable::AddShmUnit / AddShmUnits / RemoveShmUnit / RemoveClient`**

bugfix §3.2 a 已指出这是核心数据结构。`object_ref_info.h:434-435` 显示 `shmRefTable_` / `clientRefTable_` 都是 `tbb::concurrent_hash_map`，TBB 的 `.size()` 是 O(1) lock-free atomic counter。

```cpp
// 单条 AddShmUnit
METRIC_ADD(KvMetricId::WORKER_SHM_REF_ADD_TOTAL, 1);
METRIC_GAUGE_ADD(KvMetricId::WORKER_SHM_REF_TABLE_BYTES, shmUnit->size);
// SIZE Gauge 不在这里维护

// 批量 AddShmUnits（一次累加，对外只 1 次 atomic op）
uint64_t totalBytes = 0;
for (const auto &u : shmUnits) {
    totalBytes += u->size;
}
METRIC_ADD(KvMetricId::WORKER_SHM_REF_ADD_TOTAL, shmUnits.size());
METRIC_GAUGE_ADD(KvMetricId::WORKER_SHM_REF_TABLE_BYTES, totalBytes);

// RemoveShmUnit 每删一项
METRIC_ADD(KvMetricId::WORKER_SHM_REF_REMOVE_TOTAL, 1);
METRIC_GAUGE_SUB(KvMetricId::WORKER_SHM_REF_TABLE_BYTES, shmUnit->size);

// RemoveClient 入口先记录本次将删的数量
METRIC_ADD(KvMetricId::WORKER_REMOVE_CLIENT_REFS_TOTAL, refsToRemove);
// 然后内部循环每删一个仍走 RemoveShmUnit 的 metric，互不重复

// SIZE 由 res_metric 周期 lambda 单点读取（不在 add/remove 路径维护）
// 在 worker_oc_server.cpp::RegisterCollectHandler 已有的 lambda 里追加：
metrics::GetGauge(KvMetricId::WORKER_SHM_REF_TABLE_SIZE).Set(memoryRefTable_->shmRefTable_.size());
```

> 实施小心事项：
> 1. `RemoveClient` 内部如果通过 `RemoveShmUnit` 一条一条删，**第 6/8 两条 metric 在循环里已经累加**，不要重复加；第 9 条 `WORKER_REMOVE_CLIENT_REFS_TOTAL` 是独立维度（"被批量清"的次数总和）。
> 2. `bytes` Gauge 必须**严格**和实际 add/remove 配对维护；建议在 UT 加压测后断言 `bytes Gauge` ≈ `∑ shmUnit->size`（误差由抢断 timing 导致，长期应趋零）。
> 3. 若 `shmRefTable_` 暴露 size 需要新增公开方法，在 `SharedMemoryRefTable` 类加一个 `size_t Size() const { return shmRefTable_.size(); }` 转发。

**(4) `ClearObject`**

bugfix §3.2 主结论是"`ClearObject` 不感知 `memoryRefTable_`"。本期不修，但加 1 条计数：

```cpp
// worker/object_cache/service/worker_oc_service_crud_common_api.cpp::ClearObject
RETURN_IF_NOT_OK_APPEND_MSG(objectTable_->Erase(objectKey, entry), ...);
METRIC_ADD(KvMetricId::WORKER_OBJECT_ERASE_TOTAL, 1);
evictionManager_->Erase(objectKey);
```

### 4.2 Master 端

**(5) `ExpiredObjectManager::InsertObject` / `Run()` / `AsyncDelete` / `AddFailedObject`**

bugfix §3.1 已经引用了 `master/object_cache/expired_object_manager.cpp:140-148 / 318-341 / 216-235`。在对应位置加：

```cpp
// InsertObject 成功
METRIC_ADD(KvMetricId::MASTER_TTL_PENDING_SIZE, 1);  // Gauge atomic +1

// Run() 扫到期、即将提交 AsyncDelete
METRIC_ADD(KvMetricId::MASTER_TTL_FIRE_TOTAL, 1);
METRIC_SUB(KvMetricId::MASTER_TTL_PENDING_SIZE, 1);  // 出队 -1

// AsyncDelete 完成回调
if (status.IsOk()) {
    METRIC_ADD(KvMetricId::MASTER_TTL_DELETE_SUCCESS_TOTAL, 1);
} else {
    METRIC_ADD(KvMetricId::MASTER_TTL_DELETE_FAILED_TOTAL, 1);
}

// AddFailedObject（指数退避重排）
METRIC_ADD(KvMetricId::MASTER_TTL_RETRY_TOTAL, 1);
```

**(6) Master `metaTable_` size 单点读取**

目标表已确认是 `master/object_cache/oc_metadata_manager.h:1239` 的 `metaTable_`（`TbbMetaTable = tbb::concurrent_hash_map<ImmutableString, ObjectMeta>`，O(1) lock-free `.size()`）。

- **不**对 `globalRefTable_`（`ObjectGlobalRefTable<ClientKey>`，gincref/gdecref 体系）观测。
- **不**对 etcd 派生表观测（不在 master 内存）。

```cpp
// master 已有的 metric tick 周期 lambda 里追加 1 行
metrics::GetGauge(KvMetricId::MASTER_OBJECT_META_TABLE_SIZE).Set(metaTable_.size());
```

> 由于 `metaTable_` 在 `oc_metadata_manager.cpp` 有 138 处引用，**严禁**在每个 insert/erase 站点逐点维护 atomic Gauge（必漏 → 长期漂移）。**单点读取是唯一可靠方案**。

### 4.3 Client 端

**(7) `DecreaseReferenceCntImpl` early return**

bugfix §3.2 d / §3.3 列出的 3 处 early return：

```cpp
// client/object_cache/object_client_impl.cpp::DecreaseReferenceCntImpl
if (asyncReleasePool_ == nullptr || shmId.Empty()) {
    METRIC_ADD(KvMetricId::CLIENT_DEC_REF_SKIPPED_TOTAL, 1);
    return;
}
...
if (!needDecreaseWorkerRef) {
    METRIC_ADD(KvMetricId::CLIENT_DEC_REF_SKIPPED_TOTAL, 1);
    return Status::OK();
}
if (isShm && !IsBufferAlive(version)) {
    METRIC_ADD(KvMetricId::CLIENT_DEC_REF_SKIPPED_TOTAL, 1);
    return Status::OK();
}
```

**(8) `asyncReleasePool_` 队列长度**

```cpp
// client/object_cache/object_client_impl.cpp::StartMetricsThread 已有的周期 lambda
metrics::GetGauge(KvMetricId::CLIENT_ASYNC_RELEASE_QUEUE_SIZE)
       .Set(asyncReleasePool_ ? asyncReleasePool_->GetWaitingTasksNum() : 0);
```

> `ThreadPool::GetWaitingTasksNum()` 在 `common/util/thread_pool.h:158` 已存在，直接返回 `taskQ_.size()`，仓库内已无锁使用（line 156、260、263、266 等）。**不需要新增任何接口**。

---

## 五、热路径开销分析

参考 PR #586/#588 的实测数据（design.md 第二章），单条 atomic metric op 的实测开销：

| 操作 | 实测耗时 | 说明 |
|---|---|---|
| `Counter::Inc(n)` | ~5-15 ns | 1 次 `fetch_add(memory_order_relaxed)` |
| `Gauge::Set(n)` | ~5-10 ns | 1 次 `store(relaxed)` |
| `Gauge::Inc/Dec/Add/Sub(n)` | ~5-15 ns | 1 次 `fetch_add(relaxed)` |

本 RFC 18 条 metric 中**最重的埋点**（v3.1 简化后，size Gauge 改成单点周期读取，热路径开销进一步下降）：

| 埋点 | 操作 | 累计耗时 |
|---|---|---|
| `AddShmUnit` | 1 Counter (#5) + 1 Gauge add bytes (#8) | ~20-30 ns |
| `RemoveShmUnit` | 1 Counter (#6) + 1 Gauge sub bytes (#8) | ~20-30 ns |
| `AddShmUnits` 批量 | 累加 sum + 1 Counter (#5) + 1 Gauge add bytes (#8) | ~20-30 ns（与单条相同，sum 计算可忽略）|
| `RemoveClient` | (上面循环 N 次) + 1 Counter (#9) | 视 N 而定，本身额外 +15 ns |
| res_metric tick lambda | `shmRefTable_.size()`（TBB O(1) atomic load）+ `Gauge.Set(...)` | ~10 ns/次，10s 一次，可忽略 |

**远低于 1 μs**，满足约束。其余埋点都是 1 个 Counter +1（≤ 15 ns）。

> v3.1 相对 v3 的优化：把 `WORKER_SHM_REF_TABLE_SIZE` / `MASTER_OBJECT_META_TABLE_SIZE` / 可能的 `MASTER_TTL_PENDING_SIZE` 从"add/remove 站点逐点 atomic 维护"改成"周期 lambda 单点 `.size()` 读取"，**完全消除了 size Gauge 与实际容器漂移的可能性**，热路径热度也降低。

---

## 六、对照决策表（写进 docs/observable/07）

| 现象 | 主证据组合 | 结论 |
|---|---|---|
| **bugfix §3.2 d 主因** | `WORKER_ALLOCATOR_ALLOC_BYTES_TOTAL` delta ≫ `..._FREE_BYTES_TOTAL` delta + `WORKER_SHM_REF_TABLE_BYTES` Gauge 单调涨 + `OBJECT_COUNT` 持平 | 元数据已删但 ref 钉着 shm |
| **ShmOwner 整批锁死（§3.2 b）** | `WORKER_SHM_UNIT_CREATED - DESTROYED` 差值持续涨 + 平均对象大小（`OBJECT_SIZE / OBJECT_COUNT`）异常上涨 | 聚合 batch 中有 slice 未释放 |
| **TTL 完全没跑** | `MASTER_TTL_FIRE_TOTAL` delta = 0 但 `MASTER_TTL_PENDING_SIZE` > 0 | 扫描器 hang / `MasterAsyncTask` 池满 |
| **TTL 跑了但删不掉** | `MASTER_TTL_DELETE_FAILED_TOTAL` spike + `..._RETRY_TOTAL` 持续涨 | 删除 RPC 失败 |
| **TTL 队列堆积** | `MASTER_TTL_PENDING_SIZE` 持续涨 + `..._FIRE_TOTAL` delta ≪ `InsertObject` 速率 | 写入速率 > 删除速率 |
| **Master 元数据泄漏** | `MASTER_OBJECT_META_TABLE_SIZE` 单调涨 / 同期 worker `OBJECT_COUNT` 持平 | master 视图未与 worker 同步 |
| **释放靠 RemoveClient 兜底（§3.3）** | `WORKER_REMOVE_CLIENT_REFS_TOTAL` 持续 > 0 + 同期 `WORKER_SHM_REF_TABLE_BYTES` 阶跃下降 | 释放链断了，靠心跳超时兜底 |
| **Client 切 standby 漏 ref（§3.3）** | `CLIENT_DEC_REF_SKIPPED_TOTAL` spike + 滞后窗口 `WORKER_REMOVE_CLIENT_REFS_TOTAL` spike | `memoryRefCount_.Clear()` + `IsBufferAlive` 拦截 |
| **Client 异步释放滞后** | `CLIENT_ASYNC_RELEASE_QUEUE_SIZE` 持续 > 0 / 单调涨 | 单线程 `asyncReleasePool_` 跟不上写入 |
| **业务在删元数据但没在释 shm** | `WORKER_OBJECT_ERASE_TOTAL` delta > 0 + `WORKER_ALLOCATOR_FREE_BYTES_TOTAL` delta ≈ 0 | bugfix 主因的另一种表述 |

---

## 七、分阶段实施

### 阶段 1（最小可证明集，~1 周）

- 实现 metric #1 #2 #3 #4 #5 #6 #7 #8（8 条 worker 核心）
- UT 覆盖：alloc/free 计数 ↔ `OBJECT_SIZE` 一致性、ref add/remove 与 size/bytes Gauge 一致性、并发安全
- 退出条件：在 bugfix §2 同款负载下，`Metrics Summary` 能直接复述"alloc 远快于 free / ref bytes 单调涨"两条曲线

### 阶段 2（Master + 兜底，~1 周）

- 实现 metric #9 #10（worker） + #11~16（master 6 条）
- UT 覆盖：TTL fire / success / failed / retry 计数；元数据表 size 一致性
- 退出条件：在 TTL 注入故障场景下能定位"完全没跑 / 跑了删不掉 / 退避堆积"三种状态

### 阶段 3（Client + 端到端，~1 周）

- 实现 metric #17 #18
- 串场景：在被动缩容 + 切 standby 注入下，验证 `CLIENT_DEC_REF_SKIPPED_TOTAL` spike 与 `WORKER_REMOVE_CLIENT_REFS_TOTAL` 滞后 spike 形成
- 完成对照决策表 + 决策树补丁回写 `docs/observable/07-pr-metrics-fault-localization.md`

---

## 八、验收标准

### 8.1 功能验收

- [ ] `Metrics Summary` 中 18 条新 metric 全部出现在 `Total:` 段
- [ ] 单元测试：每条 metric 的"递增 / 同步 / 并发"都有 case，全部 PASS
- [ ] 微基准：`AddShmUnit` 路径开销 ≤ 50 ns（与不加 metric 的 baseline 对比）

### 8.2 场景验收（在远端 `xqyun-32c32g` 跑）

| 场景 | 期望证据 |
|---|---|
| **OOM 重放**（`set_usr_pin_8m` 5 分钟） | `worker_allocator_alloc_bytes_total` 周期 delta ≥ 5× `..._free_bytes_total` 周期 delta；`worker_shm_ref_table_bytes` Gauge 单调上涨 |
| **TTL 健康** | `master_ttl_fire_total` ≈ `master_ttl_delete_success_total`；`master_ttl_pending_size` 稳定 |
| **TTL 故障**（注入 master_async_task 池满） | `master_ttl_pending_size` 持续涨 + `master_ttl_fire_total` 增速骤降 |
| **被动缩容**（注入 passive scale-down） | `client_dec_ref_skipped_total` spike + `worker_remove_client_refs_total` 滞后 spike |
| **接管端 OOM**（在 standby worker 上看相同 18 条） | 与故障端 metric 形态可分（无 `dec_ref_skipped` spike，但 `alloc - free` 同样异常）|

### 8.3 文档验收

- [ ] `docs/observable/05-metrics-and-perf.md` 末尾追加 18 条 metric 列表
- [ ] `docs/observable/07-pr-metrics-fault-localization.md` §3 追加"释放对账"场景行 + §6 决策树补"shm 涨而 OBJECT_COUNT 不涨"分支
- [ ] `docs/observable/04-triage-handbook.md` 追加"孤儿 ref 快速判定 SOP"小节

---

## 九、不在本期 scope（明确隔离）

| 工作项 | 原因 / 去向 |
|---|---|
| 修复方案（bugfix §5 P0 / P0' / P1 / P0'' / P0''' / P1'' / P1' / P2-P5） | 修复独立成 RFC，本期纯观测 |
| Per-client / per-shmId 统计 | 违反零遍历约束 |
| Ref 持有时长 histogram | 需要 shmId → timestamp join |
| 现场快照 dump（HTTP / SIGUSR1） | 用户明确不要 |
| Eviction Action 分类（DELETE / SPILL / FREE_MEMORY / END_LIFE） | 等首期上线后视需要再补 |
| 接管端 GIncreaseRef / SwitchToStandbyWorker 计数 | 同 18 条 metric 在 standby worker 上观测即可 |
| `RemoveClient` 的 bytes 维度 | 用户已确认不加（count 足够告警，bytes 通过 `WORKER_SHM_REF_TABLE_BYTES` 阶跃反推） |
| `MASTER_ASYNC_TASKS_THREAD_POOL` 告警 | 已有 metric，告警阈值由运维侧配置 |

---

## 十、风险与权衡

| 风险 | 缓解 |
|---|---|
| ~~`MASTER_OBJECT_META_TABLE_SIZE` 表名~~ | **已确认**为 `metaTable_`（`oc_metadata_manager.h:1239`，TBB concurrent_hash_map）；不涉及 globalRefTable_、不涉及 etcd 派生表 |
| ~~`ThreadPool::GetQueueSize()` 接口缺失~~ | **已确认**：`GetWaitingTasksNum()`（`thread_pool.h:158`）已存在并无锁使用 |
| `RemoveClient` 内部循环 + 入口计数器之间的语义重叠 | 设计已明确分离（#6 是"单删次数"、#9 是"批删事件"）；写实施 PR 时强制 code review |
| `WORKER_SHM_REF_TABLE_BYTES` Gauge atomic 维护漏埋点 | UT 加"短压测后断言 `bytes Gauge` ≈ 0"（清空后）；并加"长压测后断言 Gauge bytes / `WORKER_ALLOCATOR_ALLOC_BYTES_TOTAL` 比值稳定"；不通过则说明 add/remove 路径有遗漏 |
| `MASTER_TTL_PENDING_SIZE` 的容器并发安全 | 实施前确认 `timedObj_` 容器类型；如果是 `std::set` + 持锁访问，则 `.size()` 不能裸调，需加 atomic 维护；如果是 TBB / 已有锁保护下的周期采集点，单点读取即可 |

---

## 十一、引用代码位置（与 bugfix 文档一致）

- 资源 / 释放
  - `common/shared_memory/allocator.{h,cpp}`
  - `common/shared_memory/shm_unit.{h,cpp}`
  - `common/object_cache/object_ref_info.{h,cpp}`（`SharedMemoryRefTable`）
- Worker
  - `worker/object_cache/service/worker_oc_service_crud_common_api.cpp`（`ClearObject`）
  - `worker/object_cache/worker_oc_service_impl.{h,cpp}`
- Master
  - `master/object_cache/expired_object_manager.cpp`（TTL 主链路）
  - master 元数据表（具体文件待 owner 确认）
- Client
  - `client/object_cache/object_client_impl.cpp`（`DecreaseReferenceCntImpl` / `StartMetricsThread` / `asyncReleasePool_`）
- 框架
  - `common/metrics/metrics.{h,cpp}`（PR #584）
  - `common/metrics/kv_metrics.{h,cpp}`（PR #586，本 RFC 在末尾追加）
