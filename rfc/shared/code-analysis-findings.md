# 代码分析关键发现

## 一、已有基础设施 (可直接复用)

### 1.1 Slot Recovery 框架 ✅

```
src/datasystem/worker/object_cache/slot_recovery_orchestrator.h
src/datasystem/worker/object_cache/slot_recovery/slot_recovery_manager.h
src/datasystem/worker/object_cache/metadata_recovery_manager.h
src/datasystem/protos/slot_recovery.pb.h
```

**发现**: Worker 已有完整的 Slot Recovery 协调器！`SlotRecoveryOrchestrator` 负责协调 worker-local slot 启动恢复。
- 已有 `SlotRecoveryIncidentState::IsTaskTerminal()` 终端状态判断
- **RFC1 Checkpoint 可在此框架上扩展**：新增 `CheckpointBasedRecovery` 策略

### 1.2 RocksDB 持久化基础 ✅ (但 RFC1 不直接用)

```
src/datasystem/common/kvstore/rocksdb/replica.cpp:71:
    checkpointPath_ = replicaDBRootPath + "/checkpoint_" + dbName;
    src/datasystem/common/kvstore/rocksdb/replica.cpp:30:
    #include "rocksdb/utilities/checkpoint.h"
```

**发现**: RocksDB Replica 已有 Checkpoint 机制。
- ⚠️ **但 RFC1 不直接使用 RocksDB Checkpoint**: 太重量级，迭代慢，不需要 KV 查询
- ✅ **改用直接文件序列化** (参考 Mooncake SHM 快照):
  - StateSnapshot protobuf → write() + fdatasync → 本地 NVMe
  - 恢复时 read() + protobuf 反序列化 → 直接加载到内存
  - 文件大小 < 500MB (仅 metadata)，NVMe 读 < 200ms
  - RocksDB 仅保留现有写入路径，不做额外操作

### 1.3 URMA Jetty 动态加载 ✅

```
src/datasystem/common/rdma/urma_dlopen_util.cpp:
    urma_jetty_t *ds_urma_create_jetty(context, config)
    urma_status_t ds_urma_delete_jetty(jetty)
    urma_status_t ds_urma_modify_jetty(jetty, attr)
    urma_target_jetty_t *ds_urma_import_jetty(context, remote_jetty, ...)
```

**发现**: URMA Jetty API 已通过 dlopen 动态加载！`create/delete/modify/import` 全部可用。
- **RFC4 JettyManager 可以直接调用这些接口**，无需重复造轮子
- Jetty 复用策略需要通过 `urma_jetty_cfg_t` 和 `urma_jetty_attr_t` 实现

### 1.4 NUMA 亲和性 ✅

```
src/datasystem/common/rdma/fast_transport_base.cpp:112:
    FLAGS_enable_ub_numa_affinity  (已存在!)

src/datasystem/common/rdma/urma_manager.cpp:
    sched_getcpu() 被广泛使用  (确认 CPU 位置)

src/datasystem/common/util/numa_util.h   (NUMA 工具库已存在)

src/datasystem/common/shared_memory/allocator.h:
    AllocateMemory(tenantId, needSize, ..., numaId, ...)  (内存分配支持 NUMA ID)
```

**发现**: NUMA 亲和性基础设施已基本完成！`enable_ub_numa_affinity` flag 已存在，`sched_getcpu()` 在 URMA 层广泛使用。
- **RFC2 NUMA 亲和写入可直接复用这些机制**

### 1.5 HCCS 链路识别 ✅

```
src/datasystem/common/device/ascend/p2phccl_types.h:48:  P2P_LINK_HCCS
src/datasystem/common/device/ascend/p2phccl_comm_wrapper.cpp:64:
    LOG(INFO) << "InitP2PComm HCCS dir: " << static_cast<int>(kind);
    P2pLinkBase::HCCS
```

**发现**: HCCS 链路类型已定义为 `P2P_LINK_HCCS`，P2P 通信层已知 HCCS。
- **RFC2 的 "不跨 HCCS" 约束可以通过检测 P2P_LINK_HCCS 来实现**

---

## 二、缺失的能力 (需要新增)

### 2.1 Worker Checkpoint (RFC1)

| 项 | 现状 | 需要新增 |
|----|------|---------|
| Slot Recovery | ✅ SlotRecoveryOrchestrator | 新增 Checkpoint 策略 |
| RocksDB Snapshot | ✅ rocksdb::Checkpoint | 封装为 CheckpointManager |
| 状态机 UPGRADING | ❌ 不存在 | HashRing 新增状态 |
| 升级编排 | ❌ 不存在 | Master 端升级逻辑 |

### 2.2 多副本 (RFC2+3)

| 项 | 现状 | 需要新增 |
|----|------|---------|
| HashRing 分布 | ✅ Token-based | 新增反亲和参数 (rack/zone) |
| RocksDB Replica | ✅ WAL-based PSync | Worker-level 副本 |
| NUMA 识别 | ✅ sched_getcpu | GetReplicaTargets 加 numa 偏好 |
| 副本切换 | ❌ 不存在 | ReplicaManager::PromoteToPrimary |

### 2.3 连接池 (RFC4)

| 项 | 现状 | 需要新增 |
|----|------|---------|
| ZMQ socket | ✅ zmq_stub_impl | 新增连接池封装 |
| URMA QP | ✅ 直接创建 | 新增 QP 池化 |
| URMA Jetty | ✅ dlopen API | 新增 JettyManager 分配策略 |
| 预热机制 | ❌ 不存在 | 新增 Prewarm API |

---

## 三、代码行数估算

| 模块 | 文件 | 当前行数 | 新增行数 |
|------|------|:--:|:--:|
| worker_oc_server.cpp | Worker 主流程 | ~500 | +150 |
| replica.cpp | RocksDB 副本 | ~400 | 复用 |
| urma_manager.cpp | URMA 管理 | ~1700 | +200 |
| zmq_stub_impl.cpp | ZMQ 桩 | ~800 | +100 |
| hash_ring.h | Hash Ring | ~300 | +30 |
| kv_metrics.cpp | 指标 | ~200 | +100 |

---

## 四、总结

**好消息**：太多基础设施已经存在！
- Checkpoint → RocksDB 已有，只需封装
- Jetty → URMA API 已有，只需管理策略
- NUMA → 全部已在用，只需接入副本选择
- Slot Recovery → 框架已有，只需新增策略

**需要从零写的核心逻辑**：
1. ReplicaManager（副本生命周期）— 完全新增
2. ZMQConnectionPool — 完全新增
3. 升级编排 — Master 端新逻辑
4. Replica 放置算法 — 反亲和 + NUMA 偏好
