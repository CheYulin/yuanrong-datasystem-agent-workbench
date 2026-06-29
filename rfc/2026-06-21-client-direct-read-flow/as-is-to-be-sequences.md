# Client Direct Read — AS IS vs TO BE 时序

**Status:** Done  
**Scope:** Phase 1 TCP read path; URMA / remote-write / colocated-read deferred.  
**HTML:** https://yche.me/design/client-direct-read-flow-20260624.html

---

## 0. 整体设计（TO-BE）

```mermaid
flowchart TB
    subgraph Client["SDK Client"]
        GET[ObjectClient.Get]
        CUT[TryDirectReadCutbackToLocalWorker]
        GATE{HasHealthyLocalWorker?}
        DR[DirectReadFlow]
        GW[GetBuffersFromWorker gateway]
    end
    subgraph Control["控制面"]
        RING[ClientHashRingSource]
        META[Meta Master QueryMeta]
    end
    subgraph Data["数据面"]
        DW[Data Worker TCP GetObjectRemote]
    end
    GET --> CUT --> GATE
    GATE -->|false| DR
    GATE -->|true| GW
    DR --> RING --> META --> DW
    DR -->|fallback| GW
```

---

## 1. 读路径门控

### AS IS（错误）

```mermaid
flowchart TD
    A1[Client.Get] --> A2[选 workerApi]
    A2 --> A3{!IsShmEnable cross-node?}
    A3 -->|是| A4[DirectReadFlow]
    A3 -->|否| A5[gateway Get]
```

### TO BE（当前）

```mermaid
flowchart TD
    B1[Client.Get] --> B2[GetAvailableWorkerApi]
    B2 --> B3[TryDirectReadCutbackToLocalWorker]
    B3 --> B5{!HasHealthyLocalWorker?}
    B5 -->|是| B6[DirectReadFlow]
    B5 -->|否| B7[gateway Get]
```

**变化点：** 门控从「是否跨节点读」改为「是否无 healthy local worker」；Get 前增加 ring 驱动的回切。

---

## 2. HashRing 刷新

| | AS-IS | 旧 TO-BE（已废弃） | **目标 TO-BE** |
|---|-------|-------------------|----------------|
| Bootstrap | etcd → worker | 不变 | 不变 |
| Steady | 仅 HasScalingTask 时刷新 | ~~每次 route lookup~~ | **cached snapshot**；仅 scaling / 控制面事件 / 版本不一致时刷新 |

详见 [hash-ring-refresh-policy.md](./hash-ring-refresh-policy.md)。

```mermaid
sequenceDiagram
    participant C as Client
    participant W as Worker
    participant E as etcd
    Note over C,E: Steady read (no scale/moving)
    C->>C: GetMetaAddress from cached ring
    Note over C,E: meta_is_moving or HasScalingTask
    C->>W: RefreshRing (GetClusterState)
    W->>E: cluster state if needed
    C->>C: Update snapshot
```

---

## 3. 元数据查询（迁移 / 重定向）

### AS IS

- `meta_is_moving` → continue，不刷新 ring
- redirect → 换地址重试，丢弃 partial query_metas

### TO BE

```mermaid
sequenceDiagram
    participant C as Client
    participant R as HashRingSource
    participant M as Meta Master
    C->>M: QueryMeta redirect=true
    loop meta_is_moving
        C->>R: RefreshRoute
        C->>M: retry
    end
    C->>M: QueryMeta redirect=false @ redirect master
    C->>C: merge query_metas
```

---

## 4. Direct Read 端到端 + 回切

```mermaid
sequenceDiagram
    participant C as Client
    participant R as HashRingSource
    participant M as Meta Master
    participant D as Data Worker
    C->>R: RefreshRoute
    C->>M: QueryMeta
    C->>D: GetObjectRemote TCP
    Note over C: worker0 恢复
    C->>R: HasHealthyWorkerAtAddress
    C->>C: RecoverPreferredLocalWorker → gateway
```

---

## 验证矩阵（TCP · 19/19 PASS）

| ID | 场景 | ST |
|----|------|-----|
| V1 | 无 local worker → direct meta+TCP | `StandbyWithoutLocalWorkerUsesDirectRead` |
| V2 | 扩缩容 / moving 仍可读 | `ReadSurvivesWorkerScaleDownAndUp`, `MetaMovingRefreshesRingAndSucceeds` |
| V3 | local worker 恢复回切 gateway | `LocalWorkerRecoveryCutbackToGateway` |

全量用例见 HTML 页 §6。
