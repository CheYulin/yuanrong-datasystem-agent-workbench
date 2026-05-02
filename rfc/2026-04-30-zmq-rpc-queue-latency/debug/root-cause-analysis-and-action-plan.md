# ZMQ RPC Queue Latency — 根因分析与修复计划

> 整理自 0428 深夜代码深读，结合 `symptoms-and-hypotheses.md` 补充了新的可能性。
> 覆盖：`zmq_constants.h`、`zmq_stub_impl.h`、`zmq_stub_conn.cpp`、`zmq_service.cpp`、`zmq_common.h`、`zmq_msg_queue.h`

---

## 1. Tick 打点全景图（含所有路径）

```
Client (ZmqStubImpl)                    Server (ZmqService)
  │                                           │
  │ ① AsyncWriteImpl                          │
  │    RecordTick(CLIENT_ENQUEUE)              │
  │    mQue->SendMsg() ──────────────────────►│ ④ FrontendToBackend
  │                                           │    RecordTick(SERVER_RECV)
  │                                           │    → RouteToRegBackend
  │                                           │      → WorkerCB::WorkerEntry()
  │                                           │        RecordTick(SERVER_DEQUEUE)  ⑤
  │                                           │        WorkerEntryImpl()
  │                                           │        RecordTick(SERVER_EXEC_END) ⑥
  │                                           │        → ServiceToClient()
  │                                           │          RecordTick(SERVER_SEND)   ⑦
  │                                           │          RecordServerLatencyMetrics()
  │                                           │          (追加 SERVER_EXEC_NS tick)
  │◄──────────────────────────────────────────│
  │ ⑧ AsyncReadImpl                           │
  │    RecordTick(CLIENT_RECV)                │
  │    RecordRpcLatencyMetrics()              │
```

### Client 侧发送路径分歧（关键）

```
AsyncWriteImpl → mQue->SendMsg()
                       │
                       ▼
              BackendToFrontend (lambda in ZmqFrontend ctor)
                       │
          ┌────────────┴────────────────┐
          │  func()                    │
          │  if (cInfo == nullptr ||   │  ← ZMQ internal methods: heartbeat, etc.
          │      method == ZMQ_SOCKPATH_METHOD ||
          │      method == ZMQ_TCP_DIRECT_METHOD ||
          │      method == ZMQ_PAYLOAD_GET_METHOD ||
          │      method == ZMQ_STREAM_WORKER_METHOD) {
          │      → RouteToZmqSocket()         ✅ 有 CLIENT_SEND tick
          │  } else {
          │      → RouteToUnixSocket()        ❌ 没有 CLIENT_SEND tick ← A1 根因
          │  }
```

---

## 2. 所有根因（按可能性排序）

### 根因 A1：`RouteToUnixSocket` 跳过 `TICK_CLIENT_SEND`（已确认）

**位置**：`zmq_stub_conn.cpp` L128-169

`RouteToZmqSocket`（L109-126）在 `frontend_->SendAllFrames()` 之后调用 `RecordTick(meta, TICK_CLIENT_SEND)`（L124），但 `RouteToUnixSocket`（L128-169）末尾直接 return，没有任何 `RecordTick` 调用。

走 **UDS/TCP 直连**的 RPC（`ZmqFrontend::BackendToFrontend` 中 func lambda 走到 else 分支）不会有 `CLIENT_SEND` tick。

**影响**：`zmq_client_stub_send_latency` n=0（完全缺失）。

**触发条件**：
- RPC 走 `ZMQ_SOCKPATH_METHOD` / `ZMQ_TCP_DIRECT_METHOD` / `ZMQ_PAYLOAD_GET_METHOD` / `ZMQ_STREAM_WORKER_METHOD`
- 即：UDS 连接建立后，同一连接上的后续请求

---

### 根因 A2：`GetTotalTicksTime` 对 reused `MetaPb` 失效（已确认，新发现）

**位置**：`zmq_constants.h` L72-79

```cpp
inline uint64_t GetTotalTicksTime(const MetaPb& meta) {
    auto n = meta.ticks_size();
    if (n > 1) {
        return meta.ticks(n - 1).ts() - meta.ticks(0).ts();  // 假设 ticks[0]=ENQUEUE, ticks[n-1]=RECV
    }
    return 0;
}
```

正常情况：reply meta 的 ticks 顺序是 `[CLIENT_ENQUEUE, CLIENT_TO_STUB, CLIENT_SEND, SERVER_RECV, SERVER_DEQUEUE, SERVER_EXEC_END, SERVER_SEND, SERVER_EXEC_NS, CLIENT_RECV]`

- `ticks[0]` = `CLIENT_ENQUEUE` ✅
- `ticks[n-1]` = `CLIENT_RECV` ✅

**但如果 `MetaPb` 被重用**（reply 在某处没有 Clear 就被复用），ticks 数组前面残留旧的 tick：
- 旧 reply meta 残留 `[CLIENT_ENQUEUE_OLD, CLIENT_RECV_OLD]`
- 新 reply meta 变成 `[CLIENT_ENQUEUE_OLD, CLIENT_RECV_OLD, CLIENT_ENQUEUE_NEW, ..., CLIENT_RECV_NEW]`
- `ticks[0]` = `CLIENT_ENQUEUE_OLD`（旧的）
- `ticks[n-1]` = `CLIENT_RECV_NEW`（新的）

差值 = `CLIENT_RECV_NEW - CLIENT_ENQUEUE_OLD`，这个值可能：
- 极大（跨 RPC 的时间差）
- 或极小（恰好旧 ENQUEUE 和新 RECV 时间接近）

**关键问题**：这个函数是 `E2E` 的唯一数据源。如果它错了，`E2E` 指标整体失效。

---

### 根因 B1：`RecordServerLatencyMetrics` 每次 `Add` 而非覆写 `SERVER_EXEC_NS`（已确认）

**位置**：`zmq_service.cpp` L94-99

```cpp
// L94-99
uint64_t serverExecNs = (serverExecEndTs > serverRecvTs) ? (serverExecEndTs - serverRecvTs) : 0;
TickPb execTick;
execTick.set_ts(serverExecNs);
execTick.set_tick_name("SERVER_EXEC_NS");
meta.mutable_ticks()->Add(std::move(execTick));  // ⚠️ 是 Add，不是覆写
```

**`MetaPb` 重用路径**（`ServiceToClient` 末尾 L1043-1046）：

```cpp
RecordTick(meta, TICK_SERVER_SEND);
DumpServerTicks(meta);
RecordServerLatencyMetrics(meta);   // ← 这里 Add SERVER_EXEC_NS
```

如果同一个 `MetaPb` 对象被多次用于发送 reply（比如 `offlineRpc` 场景下的 `rpc2` 发送），每次都会追加一个 `SERVER_EXEC_NS` tick。当 reply meta 在 client 侧被 `FindTickTs` 查找时：

- `FindTickTs(meta, "SERVER_EXEC_NS")` 返回**第一个**匹配的 `SERVER_EXEC_NS` ts（旧值）
- 如果旧值远小于新值，`serverExecNs` 被计算成错误的值

**累积场景**：

```
offlineRpc=true 时，rpc2.first 是 m = p.first 的 copy（包含已有 SERVER_EXEC_NS_1）
rpc2 被单独发送 → 再次调用 ServiceToClient(rpc2)
→ 又追加一个 SERVER_EXEC_NS_2

reply meta 中有两个 SERVER_EXEC_NS tick：
ticks: [..., SERVER_EXEC_NS_1, SERVER_SEND, SERVER_EXEC_NS_2]
FindTickTs("SERVER_EXEC_NS") 返回 SERVER_EXEC_NS_1（旧的）
```

---

### 根因 B2：`FindTickTs` 返回第一个匹配而非最后一个（已确认）

**位置**：`zmq_service.cpp` L44-52 和 `zmq_stub_impl.h` L47-55

```cpp
inline uint64_t FindTickTs(const MetaPb &meta, const char *tickName) {
    for (int i = 0; i < meta.ticks_size(); i++) {   // 正向遍历 → 找到第一个
        if (meta.ticks(i).tick_name() == tickName) {
            return meta.ticks(i).ts();
        }
    }
    return 0;
}
```

**问题**：新的 tick 追加在数组**末尾**，而 `FindTickTs` 找**第一个**匹配。

如果 `MetaPb` 被重用于多次 RPC（即使有 Add 覆写机制，如果 Add 了多次），旧的 tick 排在前面，新的 tick 排在后面。`FindTickTs` 返回旧的 ts，导致差值计算错误。

**修复方向**：改为反向遍历，返回最后一个匹配：

```cpp
inline uint64_t FindTickTs(const MetaPb &meta, const char *tickName) {
    for (int i = meta.ticks_size() - 1; i >= 0; i--) {  // 改为反向
        if (meta.ticks(i).tick_name() == tickName) {
            return meta.ticks(i).ts();
        }
    }
    return 0;
}
```

---

### 根因 C1：`GetLapTime` 在 `ENABLE_PERF` 下追加 tick（次要）

**位置**：`zmq_common.h` L338-355

```cpp
inline uint64_t GetLapTime(MetaPb &meta, const char *tickName) {
#ifdef ENABLE_PERF
    auto ts = TimeSinceEpoch();
    auto n = meta.ticks_size();
    uint64_t diff = n > 0 ? (ts - meta.ticks(n - 1).ts()) : 0;
    TickPb tick;
    tick.set_ts(ts);
    tick.set_tick_name(tickName);
    meta.mutable_ticks()->Add(std::move(tick));  // ⚠️ 也会追加 tick
    return diff;
#else
    return 0;
#endif
}
```

`GetLapTime` 在 `ENABLE_PERF` 开启时也会往 `meta.ticks()` 追加 tick。如果 perf 测试路径也使用了同样的 `MetaPb` 对象，会造成 tick 累积。

---

### 根因 D1：`offlineRpc` 路径的 `rpc2.first` 携带累积 tick（已确认）

**位置**：`zmq_service.cpp` L1055-1061

```cpp
if (offlineRpc) {
    MetaPb m = p.first;     // ← copy，包含所有已有 tick（含 SERVER_EXEC_NS_1）
    m.set_payload_index(ZMQ_EMBEDDED_PAYLOAD_INX);
    rpc2.first = std::move(m);
    rpc2.second = std::move(payload);
}
```

如果 `p.first` 已经累积了多个 `SERVER_EXEC_NS`（因为 `RecordServerLatencyMetrics` 被调用多次），`m` copy 时全部带走，`rpc2` 发送时 `ServiceToClient` 再次调用 `RecordServerLatencyMetrics`，又追加一个 `SERVER_EXEC_NS`。

---

### 根因 D2：`high_resolution_clock` 多核时钟漂移（低可能）

**位置**：`zmq_constants.h` L57-60

```cpp
inline uint64_t GetTimeSinceEpoch() {
    return std::chrono::high_resolution_clock::now().time_since_epoch().count();
}
```

`high_resolution_clock` 在 WSL2 或多核服务器上，不同核心可能返回略有不同的值。如果 `SERVER_EXEC_END` 记录的 ts 因为线程调度到不同核心而比 `SERVER_RECV` 的 ts 更小（或接近），`serverExecEndTs > serverRecvTs` 条件不满足，`serverExecNs` 计算为 0。

---

## 3. 根因汇总表

|| ID | 根因 | 现象 | 可能性 | 文件:行 |
|---|-----|------|--------|---------|---------|
| A1 | `RouteToUnixSocket` 跳过 `CLIENT_SEND` tick | `zmq_client_stub_send_latency` 缺失 | **高** | `zmq_stub_conn.cpp:128` |
| A2 | `GetTotalTicksTime` 对 reused `MetaPb` 失效 | `E2E` 和 `NETWORK` 计算错误 | **高** | `zmq_constants.h:72` |
| B1 | `RecordServerLatencyMetrics` 每次 Add 而非覆写 | `SERVER_EXEC_NS` 累积 | **高** | `zmq_service.cpp:99` |
| B2 | `FindTickTs` 返回第一个匹配（旧 tick） | SERVER_EXEC≈0 / SERVER_REPLY 极端值 | **高** | `zmq_service.cpp:44` / `zmq_stub_impl.h:47` |
| C1 | `GetLapTime` 追加 tick | tick 累积 | 中 | `zmq_common.h:348` |
| D1 | `offlineRpc` 路径 copy 累积 tick | SERVER_EXEC_NS 多个 | 中 | `zmq_service.cpp:1057` |
| D2 | `high_resolution_clock` 多核时钟漂移 | SERVER_EXEC≈0 | 低 | `zmq_constants.h:57` |

**核心问题**：`MetaPb` 对象的 tick 生命周期管理缺失 —— `RecordTick`、`RecordServerLatencyMetrics`、`GetLapTime` 都只追加不清除，导致 `MetaPb` 被重用时 tick 污染。

---

## 4. 修复计划

### Step 1：修复 `RouteToUnixSocket` 缺失 `CLIENT_SEND` tick

**文件**：`src/datasystem/common/rpc/zmq/zmq_stub_conn.cpp`

**改动**：在 `RouteToUnixSocket` 函数末尾（L168）return 之前添加 `RecordTick`。

```cpp
// L168 附近，RouteToUnixSocket 末尾
    WriteLock lock(fdConn->outMux_.get());
    fdConn->outMsgQueue_->emplace_back(type, std::move(frames));
    RETURN_IF_NOT_OK(fdConn->outPoller_->SetPollOut(fdConn->outHandle_));
    RecordTick(meta, TICK_CLIENT_SEND);  // ← 新增
    return Status::OK();
```

**验证**：VLOG `DumpClientTicks` 会显示 `SEND` 不再为 0。

---

### Step 2：修复 `RecordServerLatencyMetrics` — 覆写而非 Add

**文件**：`src/datasystem/common/rpc/zmq/zmq_service.cpp`

**改动**：L94-99，将 `Add` 改为查找后覆写（若已存在则覆写 ts，否则新增）。

```cpp
// 改为：
uint64_t serverExecNs = (serverExecEndTs > serverRecvTs) ? (serverExecEndTs - serverRecvTs) : 0;
TickPb execTick;
execTick.set_ts(serverExecNs);
execTick.set_tick_name("SERVER_EXEC_NS");

// 查找是否已存在 SERVER_EXEC_NS，存在则覆写 ts，否则新增
bool found = false;
for (int i = 0; i < meta.ticks_size(); i++) {
    if (meta.ticks(i).tick_name() == "SERVER_EXEC_NS") {
        meta.mutable_ticks(i)->set_ts(serverExecNs);
        found = true;
        break;
    }
}
if (!found) {
    meta.mutable_ticks()->Add(std::move(execTick));
}
```

---

### Step 3：修复 `FindTickTs` — 返回最后一个匹配

**文件**：`src/datasystem/common/rpc/zmq/zmq_service.cpp`（匿名 namespace）和 `src/datasystem/common/rpc/zmq/zmq_stub_impl.h`

**改动**：两处 `FindTickTs` 都改为反向遍历，返回最后一个匹配。

```cpp
// zmq_service.cpp L44-52
inline uint64_t FindTickTs(const MetaPb &meta, const char *tickName) {
    for (int i = meta.ticks_size() - 1; i >= 0; i--) {  // 反向遍历
        if (meta.ticks(i).tick_name() == tickName) {
            return meta.ticks(i).ts();
        }
    }
    return 0;
}

// zmq_stub_impl.h L47-55 同样改动
```

---

### Step 4（可选防御性修复）：`GetTotalTicksTime` 增加保护

**文件**：`src/datasystem/common/rpc/zmq/zmq_constants.h`

`GetTotalTicksTime` 假设 `ticks[0]` 是 `CLIENT_ENQUEUE`、`ticks[n-1]` 是 `CLIENT_RECV`。可以增加防御性检查：

```cpp
inline uint64_t GetTotalTicksTime(const MetaPb& meta) {
    auto n = meta.ticks_size();
    if (n > 1) {
        auto first = meta.ticks(0).tick_name();
        auto last = meta.ticks(n - 1).tick_name();
        if (first == TICK_CLIENT_ENQUEUE && last == TICK_CLIENT_RECV) {
            return meta.ticks(n - 1).ts() - meta.ticks(0).ts();
        }
    }
    return 0;
}
```

---

### Step 5（可选）：统一 tick 生命周期管理

如果上述修复后仍有问题，说明 `MetaPb` 在某处被静默重用。更根本的修复是在 `CreateMetaData` 或 `RecordTick` 前确保 `meta.ticks().Clear()`。

---

## 5. 修复优先级

| 优先级 | 修复 | 理由 |
|--------|------|------|
| **P0** | Step 1: RouteToUnixSocket + CLIENT_SEND | 直接消除 `CLIENT_STUB_SEND` 指标 n=0 |
| **P0** | Step 3: FindTickTs 反向遍历 | 防御所有 tick 污染场景的最终安全网 |
| **P1** | Step 2: RecordServerLatencyMetrics 覆写 | 消除 SERVER_EXEC_NS 累积 |
| **P2** | Step 4: GetTotalTicksTime 防御性检查 | 保护 E2E 计算 |
| **P3** | 远程构建验证 | 编译 + 运行 latency test + 检查 VLOG |

---

## 6. 验证计划

### 构建（使用 `xqyun-32c32g` 远程主机）

```bash
# 远程构建
ssh xqyun-32c32g 'cd ~/workspace/git-repos/yuanrong-datasystem && \
  export DS_OPENSOURCE_DIR="$HOME/.cache/yuanrong-datasystem-third-party" && \
  bash build.sh -b bazel -t build -j 16'
```

### 运行 Latency Test

```bash
# 运行 server
# 运行 client，收集 VLOG
# 检查 DumpServerTicks / DumpClientTicks 输出
```

### 期望修复后的 VLOG 输出

**正常情况**：
```
[DEBUG ClientTicks] ticks_size=9
  tick[0] CLIENT_ENQUEUE ts=...
  tick[1] CLIENT_TO_STUB ts=...
  tick[2] CLIENT_SEND ts=...        ← A1 修复后不再为 0
  tick[3] SERVER_RECV ts=...
  tick[4] SERVER_DEQUEUE ts=...
  tick[5] SERVER_EXEC_END ts=...
  tick[6] SERVER_SEND ts=...
  tick[7] SERVER_EXEC_NS ts=...     ← B1 修复后只有一个
  tick[8] CLIENT_RECV ts=...
```

**Tick 污染场景（修复后应不再出现）**：
- `ticks_size` 不应大于预期的 9（E2E 单 RPC）
- 不应出现重复的 tick name
- `FindTickTs` 的各值不应出现跨 RPC 的异常小/大 ts
