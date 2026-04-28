# ZMQ RPC Queue Latency — 现象汇总与根因假设

> 整理自 0428 日志快照 `yche-ascii-tree2.txt`，cycle=7，interval=10s，part=1/1
>
> 来源：`/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/docs/yche2.log:7`

---

## 1. 观察到的现象

### 1.1 Metrics 快照（来源：`yche2.log` cycle=7，interval=10s）

| Metric | n | avg | max | 状态 |
|--------|---|-----|-----|------|
| `zmq_client_queuing_latency` | 1527 | 21142ns ≈ **21µs** | 59960ns ≈ **60µs** | ✅ 正常 |
| `zmq_client_stub_send_latency` | — | — | — | ❌ **缺失** |
| `zmq_server_queue_wait_latency` | 35484 | 21425ns ≈ **21µs** | 112390ns ≈ **112µs** | ⚠️ n 偏高（见注） |
| `zmq_server_exec_latency` | — | — | — | ❌ **缺失** |
| `zmq_server_reply_latency` | — | — | **>10¹⁵ns，跳过** | ❌ 不可用 |
| `zmq_rpc_e2e_latency` | 1527 | 721052ns ≈ **721µs** | 1312620ns ≈ **1312µs** | ✅ 正常 |
| `zmq_rpc_network_latency` | 1527 | 721052ns ≈ **721µs** | 1312620ns ≈ **1312µs** | ⚠️ ≈E2E（逻辑矛盾） |

> **注 1**：`zmq_server_queue_wait_latency` 的 n=35484 远大于 e2e 的 n=1527，原因是多线程 server 路径有**多个 worker 线程**分别打点（`N` 个 worker × `M` 个 RPC 汇聚到同一个 histogram），而 e2e 是 client 侧单次计数。这是 histogram 累加的正常行为，不代表有问题。
>
> **注 2**：ns → µs 换算：`1µs = 1000ns`，图中 JSON 字段名仍为 `avg_us/max_us`，但实际 Observe 传入的是 ns。

### 1.2 异常值详解

```
zmq_rpc_e2e_latency        n=1527  avg=721µs  max=1312µs
zmq_rpc_network_latency    n=1527  avg=721µs  max=1312µs
                                   ↑ avg/max 完全相等
                                   NETWORK = E2E - SERVER_EXEC_NS
                                   若 SERVER_EXEC_NS≈0 则 NETWORK≈E2E

zmq_server_reply_latency   max 超过 10^15 ns 量级（约 11.6 天），被脚本跳过
```

---

## 2. Tick 记录位置回顾

```
Client                                          Server
  |                                                |
  | ① CLIENT_ENQUEUE (ZmqStubImpl.h L149)         |
  |    mQue->SendMsg() 之前                        |
  |                                                |
  | ② CLIENT_TO_STUB (ZmqFrontend L403)            |
  |    msgQue_->Send() 之前                        |
  |                                                |
  | ③ CLIENT_SEND (ZmqFrontend L124)              |
  |    SendAllFrames() 之前                        |
  |                                                |
  |================================================| TCP/IP
  |                                                |
  |                                                | ④ SERVER_RECV (ZmqService.cpp L1222)
  |                                                |    zmq_msg_recv 返回后
  |                                                |
  |                                                | → 入 worker 队列
  |                                                |
  |                                                | ⑤ SERVER_DEQUEUE (L755)
  |                                                |    WorkerEntry() 中 ReceiveMsg 后
  |                                                |
  |                                                | → 执行业务 handler (WorkerEntryImpl L728)
  |                                                |
  |                                                | ⑥ SERVER_EXEC_END (L762)
  |                                                |    WorkerEntryImpl 返回后
  |                                                |
  |                                                | → reply 通过 backendMgr_->SendMsg
  |                                                |   → BackendToFrontend (L1067-1074)
  |                                                |   → ServiceToClient (L1023-1065)
  |                                                |   → ⑦ SERVER_SEND (L1030)
  |                                                |     RecordTick + RecordServerLatencyMetrics
  |================================================| TCP/IP
  |                                                |
  | ⑧ CLIENT_RECV (ZmqStubImpl.h L213)            |
  |    收到完整 reply 后                           |
  |    RecordTick + RecordRpcLatencyMetrics          |
```

---

## 3. 各缺失/异常现象的根因假设

### 假设 A：`zmq_client_stub_send_latency` 缺失

**现象**：`FindTickTs(meta, TICK_CLIENT_SEND)` 返回 0，导致 `if (clientSendTs > clientToStubTs)` 条件不满足。

**可能的根因**（按可能性排序）：

#### A1（最可能）：`RouteToUnixSocket` 路径跳过了 CLIENT_SEND 记录

- `ZmqFrontend::SendMsg` → 记录 CLIENT_TO_STUB → 放入 `msgQue_`
- `BackendToFrontend` → 从 `msgQue_` 取消息 → 根据条件走 `RouteToUnixSocket`
- **`RouteToUnixSocket`（L128-168）中没有任何 `RecordTick` 调用**

```cpp
// ZmqFrontend::RouteToUnixSocket (zmq_stub_conn.cpp L128)
// 没有 RecordTick！
Status ZmqFrontend::RouteToUnixSocket(const std::shared_ptr<SockConnEntry> &connInfo,
                                      MetaPb &meta, ZmqMsgFrames &&frames)
{
    // ...
    WriteLock lock(fdConn->outMux_.get());
    fdConn->outMsgQueue_->emplace_back(type, std::move(frames));  // 直接入队
    RETURN_IF_NOT_OK(fdConn->outPoller_->SetPollOut(fdConn->outHandle_));
    return Status::OK();
}
```

- 当 RPC 走 **UDS/TCP direct 路径**时，CLIENT_SEND tick **不会被记录**
- `FindTickTs` 返回 0

#### A2（次可能）：Tick 在 protobuf 序列化/反序列化中丢失

- `MetaPb` 上的 `repeated TickPb ticks` 字段通过 protobuf wire format 传输
- 如果 `MetaPb` 在序列化/反序列化过程中某些 tick 没有被正确序列化（边界情况），reply meta 上的 tick 会少于 req meta
- CLIENT_TO_STUB 和 CLIENT_SEND 都在 reply meta 上被查找，如果丢失则无法计算

#### A3（低可能）：`ZmqStubImpl::PayloadTick` 的 tick 干扰

- `PayloadTick`（L199-216）是一个专用于 benchmark 工具的函数，返回 `perfRun = reply.first`
- 如果这个函数被用于某些测试场景，可能产生污染的 tick

---

### 假设 B：`zmq_rpc_network_latency ≈ zmq_rpc_e2e_latency`

**现象**：NETWORK = E2E - SERVER_EXEC_NS ≈ E2E，说明 SERVER_EXEC_NS ≈ 0。

**实测值**（cycle=7）：
- `zmq_rpc_e2e_latency` avg=721µs，max=1312µs
- `zmq_rpc_network_latency` avg=721µs，max=1312µs
- `avg` 和 `max` 完全相等 → `SERVER_EXEC_NS` 对 avg 的贡献 ≈ 0

**计算公式**：

```cpp
// zmq_stub_impl.h L67-68
uint64_t networkNs = (e2eNs > serverExecNs) ? (e2eNs - serverExecNs) : 0;
```

**SERVER_EXEC_NS 的计算**：

```cpp
// zmq_service.cpp L80-81
uint64_t serverExecNs = (serverExecEndTs > serverRecvTs) ? (serverExecEndTs - serverRecvTs) : 0;
```

**可能的根因**（按可能性排序）：

#### B1（最可能）：Tick 污染 — `FindTickTs` 返回旧的 tick

`FindTickTs` 是线性查找，返回**第一个**匹配的 tick name：

```cpp
// zmq_service.cpp L44-52
inline uint64_t FindTickTs(const MetaPb &meta, const char *tickName)
{
    for (int i = 0; i < meta.ticks_size(); i++) {
        if (meta.ticks(i).tick_name() == tickName) {
            return meta.ticks(i).ts();
        }
    }
    return 0;
}
```

如果 `MetaPb` 对象被重用（reused from a previous failed/old RPC），旧的 tick 还在上面：

```
旧 RPC ticks:  [SERVER_RECV@1000, SERVER_DEQUEUE@1050, SERVER_EXEC_END@1100, SERVER_SEND@1150]
新 RPC ticks:  [SERVER_RECV@2000, SERVER_DEQUEUE@2050, SERVER_EXEC_END@2100, SERVER_SEND@2150]
                 ↑
                 FindTickTs 找到的是这个（旧的时间戳）
```

`serverRecvTs = 1000`（旧），`serverExecEndTs = 2100`（新）：
- `SERVER_EXEC_NS = 2100 - 1000 = 1100`（看起来正常，但实际包含了旧的时间差）
- 如果新旧时间差巨大，`SERVER_EXEC_NS` 可能被计算成一个极端值

**但更关键的问题是**：如果 `serverExecEndTs <= serverRecvTs`（因为旧的 exec_end > 新的 recv），则 `serverExecNs = 0`，导致 `NETWORK ≈ E2E`。

#### B2（中等可能）：`high_resolution_clock` 时钟回跳（WSL2/Linux 多核）

`GetTimeSinceEpoch()` 使用 `std::chrono::high_resolution_clock::now()`：

```cpp
// zmq_constants.h L57-60
inline uint64_t GetTimeSinceEpoch()
{
    return std::chrono::high_resolution_clock::now().time_since_epoch().count();
}
```

在 WSL2 或多核服务器上，`high_resolution_clock` 在不同核心间可能有**时钟漂移或回跳**。如果 SERVER_EXEC_END 的 ts 因为调度到不同核心而比 SERVER_RECV 的 ts 更小（或相近），`serverExecEndTs > serverRecvTs` 的条件不满足，`serverExecNs` 就会是 0。

#### B3（低可能）：fast path 快速返回

Heartbeat 等内部方法走快速路径，`SERVER_EXEC_END` 可能在 `SERVER_RECV` 之后很短时间（甚至同时）打点，时钟精度导致差值为 0。

---

### 假设 C：`zmq_server_exec_latency` 缺失

**现象**：`FindTickTs(meta, TICK_SERVER_EXEC_END)` 或 `FindTickTs(meta, TICK_SERVER_DEQUEUE)` 返回 0。

**根因分析**：

#### C1（最可能）：Tick 污染（与假设 B 同源）

同上，`FindTickTs` 找到的是**旧的** SERVER_DEQUEUE tick（来自上一次 RPC 的 MetaPb），而**新的** SERVER_EXEC_END 还没被查找。条件 `if (serverExecEndTs > serverDequeuTs)` 不满足，不记录。

如果旧的 tick ts 远大于新的（因为旧的更早），则条件满足但计算出错误的值。如果旧的 tick ts 小于新的 exec_end 但新的 dequeue 被旧 dequeue 覆盖，则条件不满足。

#### C2（可能）：`WorkerEntry` 的 tick 路径在某些错误路径上被跳过

`WorkerEntry`（L743-771）中：

```cpp
// L755
RecordTick(meta, TICK_SERVER_DEQUEUE);
Status rc = WorkerEntryImpl(meta, inMsg.second, replyMsg);
// L762
RecordTick(meta, TICK_SERVER_EXEC_END);
```

如果 `WorkerEntryImpl` 在某些情况下提前返回（如 `HandleInternalRq` 处理内部方法），Tick 可能不完整。

---

### 假设 D：`zmq_server_reply_latency` max 异常大（10^15 ns 量级）

**现象**：`gen_kv_perf_report.py` 检测到 max 超过 `10^15` ns（约 11.6 天），直接跳过展示。

**根因分析**：

#### D1（最可能）：Tick 污染导致负数或极大差值

如果 `MetaPb` 被重用，旧的 SERVER_SEND tick（ts 很小）排在新的 SERVER_RECV tick（ts 很大）**之前**，`FindTickTs` 找到旧的 SERVER_SEND，而新的 SERVER_EXEC_END 被使用：

```
旧 RPC: SERVER_SEND@1000
新 RPC: SERVER_RECV@5000000000, SERVER_EXEC_END@5000010000, SERVER_SEND@5000020000

FindTickTs(SERVER_SEND) = 1000（旧的）
FindTickTs(SERVER_EXEC_END) = 5000010000（新的）

SERVER_REPLY = 1000 - 5000010000 = -5000009000（负数，但 uint64_t 溢出变成极大正值）
```

`uint64_t serverSendTs = 1000`，`int64_t serverExecEndTs = 5000010000`，由于 `serverSendTs > serverExecEndTs` 是 **unsigned 比较**，1000 > 5000010000 为 false，条件不满足。但如果 `serverSendTs` 的原始类型是 `int64_t`（代码中定义为 `int64_t serverSendTs`），则 `1000 > 5000010000` 为 false，不会记录。

但如果情况反过来——旧的 SERVER_EXEC_END（很大）vs 新的 SERVER_SEND（很小）——则 `serverSendTs = 5000020000`，`serverExecEndTs = 1100`（旧的），`serverSendTs > serverExecEndTs` 为 true，记录的差值 = `5000020000 - 1100 = 5000019000` ns ≈ 5000 秒——仍然是极端异常值。

#### D2（低可能）：offlineRpc 路径的 `rpc2.first` 没有 SERVER_SEND

```cpp
// zmq_service.cpp L1040-1046
if (offlineRpc) {
    MetaPb m = p.first;  // copy，包含 SERVER_SEND
    m.set_payload_index(ZMQ_EMBEDDED_PAYLOAD_INX);
    rpc2.first = std::move(m);  // rpc2.first 实际上有 SERVER_SEND（copy）
    rpc2.second = std::move(payload);
}
```

分析：这里 `m = p.first` 是 copy，`rpc2.first = std::move(m)` 把 copy 后的 m 移入 rpc2。`p.first` 本身还有 SERVER_SEND（因为 copy 时带着）。所以这个路径**不是**问题所在。但如果 offlineRpc 触发了某些边界情况，可能导致 `serverSendTs` 为 0 或极大值。

---

## 4. 根因汇总

| # | 假设 | 现象 | 可能性 | 验证方法 |
|---|------|------|--------|---------|
| A1 | `RouteToUnixSocket` 跳过了 CLIENT_SEND tick | CLIENT_STUB_SEND 缺失（n=0） | **高** | 加 VLOG 检查 RPC 走的路径 |
| A2 | protobuf 序列化丢失 tick | CLIENT_STUB_SEND 缺失 | 中 | 加 VLOG 打印 tick_size |
| B1 | `FindTickTs` 返回第一个（旧的）tick，导致 SERVER_EXEC_NS≈0 | NETWORK≈E2E（avg=721µs，max=1312µs，两者相等） | **高** | 加 VLOG 打印每个 tick 的 ts |
| B2 | `high_resolution_clock` 多核时钟漂移 | SERVER_EXEC_NS≈0 | 中 | 换 `steady_clock` 验证 |
| C1 | 与 B1 同源：Tick 污染使 SERVER_EXEC_END 条件不满足 | SERVER_EXEC 缺失（n=0） | **高** | 同 B1 |
| D1 | Tick 污染导致 SERVER_REPLY 差值为极端值 | SERVER_REPLY max>10¹⁵ns | **高** | 同 B1 |

**核心问题指向：`FindTickTs` 的"第一个匹配"语义 + `MetaPb` 对象可能被重用 = Tick 污染**

---

## 5. 已落地的 Debug VLOG

通过**独立辅助函数**实现，不改动原有函数逻辑：

### Server 侧

**函数定义**（`zmq_service.cpp` 匿名 namespace，`FindTickTs` 之后）：

```cpp
// Debug helper: dump all ticks in a MetaPb for latency debugging
inline void DumpServerTicks(const MetaPb &meta)
{
    VLOG(RPC_LOG_LEVEL) << "[DEBUG ServerTicks] ticks_size=" << meta.ticks_size();
    for (int i = 0; i < meta.ticks_size(); i++) {
        VLOG(RPC_LOG_LEVEL) << "  tick[" << i << "] " << meta.ticks(i).tick_name()
                            << " ts=" << meta.ticks(i).ts();
    }
    VLOG(RPC_LOG_LEVEL) << "  FindTickTs: RECV=" << FindTickTs(meta, TICK_SERVER_RECV)
                        << " DEQUEUE=" << FindTickTs(meta, TICK_SERVER_DEQUEUE)
                        << " EXEC_END=" << FindTickTs(meta, TICK_SERVER_EXEC_END)
                        << " SEND=" << FindTickTs(meta, TICK_SERVER_SEND);
}
```

**调用处**（`ServiceToClient`，原有逻辑完全不动）：

```cpp
RecordTick(meta, TICK_SERVER_SEND);
DumpServerTicks(meta);   // 新增
RecordServerLatencyMetrics(meta);
```

### Client 侧

**函数定义**（`zmq_stub_impl.h` 全局命名空间，`RecordRpcLatencyMetrics` 之前）：

```cpp
// Debug helper: dump all ticks in a MetaPb for latency debugging
inline void DumpClientTicks(const MetaPb &meta)
{
    VLOG(RPC_LOG_LEVEL) << "[DEBUG ClientTicks] ticks_size=" << meta.ticks_size();
    for (int i = 0; i < meta.ticks_size(); i++) {
        VLOG(RPC_LOG_LEVEL) << "  tick[" << i << "] " << meta.ticks(i).tick_name()
                            << " ts=" << meta.ticks(i).ts();
    }
    VLOG(RPC_LOG_LEVEL) << "  FindTickTs: ENQUEUE=" << FindTickTs(meta, TICK_CLIENT_ENQUEUE)
                        << " TO_STUB=" << FindTickTs(meta, TICK_CLIENT_TO_STUB)
                        << " SEND=" << FindTickTs(meta, TICK_CLIENT_SEND)
                        << " RECV=" << FindTickTs(meta, TICK_CLIENT_RECV);
}
```

**调用处**（`AsyncReadImpl`，原有逻辑完全不动）：

```cpp
RecordTick(rsp.first, TICK_CLIENT_RECV);
DumpClientTicks(rsp.first);  // 新增
RecordRpcLatencyMetrics(rsp.first);
```

### 期望看到的异常现象

1. `ticks_size` 大于预期（有污染的旧 tick）
2. 同一个 tick name 出现多次（第一次是旧的 ts）
3. `serverRecvTs` 或 `serverSendTs` 的 ts 值异常小（来自旧 RPC）
4. `serverExecNs` 接近 0 或为负（被当作 unsigned 处理）
