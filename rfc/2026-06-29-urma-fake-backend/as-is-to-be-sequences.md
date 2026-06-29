# URMA Fake Backend - As-Is / To-Be Sequences

**Status**: Draft  
**Related**: [design.md](./design.md)

---

## 1. dlopen Dispatch

```mermaid
sequenceDiagram
    participant App as UrmaManager / business path
    participant Dl as urma_dlopen_util
    participant SDK as liburma.so
    participant Fake as ds_urma_fake_* ABI

    alt Real URMA
        App->>Dl: dlopen liburma
        Dl->>SDK: dlsym("urma_init")
        SDK-->>App: real urma_init
    else BUILD_WITH_URMA_FAKE
        App->>Dl: dlopen fake sentinel
        Dl-->>App: fake handle 0xFA1E0001
        App->>Dl: dlsym("urma_init")
        Dl->>Dl: lookup FAKE_ENTRY table
        Dl-->>App: ds_urma_fake_init
        App->>Fake: ds_urma_fake_init(attr)
    end
```

关键点：业务仍按 URMA SDK API 调用，fake 替换的是 dispatch 结果。

## 2. Register / Import Segment

```mermaid
sequenceDiagram
    participant A as Sender process
    participant FA as FakeUrmaBackend A
    participant UDS as UDS SOCK_SEQPACKET
    participant FB as FakeUrmaBackend B
    participant B as Receiver process

    A->>A: memfd_create + mmap businessVa
    A->>FA: register_seg(token, businessVa, size)
    FA->>FA: MemfdResolver finds backing fd
    FA->>FA: create FakeSeg(token, va, size)

    B->>FB: exchange_jfr_info(token, host, port, instanceId)
    FB->>FB: ImportEndpointRegistry[token] = endpoint
    B->>FB: import_seg(token, wireVa, size)
    FB->>UDS: connect endpoint host:port
    UDS->>FA: HELLO(token)
    FA->>UDS: HELLO_ACK(va, len) + SCM_RIGHTS(memfd fd)
    UDS-->>FB: fd + metadata
    FB->>FB: mmap fd at receiver mapping
    FB->>FB: create remote FakeSeg
```

To-Be 约束：已注册 endpoint 的 import 必须真实走 UDS，失败即失败，不回退成本地 shm 假成功。

## 3. UrmaWritePayload Fast Path

```mermaid
sequenceDiagram
    participant FT as fast_transport_manager_wrapper
    participant Fake as FakeUrmaBackend
    participant Mem as shared memfd page
    participant Remote as remote mapped view

    FT->>Fake: UrmaWritePayload(urmaInfo, localSeg, offsets)
    Fake->>Fake: resolve remote FakeSeg by token / va
    Fake->>Mem: memcpy local payload into shared page
    Mem-->>Remote: receiver sees bytes through mmap
    Fake-->>FT: Status::OK
```

fake 模式下 payload 不通过 UDS 传输。UDS 只用于 import 阶段交换 fd。

## 4. PostSendWr Async Path

```mermaid
sequenceDiagram
    participant App as UrmaManager
    participant Fake as FakeUrmaBackend
    participant Pool as FakeThreadPool
    participant Jfc as FakeJfc

    App->>Fake: post_send_wr(wr)
    Fake->>Fake: copy wr into PostSendSnapshot
    alt queue has capacity
        Fake->>Pool: Submit(lambda)
        Pool->>Fake: memcpy / inject behavior
        Pool->>Jfc: push completion record
        Jfc-->>App: eventfd wake / poll returns CR
    else queue full
        Fake-->>App: URMA_E_AGAIN
        App->>App: mark pre-request fallback / TCP fallback
    end
```

`PostSendSnapshot` 深拷贝 WR 字段，caller 可在返回后释放原始 WR。

## 5. Fallback Is Business Semantics

```mermaid
flowchart TD
    A["URMA operation"] --> B{"fake/real URMA failure?"}
    B -->|"URMA_E_AGAIN / CQE error / wait timeout"| C["UrmaManager returns URMA status"]
    C --> D{"fallback enabled?"}
    D -->|"yes"| E["TCP fallback / pre-request fallback"]
    D -->|"no"| F["return URMA error"]
    B -->|"success"| G["complete fast path"]
```

fake 的职责是稳定制造 URMA failure 信号；是否 fallback、如何限流、错误码如何暴露属于 Object/KV 业务语义。

## 6. Cleanup / Fork

```mermaid
sequenceDiagram
    participant Proc as process
    participant Fake as FakeUrmaBackend
    participant UDS as UDS listener
    participant Mem as memfd/shm mapping

    Proc->>Fake: atfork prepare / child handler / cleanup
    Fake->>UDS: close listener and connections
    Fake->>Mem: unmap fake mappings
    Fake->>Fake: clear SideTables and registries
    Note over Proc,Fake: child process must not inherit stale fake endpoints
```
