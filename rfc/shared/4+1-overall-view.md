# datasystem 整体 4+1 视图 (三个 RFC 联动)

## 一、逻辑视图 (Logical View)

### 领域概念模型

```mermaid
classDiagram
    class Object {
        +string name
        +string key_hash
        +uint64 size
        +uint32 ttl_ms
        +ReplicaSet replicas
    }
    
    class ReplicaSet {
        +Replica primary
        +Replica[] backups
        +uint32 min_synced_seqno
        +GetBestForRead() Replica
    }
    
    class Replica {
        +string node_id
        +ReplicaRole role
        +ReplicaState state
        +uint64 last_sync_seqno
        +HealthScore health
    }
    
    class Worker {
        +string node_id
        +WorkerState state
        +CheckpointManager checkpoint_mgr
        +ReplicaManager replica_mgr
        +ZMQConnectionPool zmq_pool
        +URMAConnectionPool urma_pool
        +JettyManager jetty_mgr
    }
    
    class WorkerState {
        <<enumeration>>
        NO_INIT
        INIT
        RUNNING
        UPGRADING
        PRE_LEAVING
        FAIL
    }
    
    class CheckpointManager {
        +CreateCheckpoint() CheckpointId
        +RestoreFromCheckpoint(id) Status
        +VerifyCheckpoint(id) bool
    }
    
    class ReplicaManager {
        +CreateReplicas(key, val, config) Status
        +GetBestReplica(key) ReplicaInfo
        +PromoteToPrimary(key) Status
        +RecoverReplica(key, from) Status
    }
    
    class ZMQConnectionPool {
        +Acquire(peer_id) socket_t
        +Release(peer_id) void
        +Prewarm(peers) void
    }
    
    class URMAConnectionPool {
        +GetOrCreateQP(peer_id) QueuePair
        +WarmupQP(peers) void
    }
    
    class JettyManager {
        +AllocateCTP() (jetty_id, ctp_id)
        +JettyLoad(jetty_id) float
    }
    
    Worker --> WorkerState
    Worker --> CheckpointManager
    Worker --> ReplicaManager
    Worker --> ZMQConnectionPool
    Worker --> URMAConnectionPool
    Worker --> JettyManager
    Object --> ReplicaSet
    ReplicaSet --> Replica
```

### 子系统划分

| 子系统 | 职责 | 关联 RFC |
|--------|------|---------|
| **Checkpoint** | 本地持久化 + 快速恢复 | RFC1 |
| **Replica** | 副本生命周期 + 故障切换 | RFC2+3 |
| **Connection** | 连接池 + 复用 + Jetty | RFC4 |
| **HashRing** | 分布 + 反亲和 + 升级态 | RFC1, RFC2 |
| **Metrics** | 副本/恢复/连接相关指标 | RFC1, RFC2, RFC4 |

---

## 二、进程视图 (Process View)

### 系统进程/组件

```mermaid
flowchart TB
    subgraph Node1 [Node 1 - NUMA 0]
        direction TB
        W1[Worker Process]
        W1DB[(RocksDB<br/>Checkpoint Data)]
        W1 --> W1DB
    end
    
    subgraph Node2 [Node 2 - NUMA 0]
        W2[Worker Process]
        W2DB[(RocksDB)]
        W2 --> W2DB
    end
    
    subgraph Node3 [Node 3 - NUMA 1]
        W3[Worker Process]
        W3DB[(RocksDB)]
        W3 --> W3DB
    end
    
    subgraph MasterNode [Master Node]
        M[Master Process]
        ETCD[(etcd)]
        M --> ETCD
    end
    
    subgraph Clients [Clients]
        C1[KV Client 1]
        C2[KV Client 2]
    end
    
    C1 -->|Get/Put| W1
    C2 -->|Get/Put| W3
    W1 <-->|ZMQ Pool| W2
    W2 <-->|ZMQ Pool| W3
    W1 <-->|ZMQ Pool| W3
    
    W1 -.->|URMA RDMA| W2
    W2 -.->|URMA RDMA| W3
    
    M <-->|Heartbeat/Control| W1
    M <-->|Heartbeat/Control| W2
    M <-->|Heartbeat/Control| W3
```

### 关键交互协议

| 交互 | 协议 | 说明 |
|------|------|------|
| Client ↔ Worker (Put/Get) | ZMQ RPC | 请求/响应 |
| Worker ↔ Worker (Replicate) | ZMQ RPC + URMA RDMA | 副本同步 (控制 + 数据) |
| Worker ↔ Master (Heartbeat) | etcd Lease | 存活检测 |
| Worker → Local RocksDB | RocksDB API | Checkpoint 读写 |

---

## 三、物理视图 (Physical View)

### 1024 节点部署拓扑

```mermaid
flowchart TB
    subgraph DC [Data Center]
        subgraph Rack1 [Rack 1]
            subgraph NUMA0_1 [NUMA Node 0]
                N1[Node 1<br/>Worker + RocksDB]
                N2[Node 2<br/>Worker + RocksDB]
            end
            subgraph NUMA1_1 [NUMA Node 1]
                N3[Node 3<br/>Worker + RocksDB]
                N4[Node 4<br/>Worker + RocksDB]
            end
        end
        subgraph Rack2 [Rack 2]
            subgraph NUMA0_2 [NUMA Node 0]
                N5[Node 5]
                N6[Node 6]
            end
            subgraph NUMA1_2 [NUMA Node 1]
                N7[Node 7]
                N8[Node 8]
            end
        end
        subgraph RackN [Rack N ...]
            Nx[Node 1024]
        end
    end
    
    N1 -->|HCCS| N3
    N1 -->|RDMA| N5
    N1 -->|RDMA| Nx
```

### 物理约束

| 约束 | 说明 |
|------|------|
| **NUMA 亲和** | 写入优先同 NUMA node，避免跨 HCCS |
| **Rack 反亲和** | 副本跨 Rack 分布 (N=3 时) |
| **Jetty 限制** | 每 chip Jetty 数有限，CTP 需复用 |
| **本地盘** | 每节点 NVMe SSD 存 Checkpoint |
| **RDMA NIC** | 每 NUMA node 至少 1 块 200Gbps RDMA NIC |

---

## 四、开发视图 (Development View)

### 模块依赖

```mermaid
flowchart TB
    subgraph Client [Client Layer]
        KC[kv_client.cpp]
        OC[object_client_impl.cpp]
        RA[client_worker_remote_api.cpp]
    end
    
    subgraph Worker [Worker Layer]
        WOS[worker_oc_server.cpp]
        WOSI[worker_oc_service_impl.cpp]
        WWOS[worker_worker_oc_service_impl.cpp]
        WRM[worker_request_manager.cpp]
    end
    
    subgraph New [New Modules]
        CM[checkpoint_manager.h/cpp]
        RM[replica_manager.h/cpp]
        ZCP[zmq_connection_pool.h/cpp]
        UCP[urma_connection_pool.h/cpp]
        JM[jetty_manager.h/cpp]
    end
    
    subgraph Infra [Infrastructure]
        ZMQ[zmq_stub_impl.cpp<br/>zmq_service.cpp]
        URMA[urma_manager.cpp<br/>urma_resource.cpp]
        HR[hash_ring.h<br/>hash_ring_task_executor.*]
        MM[metrics/kv_metrics.*]
    end
    
    subgraph Storage [Storage]
        RS[RocksDB via<br/>rocks_store.*]
        PA[persistence_api.*]
    end
    
    WOS --> CM
    WOSI --> RM
    WWOS --> RM
    ZMQ --> ZCP
    URMA --> UCP
    WOS --> JM
    
    CM --> RS
    RM --> HR
    RM --> MM
    ZCP --> MM
    UCP --> MM
```

### 新增文件统计

| RFC | 新增 .h/.cpp | 修改现有 | 新增测试 |
|-----|:-----------:|:------:|:------:|
| RFC1 | 2 | 4 | 3 |
| RFC2+3 | 2 | 8 | 5 |
| RFC4 | 3 | 6 | 4 |
| **合计** | **7** | **18** | **12** |

---

## 五、场景视图 (Scenarios)

### S1: 滚动升级 (Normal Path)

```mermaid
sequenceDiagram
    participant Ops
    participant Master
    participant W1_Old as Worker-1 (rc24)
    participant W1_New as Worker-1 (rc26)
    participant Client
    
    Ops->>Master: dscli upgrade --target rc26 --worker worker-1
    Master->>W1_Old: SetState(UPGRADING)
    W1_Old->>W1_Old: CreateCheckpoint()
    W1_Old->>Master: CheckpointDone(id=42)
    Master->>W1_Old: Terminate
    Note over W1_Old: SIGTERM → graceful exit
    
    Ops->>W1_New: Start (rc26)
    W1_New->>W1_New: RestoreFromCheckpoint(42)
    W1_New->>Master: Register(recovery=true)
    Master->>W1_New: SetState(RUNNING)
    
    Client->>W1_New: Get/Put (normal service)
```

### S2: 故障切换 (Multi-Replica Failover)

```mermaid
sequenceDiagram
    participant Client
    participant Master
    participant W1 as Worker-1 (Primary)
    participant W2 as Worker-2 (Backup)
    
    Client->>W1: Get(key=abc)
    W1--xClient: No response (3s)
    
    Client->>Master: GetReplicaList(key=abc)
    Master->>Master: Detect W1 unreachable
    Master->>W2: PromoteToPrimary(key=abc)
    Master-->>Client: Primary=W2 (new)
    
    Client->>W2: Get(key=abc)
    W2-->>Client: Data (P99.99 < 5ms)
```

### S3: 1K 节点扩容 (Connection Pool)

```mermaid
sequenceDiagram
    participant NewWorker
    participant Master
    participant Pool as ZMQConnectionPool
    
    NewWorker->>Master: Join(cluster_size=1024)
    Master-->>NewWorker: ClusterTopology
    
    NewWorker->>Pool: Prewarm(top_100_peers)
    loop i=1..100
        Pool->>Pool: Async zmq_connect(peer_i)
    end
    Note over Pool: Warmed in ~5s
    
    NewWorker->>Master: Register(ready)
    Master->>NewWorker: RUNNING
    
    Note over NewWorker: First request: pool hit<br/>No cold-start latency
```

---

## 接口影响汇总

### StatusCode 新增

| Code | 名称 | 场景 |
|------|------|------|
| 1011 | `K_REPLICA_UNAVAILABLE` | 所有副本不可用 |
| 1012 | `K_REPLICA_OUT_OF_SYNC` | 副本数据落后 |
| 1013 | `K_CHECKPOINT_CORRUPTED` | Checkpoint 损坏 |

### gflags 新增 (跨三个 RFC)

| Flag | Default | RFC | 说明 |
|------|---------|-----|------|
| `checkpoint_enabled` | true | RFC1 | Checkpoint 总开关 |
| `checkpoint_interval_s` | 10 | RFC1 | Checkpoint 间隔 |
| `replica_count` | 2 | RFC2 | 副本数 |
| `replica_anti_affinity` | rack | RFC2 | 反亲和级别 |
| `numa_affinity_enabled` | true | RFC2 | NUMA 亲和写入 |
| `zmq_pool_size` | 10 | RFC4 | ZMQ 连接池大小 |
| `urma_qp_pool_size` | 10 | RFC4 | URMA QP 池大小 |
| `jetty_ctp_per_jetty` | 8 | RFC4 | Jetty CTP 复用度 |

### 跨 RFC 工作量总计

| RFC | 开发 | 测试 | 合计 |
|-----|:--:|:--:|:--:|
| RFC1 滚动升级 | 18d | 8d | 26d |
| RFC2+3 多副本 | 28d | 12d | 40d |
| RFC4 建链优化 | 24d | 10d | 34d |
| **合计** | **70d** | **30d** | **100d** |

> 3 人并行约 6-8 周完成全部三个 RFC。
