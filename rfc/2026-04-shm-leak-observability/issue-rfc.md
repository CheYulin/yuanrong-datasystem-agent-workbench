# [RFC] Worker SHM 泄漏可观测增强（Object / Buffer / Ref / TTL 释放对账）

## 背景与目标描述

### 现场触发

2026-04-19 `kv2-jingpai-1` worker 出现 OOM：`shm.memUsage` 在 100 秒内从 3.58 GB 涨到 37.5 GB（`rate=0.999`），同期 `OBJECT_COUNT` 从 438 降到 37。详细分析见 [`vibe-coding-files/bugfix/2026-04-19-worker-shm-oom-问题定位.md`](../../bugfix/2026-04-19-worker-shm-oom-问题定位.md)。

定位结论：**元数据已被 `objectTable_->Erase` 删除，但物理 shm 仍被 `memoryRefTable_` 钉着**（含 user-pin ref / `ShmOwner` 聚合 / Get 路径未释 ref / 被动缩容切 standby 后 client 主动 `memoryRefCount_.Clear()` 等多个原因叠加）。

### 当前可观测性缺口

1. `memoryRefTable_` **没有任何 metric** 暴露大小 / 字节量，无法证明"ref 钉着 shm"。
2. `Allocator::AllocateMemory` / `FreeMemory` 没有累计 Counter，只能通过 `OBJECT_SIZE` Gauge 间接看，**无法对账**。
3. `ShmUnit` / `ShmOwner` 构造析构无计数，无法判断 `shared_ptr` 是否被某条路径钉住。
4. master 侧 `ExpiredObjectManager::timedObj_` / `failedObjects_` 完全不可见，**无法回答 TTL 是否在跑、是否成功**。
5. client 侧 `asyncReleasePool_` 滞后 / `DecreaseReferenceCntImpl` 3 处 early return 没有计数（这是被动缩容漏 ref 的直接证据点）。

### 目标

新增 **18 条 metric**（10 worker + 6 master + 2 client），覆盖**对象元数据 / Buffer / ShmUnit 引用 / 缓存淘汰 / TTL 链路**的释放对账，让现场凭 `Metrics Summary` 时间序列直接判定以下 7 类问题：

| 想看什么 | 证据组合（≤ 3 条曲线） |
|---|---|
| 元数据已删但 shm 未释放 | `WORKER_ALLOC - FREE` 单调涨 + `WORKER_SHM_REF_TABLE_BYTES` 单调涨 + `OBJECT_COUNT` 持平 |
| TTL 完全没跑 | `MASTER_TTL_FIRE_TOTAL` delta = 0 / `MASTER_TTL_PENDING_SIZE` > 0 |
| TTL 跑了但删不掉 | `MASTER_TTL_DELETE_FAILED_TOTAL` spike + `..._RETRY_TOTAL` 持续涨 |
| TTL 队列堆积 | `MASTER_TTL_PENDING_SIZE` 持续涨 |
| Master 元数据泄漏 | `MASTER_OBJECT_META_TABLE_SIZE` 单调涨 / worker `OBJECT_COUNT` 不涨 |
| 释放靠 RemoveClient 兜底 | `WORKER_REMOVE_CLIENT_REFS_TOTAL` 持续 > 0 |
| Client 切 standby 漏 ref | `CLIENT_DEC_REF_SKIPPED_TOTAL` spike + `WORKER_REMOVE_CLIENT_REFS_TOTAL` 滞后 spike |

**关联 RFC**：[2026-04-zmq-rpc-metrics](../2026-04-zmq-rpc-metrics/README.md)（同款 metrics 框架的 ZMQ 落地实践）

---

## 设计约束（硬约束）

- **零锁、零遍历**：每条 metric 只能是 atomic op；禁止 `for` 扫表 / `map.find` / 持锁查询。
- **单点开销 ≤ 50 ns**：典型 1 次 `fetch_add(memory_order_relaxed)` ≈ 15 ns。
- **埋点位置 ≤ 12 处**：每处 ≤ 2 行新增代码。
- **不做现场快照 / dump 接口**。
- **不在采集侧做 join / 分类计算**：所有"差值 / 孤儿数"留给读图人通过曲线对照得出。
- **不修改错误码 / 不改业务路径**。

---

## 建议的方案

基于已合入的 `datasystem::metrics` 轻量级框架（PR #584）+ `KvMetricId` 枚举体系（PR #586），在枚举末尾追加 18 条 ID。

### Layer 1：Worker SHM 释放对账（10 条）

| 指标 | 类型 | 单位 | 触发时机 | 定界价值 |
|---|---|---|---|---|
| `worker_allocator_alloc_bytes_total` | Counter | bytes | `Allocator::AllocateMemory` 成功 | 物理 shm 分配速率 |
| `worker_allocator_free_bytes_total` | Counter | bytes | `Allocator::FreeMemory` 成功 | 物理 shm 释放速率（与上对账） |
| `worker_shm_unit_created_total` | Counter | count | `ShmUnit::ShmUnit()` | ShmUnit 实例化次数 |
| `worker_shm_unit_destroyed_total` | Counter | count | `~ShmUnit()` | ShmUnit 析构次数（与上对账） |
| `worker_shm_ref_add_total` | Counter | count | `AddShmUnit / AddShmUnits` | ref 入表速率 |
| `worker_shm_ref_remove_total` | Counter | count | `RemoveShmUnit` | ref 出表速率（与上对账） |
| `worker_shm_ref_table_size` | Gauge | count | **单点**：res_metric 周期 lambda 调 `shmRefTable_.size()`（TBB concurrent_hash_map O(1) lock-free） | 当前 outstanding ref 数 |
| `worker_shm_ref_table_bytes` | Gauge (atomic) | bytes | **多点**：同 add/remove ±`shmUnit->size`；`AddShmUnits` 一次累加 sum | **当前游离 shm 字节（OOM 直接信号）** |
| `worker_remove_client_refs_total` | Counter | count | `RemoveClient` 入口 | 靠 client 断连兜底回收的 ref 总数 |
| `worker_object_erase_total` | Counter | count | `ClearObject` 中 `Erase` 后 | 元数据删除速率 |

### Layer 2：Master TTL / 元数据健康度（6 条）

| 指标 | 类型 | 单位 | 触发时机 | 定界价值 |
|---|---|---|---|---|
| `master_object_meta_table_size` | Gauge | count | **单点**：master metric tick 周期调 `metaTable_.size()`（`oc_metadata_manager.h:1239`，TBB concurrent_hash_map O(1) lock-free）；不涉及 `globalRefTable_`、不涉及 etcd 派生表 | 全局元数据是否泄漏 |
| `master_ttl_pending_size` | Gauge | count | 优先单点 `timedObj_.size()`；如容器并发不安全则改 atomic 维护（`InsertObject` +1 / `Run()` 出队 -1） | TTL 待删队列长度 |
| `master_ttl_fire_total` | Counter | count | `Run()` 扫到期对象 | TTL 扫描器是否在跑 |
| `master_ttl_delete_success_total` | Counter | count | `AsyncDelete` 成功 | TTL 真删成功速率 |
| `master_ttl_delete_failed_total` | Counter | count | `AsyncDelete` 失败 | TTL 删除失败速率 |
| `master_ttl_retry_total` | Counter | count | `AddFailedObject`（指数退避重排） | TTL 重试堆积 |

### Layer 3：Client 异步释放（2 条）

| 指标 | 类型 | 单位 | 触发时机 | 定界价值 |
|---|---|---|---|---|
| `client_async_release_queue_size` | Gauge | count | 周期 lambda 读 `asyncReleasePool_->GetWaitingTasksNum()`（`thread_pool.h:158` 已存在并无锁使用） | 异步释放滞后 |
| `client_dec_ref_skipped_total` | Counter | count | `DecreaseReferenceCntImpl` 3 处 early return | 释放被静默吞掉次数（切 standby / 死 buffer / 本地 ref 未归零）|

### 定界决策树

```
shm.memUsage 持续上涨 / OOM？
      │
      ├── worker_allocator_alloc_bytes_total delta ≫ free_bytes_total delta？
      │       ├── 是 → 物理 shm 没还
      │       │       ├── worker_shm_ref_table_bytes 单调涨 → memoryRefTable_ 钉着（bugfix §3.2 d/§3.3）
      │       │       │       ├── worker_remove_client_refs_total > 0 → 靠 RemoveClient 兜底
      │       │       │       └── client_dec_ref_skipped_total spike → 切 standby 漏 ref
      │       │       └── shm_unit_created - destroyed 持续涨 + ref_table_bytes 不涨 → ShmOwner 聚合钉住（§3.2 b）
      │       └── 否 → 不是释放问题，看 master 元数据
      │
      ├── master_object_meta_table_size 单调涨？
      │       └── 同期 worker OBJECT_COUNT 持平 → master 视图未与 worker 同步
      │
      └── TTL 链路异常？
              ├── master_ttl_fire_total delta = 0 但 pending_size > 0   → 扫描器死 / MasterAsyncTask 池满
              ├── master_ttl_delete_failed_total spike                  → 删除 RPC 失败
              └── master_ttl_retry_total 持续涨                          → 退避堆积
```

---

## 涉及到的变更

### 新增文件

| 文件 | 说明 |
|---|---|
| `tests/ut/common/object_cache/shm_leak_metrics_test.cpp` | 18 条 metric 的 UT：counter 递增 / gauge 一致性 / 并发安全 / 微基准 |

### 修改文件

| 文件 | 改动说明 | 改动行数 |
|---|---|---|
| `src/datasystem/common/metrics/kv_metrics.{h,cpp}` | `KvMetricId` 末尾追加 18 个枚举 + `KV_METRIC_DESCS` 同步追加 | ~30 |
| `src/datasystem/common/shared_memory/allocator.cpp` | `AllocateMemory` / `FreeMemory` 各 1 行 `METRIC_ADD` | +2 |
| `src/datasystem/common/shared_memory/shm_unit.cpp` | `ShmUnit::ShmUnit()` / `~ShmUnit()` 各 1 行 | +2 |
| `src/datasystem/common/object_cache/object_ref_info.{h,cpp}` | `AddShmUnit` / `AddShmUnits`(批量一次累加 sum) / `RemoveShmUnit` / `RemoveClient` 加 metric；如需在外部读 size，加一个 `Size()` 公共转发 | ~8 |
| `src/datasystem/worker/object_cache/service/worker_oc_service_crud_common_api.cpp` | `ClearObject` 加 1 行 erase 计数 | +1 |
| `src/datasystem/worker/worker_oc_server.cpp` | `RegisterCollectHandler` 已有 lambda 追加 1 行 `WORKER_SHM_REF_TABLE_SIZE` Gauge.Set() | +1 |
| `src/datasystem/master/object_cache/expired_object_manager.cpp` | `InsertObject` / `Run()` 出队 / `AsyncDelete` 成功/失败 / `AddFailedObject` 加计数 | +5 |
| `src/datasystem/master/object_cache/oc_metadata_manager.cpp` | master metric tick 追加 1 行 `MASTER_OBJECT_META_TABLE_SIZE` Gauge.Set(`metaTable_.size()`) | +1 |
| `src/datasystem/client/object_cache/object_client_impl.cpp` | `DecreaseReferenceCntImpl` 3 处 + `StartMetricsThread` 周期 lambda 追加 1 行 | +4 |

**总计：~50 行新增代码、8-9 个文件**。

### 不变项

- `StatusCode` 枚举不变（不修改错误码）。
- 业务路径行为不变。
- `res_metrics.def` / `resource.log` 格式不变。
- 现有 metric 不动，仅在 `KvMetricId` 末尾追加。

---

## 测试验证

### UT（CMake）

```bash
ssh xqyun-32c32g 'cd /root/workspace/git-repos/yuanrong-datasystem/build && \
  ./tests/ut/ds_ut --gtest_filter="ShmLeakMetricsTest.*" -v'
# 期望：18 条 metric 全部 PASSED
```

### UT（Bazel）

```bash
ssh xqyun-32c32g 'cd /root/workspace/git-repos/yuanrong-datasystem && \
  USE_BAZEL_VERSION=7.4.1 bazel test \
    //tests/ut/common/object_cache:shm_leak_metrics_test \
    --jobs=8 --test_output=all'
```

### 场景验收（远端 `xqyun-32c32g`）

| 场景 | 期望证据 |
|---|---|
| **OOM 重放**（`set_usr_pin_8m` 5 分钟） | `worker_allocator_alloc_bytes_total` delta ≥ 5× `..._free_bytes_total` delta；`worker_shm_ref_table_bytes` Gauge 单调涨 |
| **TTL 健康** | `master_ttl_fire_total` ≈ `master_ttl_delete_success_total`；`pending_size` 稳定 |
| **TTL 故障**（注入 `MasterAsyncTask` 池满） | `pending_size` 持续涨 + `fire_total` 增速骤降 |
| **被动缩容**（注入 passive scale-down） | `client_dec_ref_skipped_total` spike + `worker_remove_client_refs_total` 滞后 spike |

### 微基准

参照 PR #588 的方法，对 `AddShmUnit` 路径做 1M 次循环对比 baseline：

```
期望：增加 metric 后单次开销增量 ≤ 50 ns
```

---

## 期望的反馈时间

- 建议反馈周期：**5~7 天**。
- 重点反馈：
  1. **`MASTER_TTL_PENDING_SIZE` 的容器类型**：实施前需要确认 `expired_object_manager.cpp` 中 `timedObj_` 的容器类型与并发模型 —— 如果是 TBB / 已有锁保护下的周期采集点，则单点读取 `.size()`；如果是 `std::set` 且裸调 `.size()` 有数据竞争，则改为 `InsertObject` +1 / `Run()` 出队 -1 的 atomic Gauge 维护。
  2. **三阶段实施（确认）**：阶段 1 = worker 8 条核心（`#1-#8`）；阶段 2 = worker 兜底 2 条（`#9-#10`）+ master 6 条（`#11-#16`）；阶段 3 = client 2 条（`#17-#18`）+ 决策树 / 场景表回写 docs/observable/。每阶段独立可验证、独立合入。
  3. **告警阈值**：`MASTER_TTL_PENDING_SIZE` / `WORKER_SHM_REF_TABLE_BYTES` 等 Gauge 是否在告警系统里设置默认阈值？（建议另开运维 ticket，与本 RFC 解耦）
  4. **修复 RFC 何时启动**：本 RFC 仅观测；bugfix §5 P0/P0'/P1 等修复方案需要单独 RFC，是否在本 RFC 合入后立即启动？

## 已澄清（不再需要决策）

| 历史问题 | 答案 |
|---|---|
| Master 目标表名 | **`metaTable_`**（`oc_metadata_manager.h:1239`，TBB concurrent_hash_map）；不涉及 `globalRefTable_`（gincref/gdecref）、不涉及 etcd 派生表 |
| `AddShmUnits` 批量 bytes 累加方式 | **一次累加 sum**（性能稍优，语义对外一致） |
| `ThreadPool::GetQueueSize()` 接口 | **已存在** —— 用 `GetWaitingTasksNum()`（`thread_pool.h:158`），仓库内已无锁使用 |
| Master `metaTable_` 多个 insert/erase 站点 | **不在每点埋**，改为单点周期读 `metaTable_.size()`（TBB O(1) lock-free），消除漂移风险 |
| Worker `shmRefTable_` size Gauge 维护 | 同上，单点周期读，不在 add/remove 路径维护（**bytes** Gauge 仍多点 atomic 维护，因容器不知 size） |
