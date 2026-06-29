# Meta-Affinity Write — AS IS vs TO BE 时序

**Status:** Done (Phase 1)  
**Scope:** 同节点 async replicate + remote-only 直写 + Get primary 优先  
**MR:** [!1151](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1151)

---

## 0. 整体设计（TO-BE）

```mermaid
flowchart TB
    subgraph Client["SDK Client"]
        W[Create/Put/Publish/Seal]
        GATE{ShouldRouteWriteToMetaOwner?}
        LOCAL[GetAvailableWorkerApi]
        DIRECT[GetWriteWorkerApi → meta owner]
    end
    subgraph WorkerOrigin["Origin Worker"]
        PUB[PublishObject]
        REP[MetaAffinityReplicateManager]
        MIG[DataMigrator]
    end
    subgraph WorkerMeta["Meta Owner Worker"]
        PRI[Primary copy]
    end
    W --> GATE
    GATE -->|false| LOCAL --> PUB
    GATE -->|true| DIRECT --> PUB
    PUB -->|async| REP --> MIG --> PRI
    PUB -->|remove_location=false| LOCAL
```

---

## 1. 写路径门控

### AS IS

```mermaid
flowchart TD
    A1[Client Write] --> A2[GetAvailableWorkerApi]
    A2 --> A3[Gateway/Local Worker Publish]
    A3 --> A4[Primary @ origin]
    A4 -.->|optional async| A5[Replicate to meta owner]
```

### TO BE（Phase 1）

```mermaid
flowchart TD
    B1[Client Write] --> B2{enable_meta_affinity_replicate<br/>&& distributed_master<br/>&& !HasHealthyLocalWorker?}
    B2 -->|否| B3[GetAvailableWorkerApi → Publish]
    B3 --> B4[ScheduleMetaAffinityReplicateIfNeeded]
    B2 -->|是| B5[MetaAffinityClientRingSource]
    B5 --> B6[GetWriteWorkerApi → meta owner Publish]
    B6 --> B7[Primary @ meta owner immediately]
    B4 --> B8[Async replicate + ReplacePrimary]
    B8 --> B9[Primary @ meta owner, origin keeps local copy]
```

**变化点：**

- 跨节点写：跳过 gateway replicate 链，直写 meta owner
- 同节点写：Publish 后异步 replicate（不阻塞 ACK）
- `ReplacePrimary(remove_location=false)`：origin 保留 local copy

---

## 2. 同节点 Publish + Replicate

```mermaid
sequenceDiagram
    participant C as Client@W0
    participant W0 as Origin Worker W0
    participant M as Meta Master
    participant W1 as Meta Owner W1

    C->>W0: Publish(objectKey)
    W0->>M: RequestingToMaster
    W0-->>C: Publish ACK (primary @ W0)
    Note over W0: ScheduleMetaAffinityReplicateIfNeeded
    W0->>W1: DataMigrator.MigrateData
    W0->>M: ReplacePrimary(remove_location=false)
    Note over W0,W1: W0 保留 local copy; primary → W1
    C->>W0: Get (local hit)
    W0-->>C: serve local copy
    C->>W0: InvalidateBuffer
    C->>W0: Get (cold)
    W0->>W1: fetch from primary
    W1-->>C: data
```

---

## 3. Remote-only 直写 meta owner

```mermaid
sequenceDiagram
    participant C as Remote Client
    participant W1 as Meta Owner W1
    participant M as Meta Master

    Note over C: !HasHealthyLocalWorker()
    C->>C: MetaAffinityClientRingSource.GetMetaAddress
    C->>W1: Create/Put/Publish (direct)
    W1->>M: master ops
    W1-->>C: ACK (primary @ W1)
    Note over C,W1: 无 gateway + 无 async replicate
```

---

## 4. Get 读侧（primary 优先）

```mermaid
sequenceDiagram
    participant C as Reader Client
    participant Wx as Worker (any)
    participant M as Meta Master

    C->>M: QueryMeta
    M-->>C: locations + primary hint
    Note over C: SelectObjectLocation prefers primary
    alt local copy hit
        C->>Wx: Get local buffer
    else cold, non-primary local miss
        C->>Wprimary: Get from primary worker
    end
```

---

## 5. 与 Direct Read 组合（Deferred ST）

| 场景 | 写 | 读 | 期望 |
|------|----|----|------|
| Remote-only | meta-affinity 直写 | direct read | primary colocate + 少 Get hop |
| 同节点 | async replicate | gateway/local | local copy + primary 切换 |

Phase 2 需补组合 ST（见 [issue-rfc.md](./issue-rfc.md)）。
