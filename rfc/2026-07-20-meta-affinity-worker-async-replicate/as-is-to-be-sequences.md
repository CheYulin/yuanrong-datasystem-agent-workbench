# As-Is / To-Be 时序 — Worker Async Replicate

## 1. As-Is（main/master）：同节点写入无 replicate

```mermaid
sequenceDiagram
    participant C as Client (local worker)
    participant Wo as Origin Worker
    participant M as Metadata Master
    participant Wr as Meta Owner Worker

    C->>Wo: Publish (binary)
    Wo->>M: CreateMeta / UpdateMeta
    M-->>Wo: ACK (meta owner = Wr)
    Wo->>Wo: Save data, primary=true @ Wo
    Wo-->>C: Publish ACK

    Note over Wo,Wr: primary 留在 origin，meta owner 在 Wr

    C->>Wo: Get (later, cold)
    Wo->>M: QueryMeta
    M-->>Wo: primary @ Wo, locations [Wo, ...]
    Wo-->>C: data from Wo
```

**问题**：若读者连到其它 worker，需 cross-worker pull；meta/data 不 colocate。

---

## 2. To-Be Phase 1（feature）：async replicate

```mermaid
sequenceDiagram
    participant C as Client
    participant Wo as Origin Worker
    participant M as Metadata Master
    participant Wr as Meta Owner Worker
    participant Q as MetaAffinityReplicateManager

    C->>Wo: Publish (binary)
    Wo->>M: CreateMeta
    M-->>Wo: ACK
    Wo->>Wo: Save data, primary=true @ Wo
    Wo->>Q: ScheduleMetaAffinityReplicateIfNeeded (async)
    Wo-->>C: Publish ACK (不等待 replicate)

    Q->>Q: ExecuteMetaAffinityReplicate
    Q->>Wo: DataMigrator.MigrateToTargetNode → Wr
    Wo->>Wr: worker-worker migrate RPC
    Wr-->>Wo: migrate OK
    Q->>M: ReplacePrimary(origin=Wo, new=Wr, remove_location=false)
    M-->>Q: success
    Q->>Wo: MarkLocalCopyOnOriginWorker (primary=false locally)

    Note over Wo,Wr: primary @ Wr; Wo 仍保留 local copy location
```

---

## 3. To-Be Phase 1.5：失败与关停

### 3.1 Migrate 失败

```mermaid
sequenceDiagram
    participant Q as ReplicateExecutor
    participant Wo as Origin Worker
    participant Wr as Meta Owner Worker
    participant M as Master

    Q->>Wo: MigrateToTargetNode
    Wo-xWr: migrate fail (network/timeout)
    Q->>Q: LOG(WARNING) + failure_metric{stage=migrate}
    Note over Wo: primary 仍为 Wo；读者行为与 As-Is 一致
```

### 3.2 Worker Shutdown Drain

```mermaid
sequenceDiagram
    participant S as WorkerOCServer
    participant Q as MetaAffinityReplicateManager
    participant T as Replicate Threads

    S->>Q: StopMetaAffinityReplicate(timeout=30s)
    Q->>Q: running_=false
    loop drain queue
        Q->>T: finish in-flight tasks
    end
    Q->>T: join
    S->>S: continue worker teardown
```

---

## 4. 读路径（replicate 完成后）

```mermaid
sequenceDiagram
    participant C as Client
    participant Wo as Origin Worker (local copy)
    participant Wr as Meta Owner (primary)
    participant M as Master

    C->>Wr: Get (reader on meta owner colocated node)
    Wr->>M: QueryMeta
    M-->>Wr: primary @ Wr
    Wr-->>C: local hit (1 hop)

    C->>Wo: Get (reader still on origin)
    Wo->>M: QueryMeta
    M-->>Wo: primary @ Wr, location includes Wo
    Wo->>Wr: pull from primary (if needed)
    Wo-->>C: data
```

`SelectObjectLocation` primary 优先（Master 侧，父 RFC）保证 QueryMeta 先指向 Wr。
