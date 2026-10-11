# Meta-Affinity Worker Async Replicate — 模块设计

**Status**: Draft  
**基线**: `main/master` @ `8e09318ef`  
**实现**: `feature/meta-affinity-write` @ `3f4a93c9`  
**父 RFC**: [2026-06-29-meta-affinity-write](../2026-06-29-meta-affinity-write/design.md)

---

## 1. 背景

同节点 Client 写入时，Publish ACK 通常落在 **origin/gateway worker**，而 **metadata owner** 由 hash ring 决定。若不 async replicate，primary copy 留在 origin，读者冷 Get 需跨 worker 拉数据。

**Worker async replicate 目标**：Publish 成功 **不阻塞** replicate；后台把 **binary** 对象迁到 meta owner，再 `ReplacePrimary(remove_location=false)`，origin 保留 local copy。

---

## 2. 设计原则

| 原则 | 含义 |
|------|------|
| **Publish ACK 不等待 replicate** | 异步队列 + 线程池，与 spill/rebalance 模式一致 |
| **remove_location=false** | origin 保留 location；本地 Get 仍可命中 |
| **仅 local ≠ meta owner 时调度** | `ShouldScheduleMetaAffinityReplicate` |
| **默认关闭** | `-enable_meta_affinity_replicate=false` |
| **复用 DataMigrator** | 不新增 worker-worker migrate RPC |

---

## 3. As-Is（main/master @ 8e09318ef）

```
Client Put/Publish
  → Worker::Publish → PublishImpl → PublishObjectWithLock → PublishObject
       → RequestingToMaster → SaveBinaryObjectToMemory → NotifyPendingGetRequest
       → [结束，primary @ origin worker]
```

- master **无** `MetaAffinityReplicate*` 模块
- `DataMigrator::MigrateToTargetNode` 仅被 L2 迁移 / Rebalance 使用

---

## 4. To-Be Phase 1（feature 分支，待合入）

### 4.1 组件图

```
PublishObject success (BINARY)
  └─ ScheduleMetaAffinityReplicateIfNeeded
       └─ MetaAffinityReplicateManager (hash 分片队列, kQueueNum 线程)
            └─ MetaAffinityReplicateExecutor
                 ├─ GetMetaAddress(objectKey)
                 ├─ DataMigrator::MigrateToTargetNode → meta owner
                 ├─ ReplacePrimary(remove_location=false)
                 └─ MarkLocalCopyOnOriginWorker
```

### 4.2 模块职责

| 模块 | 路径 | 职责 |
|------|------|------|
| `MetaAffinityReplicateManager` | `worker/object_cache/` | 任务队列、hash 分片、线程池 |
| `MetaAffinityReplicateExecutor` | `worker/object_cache/` | 调度条件、迁移、ReplacePrimary、origin 标记 |
| `WorkerOcServicePublishImpl` | `worker/service/` | ctor 初始化 Manager；`PublishObject` 末尾调度 |
| gflag | worker | `-enable_meta_affinity_replicate`（默认 false） |

### 4.3 调度条件

```cpp
bool ShouldScheduleMetaAffinityReplicate(localAddress, metaWorkerAddress):
  return enable_meta_affinity_replicate
      && !localAddress.empty()
      && !metaWorkerAddress.empty()
      && localAddress != metaWorkerAddress

ScheduleMetaAffinityReplicateIfNeeded:
  + DataFormat == BINARY   // P2 扩展 HETERO
```

### 4.4 与现有迁移子系统关系

| 能力 | Rebalance / Spill | Meta-Affinity Replicate |
|------|-------------------|-------------------------|
| 触发 | Master rebalance / 容量 | Publish 成功后本地调度 |
| Migrator type | SPILL / SCALE_DOWN 等 | 当前用 `SCALE_DOWN`（P1.5 建议新增 `META_AFFINITY` enum） |
| ReplacePrimary | 是 | 是，`remove_location=false` |
| 并发控制 | RebalanceExecutor 全局 | Per-object hash 队列 |

---

## 5. To-Be Phase 1.5（生产化，本 RFC 新增）

### 5.1 可观测性

新增 metric（建议 `common/metrics/kv_metrics`）：

| Metric | 类型 | 说明 |
|--------|------|------|
| `meta_affinity_replicate_scheduled_total` | Counter | 入队次数 |
| `meta_affinity_replicate_success_total` | Counter | migrate + ReplacePrimary 成功 |
| `meta_affinity_replicate_failure_total` | Counter | 按阶段 label：`migrate` / `replace_primary` |
| `meta_affinity_replicate_latency_us` | Histogram | 端到端 async 延迟 |
| `meta_affinity_replicate_queue_depth` | Gauge | 各分片队列深度 |

挂点：`ScheduleMetaAffinityReplicateIfNeeded`（scheduled）、`ExecuteMetaAffinityReplicate` 出口（success/fail/latency）。

### 5.2 Graceful Shutdown

**问题**：`MetaAffinityReplicateManager::Stop()` 仅在析构调用；worker 关停时 PublishImpl 可能仍有 in-flight replicate。

**方案**：

1. `WorkerOCServer::Stop()`（或等价 teardown）显式调用 `GetPublishImpl()->StopMetaAffinityReplicate()`
2. `Stop()`：置 `running_=false` → **drain 队列**（带 timeout，如 30s）→ join 线程
3. drain 超时：记 `failure_total{reason=shutdown_drain}`，打 WARNING

### 5.3 失败策略

**现状**：失败 log-only，无重试队列。

**Phase 1.5 最小方案**：

- 失败 increment metric + structured log（objectKey, metaOwner, stage, status）
- **不**做无限重试（避免与 rebalance 冲突）
- 可选：`K_TRY_AGAIN` 类错误 **有限重入队**（max 1，delay 100ms）— 仅 UT/ST 验证后再开

### 5.4 MigrateType 语义

新增 `MigrateType::META_AFFINITY`（或 `COLOCATE_PRIMARY`）：

- `DataMigrator` 日志/指标可区分 meta-affinity vs scale-down
- 不改变 migrate handler 行为（仍走 `MigrateDataToRemote`）

---

## 6. To-Be Phase 2（非 BINARY，Deferred）

| DataFormat | 策略 |
|------------|------|
| `BINARY` | Phase 1 已覆盖 |
| `HASH_MAP` | **不 replicate**（hset 语义） |
| `HETERO` | 需评估 `device` migrate handler；可能需 device-aware `MigrateDataToRemote` |

挂接点不变：仍在 `ScheduleMetaAffinityReplicateIfNeeded` 扩展 format 分支。

---

## 7. Flags

| Flag | 默认 | Scope |
|------|------|-------|
| `enable_meta_affinity_replicate` | false | Worker：async replicate 总开关 |

Client 直写门控同 flag，见父 RFC；Worker-only 场景可只开 worker flag。

---

## 8. 测试计划

| 层级 | 用例 | 验收 |
|------|------|------|
| UT | 现有 ×4 + `StopDrainsQueue` + `FailureIncrementsMetric` | Bazel `MetaAffinityReplicateTest` |
| ST | 现有 colocate ST + `ReplicateFailureInject` | primary 保持 origin；metric/log 可见 |
| ST | `ShmPublishColocate` | SHM binary 对象 replicate 成功 |
| Perf | 已有 Get RPC benchmark | 非 replicate 延迟回归 |

---

## 9. 不在范围

- Client remote-only 直写 meta owner（父 RFC P1，Client 侧）
- `MetaAffinityClientRingSource` 与 `ClientHashRingSource` 统一（Issue #7）
- 同节点 client 直写省 replicate（Issue #6，Phase 2）
