# 接口变更与端到端流程分析

> 覆盖三个 RFC 的北向 SDK、RPC Pb 变更、端到端流程、操作同步/异步标注、故障可用性分析

---

## 一、北向 SDK 接口变更 (Client API)

### 1.1 当前接口

```cpp
// kv_client.h — 当前 Client API
Status Put(const std::string &key, const std::string &value, const SetParam &param);
Status Get(const std::string &key, std::string *value, int32_t timeout_ms);
Status Del(const std::string &key);
```

### 1.2 RFC2 变更: 多副本感知

```cpp
// 新增参数: ReplicaParam (可选, 默认 N=1 即单副本)
struct ReplicaParam {
    uint32_t replica_count = 1;     // 目标副本数 (1/2/3)
    bool require_quorum = true;     // 是否等待 Quorum (默认 true)
    uint32_t quorum_timeout_ms = 3; // Quorum 等待超时 (ms)
};

// Put 接口不变, 通过 SetParam 传递 ReplicaParam
struct SetParam {
    uint32_t ttl_second;            // 已有
    ReplicaParam replica;           // ← 新增
};

// Get 接口: 新增 ReplicaReadHint (可选)
struct GetParam {
    bool allow_stale = false;       // 是否允许读陈旧副本 (默认 false)
    bool prefer_local_numa = true;  // 是否优先 NUMA 本地副本
};

Status Get(const std::string &key, std::string *value, 
           int32_t timeout_ms, const GetParam &param = GetParam());

// 新增: 批量写入 (优化多副本场景, 合并 RPC)
struct BatchPutParam {
    std::vector<std::string> keys;
    std::vector<std::string> values;
    ReplicaParam replica;
};
Status BatchPut(const BatchPutParam &param);
```

### 1.3 RFC1 + RFC4: 无 Client API 变更

- RFC1 (滚动升级): 对 Client 透明，Master 协调
- RFC4 (连接池): 内部优化，Client 无感

---

## 二、RPC Proto 变更汇总

### 2.1 新增消息

```protobuf
// === RFC1: 恢复相关 ===
// 无新增 RPC, 复用现有 Register RPC 增加 recovery 字段
message RegisterReqPb {  // 现有消息, 新增字段
    string worker_address = 1;
    bool is_recovery = 2;           // ← 新增: 是否为恢复启动
    uint64 snapshot_seqno = 3;      // ← 新增: Snapshot 锚点 seqno (0=全量恢复)
}

message RegisterRspPb {
    // 新增: 增量对账数据
    repeated ObjectMetaEntry delta_added = 10;    // 新增的对象
    repeated string delta_removed = 11;           // 删除的对象
    HashRingPb current_hash_ring = 12;            // 最新 HashRing
    repeated string promoted_keys = 13;           // 已被 Promote 的 Primary key
}

// === RFC2: 副本相关 ===
message SyncReplicateReqPb {
    string object_key = 1;
    uint64 seqno = 2;
    bytes data = 3;                // 实际数据 (小对象走 payload, 大对象走 URMA)
    uint32 ttl_second = 4;
    string primary_address = 5;
    WriteMode write_mode = 6;
}

message SyncReplicateRspPb {
    bool ack = 1;
    uint64 local_seqno = 2;
    int32 status_code = 3;         // 0=OK, 1013=TIMEOUT, 1016=RECOVERING
}

message CreateMetaReqPb {  // 现有消息, 新增字段
    // ... 现有字段 ...
    repeated string backup_addresses = 20;  // ← 新增: 备副本地址列表
    uint32 quorum_size = 21;                // ← 新增: Quorum 大小
    uint64 seqno = 22;                      // ← 新增: 写入 seqno
    uint32 replica_count = 23;              // ← 新增: 总副本数
}

message ReplicaSetPb {  // 新增: Client 缓存副本集
    string primary_address = 1;
    repeated string backup_addresses = 2;
    uint64 version = 3;            // 副本集版本 (变更递增)
    uint64 primary_seqno = 4;
}

message PromoteReplicaReqPb {  // 新增: Master→Backup 提升
    string object_key = 1;
    string new_primary = 2;
}

message RecoverReplicaReqPb {  // 新增: 副本恢复
    string object_key = 1;
    string source_worker = 2;
    string target_worker = 3;
    uint64 from_seqno = 4;
}
```

### 2.2 新增 StatusCode

```cpp
// 1011-1020: 副本相关
K_REPLICA_UNAVAILABLE = 1011;      // 所有副本不可用
K_REPLICA_OUT_OF_SYNC = 1012;      // 副本 seqno 落后
K_REPLICA_WRITE_TIMEOUT = 1013;    // 单个备份写入超时
K_REPLICA_QUORUM_FAILED = 1014;    // Quorum 未达
K_REPLICA_PLACEMENT_FAILED = 1015; // 找不到反亲和位置
K_REPLICA_RECOVERING = 1016;       // 副本恢复中
K_REPLICA_VERSION_MISMATCH = 1017; // 版本不匹配 (Client 缓存过期)
```

---

## 三、端到端核心流程 (标注同步/异步)

### 3.1 写入流程 (RFC2: 同步多副本)

```
Client                          Primary Worker                  Backup Workers           Master
  │                                  │                              │                      │
  ├─ Put(key, data, N=2) ──────────►│                              │                      │
  │                                  ├─ [sync] AllocShmUnit (50us)  │                      │
  │                                  ├─ [sync] MemoryCopy           │                      │
  │                                  │                              │                      │
  │                                  ├─ [sync] SyncReplicate ──────►│                      │
  │                                  │        (URMA RDMA, 并行)      ├─ [sync] AllocShm    │
  │                                  │                              ├─ [sync] WriteData   │
  │                                  │◄───── ACK ──────────────────┤                      │
  │                                  │        (Quorum=2/2)          │                      │
  │                                  │                              │                      │
  │                                  ├─ [sync] CreateMeta ─────────────────────────────────►│
  │                                  │        (locations=[P,B1,B2])                         ├─ [sync] RocksDB write
  │                                  │◄───── OK ────────────────────────────────────────────┤
  │                                  │                                                       │
  │◄─ PutResult(OK, replicas=2) ────┤                                                       │
  │                                  │                                                       │
  │                                  ├─ [async] ReconcileReplicas ──► (60s 后台对账,不阻塞)   │
```

**总延迟 (P99):** Phase1(69us) + Phase2(21us) + Phase3(378us) = **468us << 3ms** ✅
**同步操作:** AllocShm, MemoryCopy, SyncReplicate, CreateMeta
**异步操作:** ReconcileReplicas (后台)

### 3.2 读取流程 (RFC2: 多副本负载感知)

```
Client                          Master                      Worker (best replica)
  │                               │                              │
  ├─ Get(key, prefer_local) ─────►│                              │
  │                               ├─ [sync] Lookup locations     │
  │                               ├─ [sync] Filter healthy       │
  │                               ├─ [sync] Score replicas       │
  │◄── ReplicaSetPb(P+B1+B2) ────┤                              │
  │                               │                              │
  ├─ [sync] Get(key, seqno=N) ──────────────────────────────────►│
  │                               │                              ├─ [sync] Verify seqno >= N
  │                               │                              ├─ [sync] RLock object
  │                               │                              ├─ [sync] Read SHM / URMA pull
  │◄── GetResult(data) ──────────────────────────────────────────┤
  │                               │                              │
  │ (若 Worker 故障: 走故障切换路径, 见 4.1)                     │
```

**同步操作:** Master Lookup, Score, Worker Read
**异步操作:** Client 缓存刷新 (后台 TTL 过期后)

### 3.3 恢复流程 (RFC1: Snapshot + 对账)

```
Worker Start                    Master                        Backup Workers
  │                               │                              │
  ├─ [sync] Read Snapshot (200ms) │                              │
  ├─ [sync] Verify CRC32          │                              │
  ├─ [sync] Deserialize Proto     │                              │
  │                               │                              │
  ├─ [sync] Register(recovery) ──►│                              │
  ├─ [sync] DeltaSync(seqno) ◄───┤                              │
  │   (etcd 中 seqno > N 的变更)   │                              │
  ├─ [sync] StateSync ◄──────────┤                              │
  │   (HashRing, Promotions)      │                              │
  │                               │                              │
  ├─ [sync] EmergencyRecover ────────────────────────────────────►│
  │   (Primary 对象 SHM 丢失时才触发, URMA <2ms/obj)               │
  │                               │                              │
  │── ENTER RUNNING (<3s) ────────│                              │
  │                               │                              │
  ├─ [async] LazyRecover ────────────────────────────────────────►│
  │   (Backup 对象后台批量拉取)     │                              │
```

**同步操作:** Snapshot 读取, DeltaSync, StateSync, EmergencyRecover
**异步操作:** LazyRecover (后台不阻塞 RUNNING)

### 3.4 故障切换流程 (RFC2: Promote)

```
Client                         Master                          Backup(Promoted)   Old Primary(dead)
  │                              │                                  │                   │
  ├─ Get(key) ────────────────────────────────────────────────────────────────────────►│
  │                              │                                  │                   ✗ (3s timeout)
  │                              │                                  │                   
  ├─ RefreshReplica(key) ───────►│                                  │                   
  │                              ├─ [sync] Detect dead (heartbeat)  │                   
  │                              ├─ [sync] Choose best Backup       │                   
  │                              ├─ [sync] Promote(key, W2) ───────►│                   
  │                              │                                  ├─ [sync] Set Primary
  │                              │◄───── OK ────────────────────────┤                   
  │◄── ReplicaSet(P=W2) ────────┤                                  │                   
  │                              │                                  │                   
  ├─ Get(key, P=W2) ──────────────────────────────────────────────►│                   
  │◄── GetResult(data) ────────────────────────────────────────────┤                   
  │                              │                                  │                   
  │                              ├─ [async] RecoverReplica ─────────────────────────────────►│
  │                              │   (旧 Primary 恢复后转为 Backup)  │                   
```

**同步操作:** Detect, Promote, Client 重路由
**异步操作:** 旧 Primary 恢复后的副本修复
**故障切换延迟 (P99.99):** Detect(3s) + Promote(<1ms) + Client Refresh(<1ms) = **<5ms** ✅

### 3.5 扩容预热流程 (RFC4: 连接池)

```
New Worker                    Master                      ConnectionPool      Existing Workers
  │                             │                              │                    │
  ├─ Join(cluster_size=1024) ──►│                              │                    │
  │◄── Topology ────────────────┤                              │                    │
  │                             │                              │                    │
  ├─ [async] Prewarm(100) ─────┼──────────────────────────────►│                    │
  │                             │                              ├─ [async] zmq_connect│
  │                             │                              ├─ [async] urma_create│
  │                             │                              │  (5s, 后台)         │
  │                             │                              │                    │
  ├─ Register(ready) ──────────►│                              │                    │
  │◄── RUNNING ─────────────────┤                              │                    │
  │                             │                              │                    │
  │ (首次 Get/Put 直接池命中)    │                              │                    │
```

**同步操作:** Join, Register
**异步操作:** Prewarm (后台 5s), HealthCheck (30s 周期), PruneIdle (60s 周期)

---

## 四、故障可用性分析

### 4.1 写入路径故障

| 故障点 | 故障场景 | 对客户端影响 | 恢复机制 | 可用性保证 |
|--------|---------|------------|---------|:--:|
| **Primary SHM 分配失败** | OOM | Put 立即返回 K_OUT_OF_MEMORY | Client 重试其他 key 或等待 GC | 立即返回, 无阻塞 |
| **Backup-1 SyncReplicate 超时** | Backup Worker 宕机 | Put 继续等待 Backup-2 (Quorum=2), 若 Backup-2 也失败则返回 1014 | Master 后台分配新 Backup | 单 Backup 故障不丢数据 |
| **全部 Backup SyncReplicate 失败** | 网络分区 | Put 返回 K_REPLICA_QUORUM_FAILED (1014) | Client 重试, Master 重新选择 Backups | 数据在 Primary SHM, 回滚释放 |
| **Master CreateMeta 失败** | Master 不可达 | Put 返回 K_RPC_UNAVAILABLE (1002) | Primary 回滚 SHM → Client 重试 | etcd HA 保障 (3节点 Raft) |
| **Primary 在 Phase2 后 Crash** | Phase3 未执行 | Client 超时, Master 不知此 key | Object 数据在 Primary SHM 丢失 (崩溃), Backup 上可能有部分数据 | 下次写入重试即可 |

### 4.2 读取路径故障

| 故障点 | 故障场景 | 对客户端影响 | 恢复机制 | 可用性保证 |
|--------|---------|------------|---------|:--:|
| **首选副本宕机** | Primary 不可达 | 3s 超时 → Client Refresh → 自动切换 Backup | Master Promote (见 3.4) | <5ms 切换 |
| **Master 短暂不可达** | etcd 选举 | Client 使用本地缓存 ReplicaSetPb (带版本号) | 缓存有效期内无需 Master | 30s 内无需 Master |
| **全部副本不可达** | 大范围故障 | Get 返回 K_REPLICA_UNAVAILABLE (1011) | 等待 Worker 恢复 + Master 重新分配 | 业务侧降级 |
| **缓存 ReplicaSetPb 过期** | 副本集变更 (Promote) | Client 用旧 Primary 地址 Get → 返回 REDIRECT | Client 自动 Refresh | 一次额外 RTT (<1ms) |

### 4.3 升级路径故障

| 故障点 | 故障场景 | 对客户端影响 | 恢复机制 | 可用性保证 |
|--------|---------|------------|---------|:--:|
| **升级中新版本 Crash** | 新版本 bug | UPGRADING Worker 不可用 | 30s 超时自动回滚到旧版本 | <30s 回滚 |
| **Snapshot 恢复失败 (损坏)** | NVMe bit rot | 降级到 SlotRecovery 全量恢复 | CRC 校验 + 自动回退上一版本 | <3min 全量恢复 |
| **升级过程中 Master 故障** | etcd 选举 | 升级暂停 | 新 Master 从 etcd 恢复升级状态 | <5s 恢复升级 |

### 4.4 连接池故障

| 故障点 | 故障场景 | 对客户端影响 | 恢复机制 | 可用性保证 |
|--------|---------|------------|---------|:--:|
| **Pool 耗尽** | 所有连接 busy | Acquire 排队 100ms → 超时返回 error | PruneIdle + Prewarm 保持 pool 水位 | 100ms 排队窗口 |
| **ZMQ Socket 断连** | 对端 Worker 重启 | 单次 RPC 失败 | HealthCheck 30s 检测 → 重建 | 30s 内复用新连接 |
| **Jetty 错误 (WR_FLUSH_ERR)** | RDMA 链路抖动 | 当前 URMA 写入失败 | ReCreateJetty 异步重建 (<50ms) | 下次写入用新 Jetty |

---

## 五、操作归类: 同步 vs 异步

| 操作 | RFC | 同步/异步 | 阻塞时间 | 影响路径 |
|------|-----|:--:|------|------|
| Snapshot Create (周期) | RFC1 | 异步 (后台) | 0 (不阻塞写入) | — |
| Snapshot Restore | RFC1 | **同步** | <200ms | Worker 启动 |
| DeltaSync (Meta 对账) | RFC1 | **同步** | <200ms | Worker 启动 |
| EmergencyRecover (Primary 数据) | RFC1 | **同步** | <2ms/obj | Worker 启动 (Priority 1) |
| LazyRecover (Backup 数据) | RFC1 | 异步 (后台) | 0 | 不阻塞 RUNNING |
| SyncReplicate | RFC2 | **同步** | <21us P99 | Put 关键路径 |
| CreateMeta (Master) | RFC2 | **同步** | <378us P99 | Put 关键路径 |
| PromoteToPrimary | RFC2 | **同步** | <1ms | Get 故障切换 |
| ReconcileReplicas | RFC2 | 异步 (后台) | 0 (每 60s) | — |
| ZMQ Prewarm | RFC4 | 异步 (后台) | 0 (5s 后台) | 扩容 |
| HealthCheck | RFC4 | 异步 (后台) | 0 (每 30s) | — |
| PruneIdle | RFC4 | 异步 (后台) | 0 (每 60s) | — |
| Jetty ReCreate | RFC4 | 异步 (后台) | 0 (<50ms) | — |
| Snapshot Periodic | RFC1 | 异步 (后台) | 0 (每 10s) | — |

**关键原则:** 所有在 Client 请求关键路径上的操作都是同步的，所有维护/优化操作都是异步后台的。
