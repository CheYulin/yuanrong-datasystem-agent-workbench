# 数据多副本 — 可靠性 + 性能设计

## 一、需求拆解规格

### 1.1 需求分解

| 子需求 ID | 名称 | 描述 | 优先级 |
|-----------|------|------|:--:|
| MR-01 | 写入多副本 | KV Put 时同步写入 N 个副本 (N=2/3) | P0 |
| MR-02 | 副本反亲和 | 副本分布在不同节点/机架/可用区 | P0 |
| MR-03 | 主备故障切换 | 主副本故障时备副本接管，P99.99 < 5ms | P0 |
| MR-04 | 数据恢复 | 备副本故障后从主副本恢复，不阻塞读写 | P1 |
| MR-05 | 一致性同步老化 | 主备副本 TTL 同步，到期一起淘汰 | P1 |
| MR-06 | 均衡读取 | 从主/备副本选择负载最低者读取 | P0 |
| MR-07 | NUMA 亲和写入 | 写入时优先选择 NUMA 本地节点 | P0 |
| MR-08 | 跨版本兼容 | 主备副本不同版本间可互读 (滚动升级场景) | P1 |

### 1.2 定量指标

| 指标 | 当前 | 目标 |
|------|------|------|
| 故障切换 P99.99 | 分钟级 (Client 重试) | **< 5ms** |
| 8MB KV Get P99.99 | ~10ms (单 Worker) | **< 5ms** (备副本分担) |
| 写入 P99 (副本同步) | ~1ms (单写) | **< 3ms** (主写 + 备异步确认) |
| QPS 提升 (1写10读) | 基准 100% | **+30%** |
| 副本数 | 1 | 2 (默认) |
| 数据丢失窗口 | 分钟级 | 0 (同步写入) |

### 1.3 规格约束

- **最大副本数**: 3 (主 + 2 备)
- **适用场景**: KV 8MB 对象、精排 Decode 1 写 10 读
- **NUMA 亲和**: 写请求路由到同 NUMA node Worker
- **跨 NUMA 容忍**: 不跨 HCCS (Huawei Cache Coherent System)

---

## 二、概念模型

### 2.1 副本模型

```
Object (name + hash_key)
├── PrimaryReplica    — leader copy, 写入入口
│   └── 所在 Worker:  hash_ring.GetNode(key, 0)
├── BackupReplica[0]  — 备副本 1, hash_ring.GetNode(key, 1)
└── BackupReplica[1]  — 备副本 2 (N=3时), hash_ring.GetNode(key, 2)
```

```mermaid
flowchart TB
    subgraph Client
        C[KV Client]
    end
    
    subgraph Node1 [NUMA Node 0]
        W1[Worker A<br/>Primary]
    end
    
    subgraph Node2 [NUMA Node 0]
        W2[Worker B<br/>Backup 1]
    end
    
    subgraph Node3 [Different Rack]
        W3[Worker C<br/>Backup 2]
    end
    
    C -->|1. Put (sync)| W1
    W1 -->|2. Replicate (async)| W2
    W1 -->|3. Replicate (async)| W3
    C -->|4. Get (load-aware)| W2
```

### 2.2 新增核心对象

**ReplicaManager** — 副本生命周期管理：

```
ReplicaManager (per Worker):
  - CreateReplicas(key, value, config) → Status
  - GetBestReplica(key) → ReplicaInfo (nearest/least-loaded)
  - PromoteToPrimary(key) → Status (failover)
  - RecoverReplica(key, from_replica) → Status
  - ReconcileReplicas(key) → Status (TTL sync)
  - ReportReplicaHealth() → HealthStatus

ReplicaInfo:
  - node_id, replica_id, role {PRIMARY, BACKUP}
  - state {INIT, SYNCING, READY, STALE}
  - last_sync_seqno
  - health_score (0-100)
```

---

## 三、关键流程设计

### 3.1 写入多副本流程

```mermaid
sequenceDiagram
    participant Client
    participant Primary as Primary Worker
    participant Backup1 as Backup Worker 1
    participant Backup2 as Backup Worker 2
    participant Master
    
    Client->>Master: GetReplicaTargets(key, N=2)
    Master-->>Client: [Primary: W1, Backup: W2] (anti-affinity checked)
    
    Client->>Primary: PutStart(key, N=2)
    Primary->>Primary: Allocate local ShmUnit
    
    Client->>Primary: WriteChunk(data, offset)
    Primary->>Backup1: ReplicateChunk(data, offset) [async]
    
    Client->>Primary: PutEnd(key)
    Primary->>Primary: Mark COMPLETE (seqno=N)
    Primary->>Backup1: ReplicatePutEnd(seqno=N) [async]
    Backup1-->>Primary: ACK (seqno=N)
    Primary-->>Client: PutResult(OK, replicas=2)
```

**关键决策:**
- 主副本同步写入 (Client → Primary)
- 备副本异步写入 (Primary → Backup)，Client 不等待
- PutEnd 时 Primary 确保 seqno 单调递增
- 备副本 ACK 返回后 Primary 标记 min_synced_seqno

### 3.2 故障切换流程

```mermaid
sequenceDiagram
    participant Client
    participant Primary as Primary (failing)
    participant Backup as Backup
    participant Master
    
    Client->>Primary: Get(key)
    Primary--xClient: No response (3s timeout)
    
    Client->>Master: RefreshReplica(key)
    Master-->>Client: Primary=unreachable, Promote Backup=W2
    
    Client->>Backup: Get(key, replica_role=BACKUP)
    alt Backup has data (seqno >= last_good)
        Backup-->>Client: GetResult(data)
        Note over Client: P99.99 < 5ms
    else Backup stale
        Backup->>Master: Request recovery
        Master-->>Backup: Recovery source
        Backup-->>Client: TryLater (1002)
    end
```

### 3.3 均衡读取流程

```mermaid
flowchart TD
    A[Client::Get key] --> B{Local has replica?}
    B -->|Yes| C[Read from local Worker]
    B -->|No| D[Query Master for replicas]
    D --> E[Get replica list: P, B1, B2]
    E --> F{Filter: healthy + in-sync}
    F --> G[Score each replica]
    G --> H[Select best replica]
    C --> H
    
    subgraph Score
        I[NUMA affinity: +10]
        J[Health score: *weight]
        K[Current load: -load_factor]
        L[RTT estimate: -rtt_weight]
    end
```

**副本选择策略：**
```python
def select_best_replica(replicas):
    for r in replicas:
        score = (
            +10 if r.local_numa else 0
            + r.health_score * 0.3
            - r.active_requests * 0.1
            - r.rtt_us * 0.001
        )
    return max(replicas, key=score)
```

### 3.4 NUMA 亲和写入

```mermaid
sequenceDiagram
    participant Client as Client (NUMA-0)
    participant Master
    participant W0 as Worker NUMA-0 (Primary)
    participant W1 as Worker NUMA-1 (Backup)
    
    Client->>Client: Detect NUMA node (sched_getcpu)
    Client->>Master: GetReplicaTargets(key, NUMA=0)
    
    Note over Master: Prefer node on NUMA-0<br/>for Primary replica
    
    Master-->>Client: Primary=W0(NUMA-0), Backup=W1(NUMA-1)
    
    Client->>W0: PutStart (local NUMA, no HCCS cross)
    W0->>W1: Replicate (cross NUMA, tolerate)
    
    Note over Client,W0: Primary write stays on local NUMA<br/>avoiding HCCS latency
```

---

## 四、代码模块影响

| 模块 | 文件 | 改动类型 | 说明 |
|------|------|:--:|------|
| KV Client | `src/datasystem/client/kv_cache/kv_client.cpp` | **重构** | 多副本 Put/Get 路径 |
| Object Client | `src/datasystem/client/object_cache/object_client_impl.cpp` | **修改** | 副本选择逻辑 |
| Worker OC | `src/datasystem/worker/object_cache/service/worker_oc_service_impl.cpp` | **重构** | ReplicateChunk 处理 |
| Worker-Worker | `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp` | **修改** | 副本间数据传输 |
| ReplicaManager | `src/datasystem/worker/object_cache/replica_manager.h/.cpp` | **新增** | 副本管理核心 |
| Hash Ring | `src/datasystem/common/metastore/hash_ring.h` | **修改** | 多副本位置计算 |
| Master | `src/datasystem/master/*` | **修改** | 副本放置决策 |
| TTL/Eviction | `src/datasystem/worker/object_cache/*ttl*` | **修改** | 副本同步老化 |
| Metrics | `src/datasystem/common/metrics/kv_metrics.cpp` | **新增** | 副本相关指标 |

### 新增 Metrics

| Metric ID | 名称 | 类型 | 说明 |
|-----------|------|------|------|
| 50 | `replica_create_total` | COUNTER | 副本创建数 |
| 51 | `replica_sync_latency` | HISTOGRAM | 副本同步延迟 |
| 52 | `replica_failover_total` | COUNTER | 故障切换次数 |
| 53 | `replica_failover_latency` | HISTOGRAM | 故障切换延迟 |
| 54 | `backup_read_total` | COUNTER | 从备副本读次数 |
| 55 | `replica_out_of_sync_total` | COUNTER | 副本不同步次数 |

---

## 五、工作量估算

| 子需求 | 开发 | 测试 | 人员 | 合计 |
|--------|:--:|:--:|------|:--:|
| MR-01 写入多副本 | 8d | 3d | A | 11d |
| MR-02 反亲和 | 5d | 2d | B | 7d |
| MR-03 故障切换 | 6d | 3d | A | 9d |
| MR-04 数据恢复 | 5d | 2d | B | 7d |
| MR-05 一致性老化 | 3d | 1d | C | 4d |
| MR-06 均衡读取 | 5d | 2d | C | 7d |
| MR-07 NUMA 亲和 | 5d | 2d | B | 7d |
| MR-08 跨版本兼容 | 3d | 1d | A | 4d |
| **合计** | **40d** | **16d** | 3人 | **56d** |

### 拆解到 3 人并行

| 角色 | 负责 | 依赖 |
|------|------|------|
| 人力 A: 写入路径 | MR-01 + MR-03 + MR-08 | ReplicaManager 接口 |
| 人力 B: 放置/分布 | MR-02 + MR-04 + MR-07 | Hash Ring 修改 |
| 人力 C: 读取路径 | MR-05 + MR-06 | MR-01 (副本存在) |

---

## 六、验证方案

### 6.1 功能验证

| 场景 | 验证方法 | 标准 |
|------|---------|------|
| 正常多副本写入 | Put(N=2), 检查两个 Worker 都有数据 | 数据一致 |
| 主副本故障 | kill Primary, Get 返回备副本数据 | < 5ms 完成 |
| 副本恢复 | kill Backup, 等待自动恢复 | 5min 内恢复 READY |
| 均衡读取 | 1写10读，统计各副本读次数 | 偏差 < 20% |

### 6.2 性能测试

| 指标 | 方法 | 目标 |
|------|------|------|
| 写入 P99 | Put 8MB × 10000次 | < 3ms |
| 故障切换 P99.99 | 主副本 kill × 1000次 | < 5ms |
| QPS 提升 | 1写10读 对比 单副本 vs 多副本 | +30% |

---

## 七、与 Mooncake 的对比

| 维度 | Mooncake | 我们方案 | 优势 |
|------|---------|---------|------|
| 副本操作 | Put/Copy/Move 三种 | Put (主+备同步) + Recover | 简化 API |
| 一致性 | PutStart/PutEnd 两阶段 | 同样两阶段 + seqno 锚点 | 等价 |
| 故障切换 | GetReplicaList 自动切换 | Master 主动 Promote + Client 刷新 | 更快的切换路径 |
| 均衡读取 | Conductor 全局调度 | Client 侧 score 选择 | 分散决策，无单点 |
| NUMA 亲和 | 自动拓扑发现 | 同样 sched_getcpu + Master 偏好 | 等价 |
| 反亲和 | Slice 级 | Object 级 (key hash) | 更粗粒度，但足够 |
