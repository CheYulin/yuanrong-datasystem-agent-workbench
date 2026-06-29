# Meta-Affinity Write — 模块设计

**Status**: In-Progress  
**Branch**: `feature/meta-affinity-write` · **HEAD**: `0e644bc4`  
**MR**: [!1151](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1151)  
**Related**: [client-direct-read design](../2026-06-21-client-direct-read-flow/design.md)

---

## 1. 背景与问题

Object 的 **metadata owner**（hash 路由）与 **primary copy** 常落在不同 worker：

- 同节点 Client 经 gateway worker Publish → primary 在 origin → 需异步 replicate 才 colocate
- 跨节点 Client 无 local worker → 写经 gateway → replicate → 读者 Get 多 hop
- 读者 `SelectObjectLocation` 若不优先 primary，可能先命中非 primary location

**目标**：在 `-enable_meta_affinity_replicate=true` 时，让 primary 尽早与 meta owner 对齐，且 **不破坏** origin local copy 语义。

---

## 2. 设计原则

| 原则 | 含义 |
|------|------|
| **Publish ACK 不阻塞 replicate** | 同节点路径异步队列 + `DataMigrator`，与现有 spill/replicate 模式一致 |
| **remove_location=false** | replicate 后 origin 保留 local copy；读本地 hit 仍可走 origin |
| **写门控与读门控对称** | remote-only 直写条件：`enable_meta_affinity_replicate && enable_distributed_master && !HasHealthyLocalWorker()` |
| **默认关闭** | gflag 默认 false；关闭时与现网行为一致 |
| **Ring 只读共享** | Client 侧复用 `ReadOnlyHashRingView`；不引入 worker 依赖到 client common |

---

## 3. 组件地图

### 3.1 Worker 侧

```
PublishObject (binary) success
  └─ ScheduleMetaAffinityReplicateIfNeeded
       └─ MetaAffinityReplicateManager (async queue, hash 分片)
            └─ MetaAffinityReplicateExecutor
                 ├─ ShouldScheduleMetaAffinityReplicate (local != meta owner)
                 ├─ DataMigrator::MigrateData → meta owner worker
                 └─ ReplacePrimary(remove_location=false)
```

| 模块 | 路径 | 职责 |
|------|------|------|
| `MetaAffinityReplicateManager` | worker/object_cache | 异步任务队列、线程池 |
| `MetaAffinityReplicateExecutor` | worker/object_cache | 调度条件、迁移、ReplacePrimary |
| `WorkerOcServicePublishImpl` | worker/service | Publish 成功后挂接调度 |
| gflag | worker | `-enable_meta_affinity_replicate`（默认 false） |

### 3.2 Client 侧

```
Create / Put / Publish / Seal
  └─ GetWriteWorkerApi(objectKey)
       ├─ ShouldRouteWriteToMetaOwner? → GetAvailableWorkerApi (legacy)
       └─ MetaAffinityClientRingSource
            ├─ BootstrapRing (etcd + gateway GetClusterState)
            ├─ ReadOnlyHashRingView::GetMetaAddress
            └─ ClientWorkerRemoteApi → meta owner worker
```

| 模块 | 路径 | 职责 |
|------|------|------|
| `ReadOnlyHashRingView` | common/object_cache | hash → meta owner 只读计算 |
| `MetaAffinityClientRingSource` | client/object_cache/meta_affinity | ring 刷新策略、Bootstrap |
| `ObjectClientImpl::GetWriteWorkerApi` | client | 写路径 worker 选择 |
| `cluster_master_flags.cpp` | common/util/gflag | `master_address` / `enable_distributed_master` 共享定义 |

### 3.3 Master 读侧

| 改动 | 说明 |
|------|------|
| `SelectObjectLocation` | 多 location 时 **优先 primary**；无 primary 时 fallback 原逻辑 |
| 本地 copy | origin worker 保留 location；本地 Get hit 不变 |

---

## 4. 行为矩阵

| Client 场景 | 写路径 | primary @ Put 返回 | 后续冷 Get |
|-------------|--------|-------------------|------------|
| 同节点，meta=本地 worker | gateway/local Publish | 本地（若 meta=local） | 本地 / primary |
| 同节点，meta=远端 worker | local Publish → **async replicate** | origin worker | replicate 完成后 primary@meta owner |
| 同节点，remote-only 直写（flag） | **直写 meta owner** | **meta owner** | 读者少 hop |
| 跨节点，legacy | gateway → replicate | origin @ gateway | 跨 worker Get |
| 跨节点，meta-affinity | **直写 meta owner** | **meta owner** | ~75% Get RPC 降低（4KB 实测） |

---

## 5. Flags 与接口

| Flag | 默认 | 说明 |
|------|------|------|
| `enable_meta_affinity_replicate` | false | Worker：async replicate；Client：remote-only 直写门控 |
| `enable_distributed_master` | true | Client 直写前置条件（已有 flag，定义于 `cluster_master_flags.cpp`） |

**对外 SDK/API**：无签名变更；仅新增 worker gflag。

---

## 6. 与 Client Direct Read 的边界

| 项 | 现状 | 目标（Deferred） |
|----|------|------------------|
| `ReadOnlyHashRingView` | **已共享** | 保持 |
| Ring source | `MetaAffinityClientRingSource` vs `ClientHashRingSource` | **合并为单一 Client ring source** |
| 刷新策略 | 写路径 `RefreshForRouteLookup` | 与 [hash-ring-refresh-policy](../2026-06-21-client-direct-read-flow/hash-ring-refresh-policy.md) 对齐 |
| 读+写同开 | 可独立 flag | 文档化组合场景 ST |

---

## 7. 测试覆盖

| 类型 | 用例 | 覆盖点 |
|------|------|--------|
| UT | `MetaAffinityReplicateTest.*` ×4 | 调度条件、gflag、队列执行 |
| ST | `ColocatePrimaryWithMetaOwnerAndReadLocalCopy` | replicate、双 location、Invalidate 后读 primary |
| ST | `RemoteOnlyClientPutDirectlyOnMetaOwner` | 无 local worker 直写、primary 立即在 meta owner |
| ST perf | `GetRpcReduction*Benchmark` | 4KB Get RPC 门禁（同节点/跨节点） |

---

## 8. Deferred / 已知限制

1. **仅 binary Publish 挂 replicate**：`PublishBinaryObject` 成功路径；Shm/其他 Publish 变体未挂接
2. **同节点有 local worker 仍走 async replicate**：未做 client 直写 meta owner 优化（Phase 2）
3. **Scale 后 changed_ranges ST**：未覆盖 worker 扩缩容后写路由
4. **check_code**：OpenLibing 编码规范项待 CI 绿后逐项关闭（见 [issue-rfc.md](./issue-rfc.md)）
