# 用例描述 (Use Cases)

## UC-1: 运维人员滚动升级 100 节点集群

```mermaid
sequenceDiagram
    actor Ops as 运维人员
    participant CLI as dscli
    participant Master
    participant W1 as Worker-1 (rc24)
    
    Ops->>CLI: dscli upgrade --target rc26 --max-unavailable 10%
    CLI->>Master: BeginUpgrade(rc26, 10%)
    Master->>Master: 检查集群健康
    
    loop 每个 Worker (10% 并行)
        Master->>W1: SetState(UPGRADING)
        W1->>W1: CreateCheckpoint()
        W1-->>Master: CheckpointDone
        Master->>W1: Terminate(old)
        Note over W1: 新进程启动 (rc26)
        W1->>W1: RestoreFromCheckpoint()
        W1-->>Master: Register(recovery)
        Master->>W1: SetState(RUNNING)
        Note over Master: 等待 30s 稳定
    end
    
    Master-->>CLI: UpgradeComplete (45min)
    CLI-->>Ops: 升级完成
```

**前置条件**: 集群 100 Worker (rc24)，全部 RUNNING，服务正常
**后置条件**: 集群 100 Worker (rc26)，数据零丢失，升级中服务可用

**验收标准**:
- [ ] 全量升级 < 60min
- [ ] 升级过程中 P99 latency < 2x 基线
- [ ] 升级后 5min 内指标恢复正常
- [ ] 零数据迁移，零 cache miss 增加

---

## UC-2: Worker 异常崩溃后快速恢复

```mermaid
sequenceDiagram
    participant Kernel
    participant Worker
    participant Checkpoint
    participant Master
    participant Client
    
    Note over Worker: 正常运行中...
    Kernel->>Worker: SIGKILL (OOM/crash)
    Note over Worker: 进程终止
    
    Note over Worker: 自动重启 (systemd)
    Worker->>Checkpoint: HasCheckpoint()? Yes
    Worker->>Checkpoint: VerifyCheckpoint() → OK
    Worker->>Checkpoint: RestoreFromLatest()
    Worker->>Master: Register(recovery=true, last_seqno=N)
    Master->>Worker: DeltaSync(from_seqno=N) — 缺失的 Put 操作
    Worker-->>Master: SyncDone
    
    Client->>Worker: Get(key) — 正常服务
    Worker-->>Client: Data
```

**前置条件**: Worker 已有至少 1 个成功 Checkpoint，RocksDB 数据完整
**后置条件**: Worker 恢复 RUNNING，丢失 < 10s 写入数据

**验收标准**:
- [ ] 恢复时间 < 3s (从进程启动到 RUNNING)
- [ ] 恢复后 5s 内正常服务
- [ ] 数据丢失窗口 = Checkpoint 间隔 (10s)

---

## UC-3: 升级失败自动回滚

```mermaid
sequenceDiagram
    actor Ops
    participant Master
    participant W1 as Worker-1
    
    Ops->>Master: Upgrade(rc26)
    Master->>W1: UPGRADING → Checkpoint → Terminate
    Note over W1: 启动 rc26...
    W1-->>Master: Register(recovery)
    Master->>W1: RUNNING
    Note over W1: 新版本运行，但指标异常!
    
    Master->>Master: 检测: P99 latency > 10x 基线
    Master->>Master: 超时 30s，触发回滚
    Master->>W1: Rollback(rc24)
    W1->>W1: Terminate (rc26)
    Note over W1: 启动 rc24 (旧版本)
    W1->>W1: RestoreFromCheckpoint()
    W1-->>Master: Register(recovery, rc24)
    Master-->>Ops: RollbackComplete
```

**前置条件**: Worker 升级到 rc26 后指标异常
**后置条件**: Worker 回退到 rc24，数据不丢失，服务恢复

**验收标准**:
- [ ] 异常检测 < 30s
- [ ] 回滚执行 < 10s
- [ ] 回滚后指标恢复正常

---

## UC-4: 主副本故障 → 备副本无缝接管

```mermaid
sequenceDiagram
    participant Client as KV Client
    participant Master
    participant W1 as Worker-1 (Primary) [dead]
    participant W2 as Worker-2 (Backup)
    
    Client->>W1: Get(key=abc)
    W1--xClient: no response (3s timeout)
    
    Client->>Master: GetReplicaList(key=abc, refresh=true)
    Master->>Master: 检测 W1 心跳超时 (3s)
    Master->>W2: PromoteToPrimary(key=abc)
    W2->>W2: Mark role=PRIMARY
    W2-->>Master: PromoteOK
    
    Master-->>Client: ReplicaList(Primary=W2, Backup=待分配)
    Client->>W2: Get(key=abc)
    W2-->>Client: Data (P99.99 < 5ms!)
```

**前置条件**: key=abc 有 Primary(W1) + Backup(W2)，W1 正常后故障
**后置条件**: W2 提升为 Primary，Client 继续正常读取

**验收标准**:
- [ ] 故障检测 < 3s
- [ ] Promote 完成 P99.99 < 5ms
- [ ] Client 自动切换，无业务感知

---

## UC-5: 1024 节点集群扩容 100 节点

```mermaid
sequenceDiagram
    participant Master
    participant Pool as ZMQConnectionPool
    participant New as New Workers (100)
    participant Old as Existing Workers (1024)
    
    Master->>Master: 接收扩容命令 (+100)
    
    par 100 个新 Worker 并行
        New->>Master: Register(joining)
        Master-->>New: ClusterTopology (1124 nodes)
        New->>Pool: Prewarm(top_100_hot_peers)
        Pool->>Old: zmq_connect (async, 100 conns)
        Pool->>Old: urma_create_qp (async, 100 QPs)
        Note over Pool: 预热 5s
        New->>Master: Ready
    end
    
    Master->>Master: 更新 HashRing
    Master->>Old: NotifyTopologyChange
    Note over Old: 接受新节点连接<br/>P99.99 毛刺 < 10ms
```

**前置条件**: 1024 节点集群正常运行
**后置条件**: 1124 节点集群正常运行，扩容过程无毛刺

**验收标准**:
- [ ] 100 节点全量扩容 < 30s
- [ ] 扩容过程中 P99.99 < 10ms
- [ ] 新节点连接池命中率 > 90%

---

## 用例与 SR 追溯矩阵

| Use Case | RFC | IR | SR | 验收关键指标 |
|----------|-----|----|----|-----------|
| UC-1 滚动升级 | RFC1 | IR-1,IR-3,IR-4 | SR-1.1~SR-4.2 | 全量升级 < 60min, 零迁移 |
| UC-2 崩溃恢复 | RFC1 | IR-1,IR-2 | SR-1.2,SR-2.1,SR-2.2 | 恢复 < 3s, 丢失 < 10s |
| UC-3 升级回滚 | RFC1 | IR-3 | SR-3.3,SR-3.4 | 检测 < 30s, 回滚 < 10s |
| UC-4 故障切换 | RFC2 | IR-7 | SR-7.1~SR-7.4 | P99.99 < 5ms |
| UC-5 扩容 | RFC4 | IR-13,IR-14 | SR-13.1,SR-14.3 | 扩容 < 30s, P99.99 < 10ms |
