# URMA Send Jetty Lane Isolation — AS IS vs TO BE

**Status**: Draft

## 1. 普通写路径

### AS IS

```mermaid
sequenceDiagram
    participant W as Writer
    participant M as UrmaManager
    participant C as UrmaConnection
    participant U as URMA
    W->>M: UrmaWritePayload
    M->>C: GetJetty + GetTargetJetty
    loop chunks
        M->>M: CreateEvent(requestId, same jetty)
        M->>U: post WR(same send jetty)
    end
    U-->>M: CQE local_id=same jetty id
    M->>M: WaitToFinish/DeleteEvent
```

### TO BE

```mermaid
sequenceDiagram
    participant W as Writer
    participant M as UrmaManager
    participant C as UrmaConnection
    participant U as URMA
    W->>M: UrmaWritePayload
    loop chunks / one WR
        M->>C: AcquireSendLane
        C-->>M: lane(jetty,targetJetty)
        M->>M: CreateEvent(requestId, lane jetty)
        M->>U: post one WR(lane jetty)
        U-->>M: CQE local_id=lane jetty id
        M->>M: WaitToFinish/DeleteEvent
        M->>C: ReleaseSendLane(event jetty)
    end
```

## 2. CQE jetty failure

```mermaid
sequenceDiagram
    participant U as URMA
    participant P as Poll Thread
    participant R as UrmaResource
    participant C as UrmaConnection
    participant E as UrmaEvent
    U-->>P: CQE error(status=9, local_id=failedJetty)
    P->>R: GetJettyById(local_id)
    R-->>P: failedJetty
    P->>C: ReCreateJetty(failedJetty)
    C->>C: Find lane by failedJetty
    C->>C: MarkInvalid()
    C->>R: CreateJetty
    C->>R: ImportTargetJetty(remoteInfo,newJetty)
    C->>C: replace failed lane
    P->>E: SetFailed(status)
    E-->>C: DeleteEvent releases lane
```

## 3. AE jetty failure

```mermaid
sequenceDiagram
    participant A as Async Event Thread
    participant R as UrmaResource
    participant C as UrmaConnection
    participant E as In-flight Event
    A->>A: URMA_EVENT_JETTY_ERR(rawJetty)
    A->>R: GetJettyById(rawJetty.id)
    R-->>A: failedJetty
    A->>C: ReCreateJetty(failedJetty)
    C->>C: MarkInvalid()
    alt lane has in-flight WR
        C->>C: replace jetty but keep lane inUse=true
        E-->>C: CQE/timeout later DeleteEvent
        C->>C: ReleaseSendLane(old failedJetty)
    else no in-flight WR
        C->>C: replace jetty and lane idle
    end
```

## 4. NUMA affinity 普通路径

```mermaid
sequenceDiagram
    participant W as Worker/Client
    participant M as UrmaManager
    participant C as UrmaConnection
    participant U as URMA
    W->>M: UrmaWritePayload(srcChipId,dstChipId)
    M->>M: useNumaAffinity = flag && chips valid
    M->>C: AcquireSendLane
    C-->>M: lane(jetty,targetJetty)
    M->>M: INJECT_POINT UrmaWriteNumaAffinity
    M->>U: PostJettyRw(..., true, srcChipId, dstChipId)
```

## 5. 无空闲 lane

```mermaid
sequenceDiagram
    participant M as UrmaManager
    participant C as UrmaConnection
    participant R as UrmaResource
    M->>C: AcquireSendLane
    C->>C: no idle lane
    C->>R: TryAcquireSendLaneSlot
    alt budget available
        R-->>C: reserved
        C->>R: CreateJetty + ImportTargetJetty
        C-->>M: new lane
    else budget exhausted
        R-->>C: false
        C-->>M: K_TRY_AGAIN
        M->>M: nanosleep and retry until request deadline
        M-->>M: K_URMA_WAIT_TIMEOUT if no lane before deadline
    end
```
