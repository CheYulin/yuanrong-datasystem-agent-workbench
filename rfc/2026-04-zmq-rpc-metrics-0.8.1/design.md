# Design: ZMQ RPC Metrics ENABLE_PERF=false 修复方案

本设计与 [PR #706](https://gitcode.com/openeuler/yuanrong-datasystem/pull/706) 一致，目标是在 **`ENABLE_PERF=false`** 的生产/默认编绎下仍能通过 `MetaPb.ticks` 驱动 **7 个 Histogram**（队列/网络/E2E 分段），不新增网络字段。

**最小化原则**：不重复发明 tick 名与公式；0.8.1 上若与 master 有 diff，以 PR #706 的语义为准做冲突合并。

## 1. 问题

### 1.1 现象

当 `ENABLE_PERF=false` 时：
- `GetLapTime()` 返回 0，不记录 tick
- `GetTotalTime()` 返回 0
- ZMQ RPC metrics 无法分段时间

### 1.2 影响

无法通过 metrics 自证清白：
- network 延迟（`zmq_server_queue_wait_latency`）
- RPC framework 回复时间（`zmq_server_reply_latency`）

## 2. 修复方案

### 2.1 新增 / 落地函数（与 0.8.1 源码一致）

**墙钟 tick** 在 `zmq_constants.h`：`RecordTick` 只追加 **当前 `GetTimeSinceEpoch()`（ns）**，**不**把 lap 写进 `TickPb.ts`（与早期草稿里「diff 写入 ts」不同）。

```cpp
// zmq_constants.h（摘录）
inline uint64_t RecordTick(MetaPb& meta, const char* tickName)
{
    auto ts = GetTimeSinceEpoch();
    TickPb tick;
    tick.set_ts(ts);
    tick.set_tick_name(tickName);
    meta.mutable_ticks()->Add(std::move(tick));
    return ts;
}

inline uint64_t ZmqDurationNsToMetricUs(uint64_t durationNs)
{
    return (durationNs + 500u) / 1000u;
}
```

客户端 **E2E / network** 等在 `zmq_stub_impl.h` 的 `RecordRpcLatencyMetrics` 中，用 **墙钟 tick 差** 与 **`SERVER_EXEC_NS` 的 duration 字段** 组合计算（见 [sequence_diagram.puml](sequence_diagram.puml)）。

### 2.2 修改后的 GetLapTime/GetTotalTime

```cpp
// GetLapTime：保持原有行为，ENABLE_PERF 关闭时返回 0
inline uint64_t GetLapTime(MetaPb &meta, const char *tickName)
{
#ifdef ENABLE_PERF
    return RecordTick(meta, tickName);
#else
    (void)meta;
    (void)tickName;
    return 0;
#endif
}

// GetTotalTime：保持原有行为，ENABLE_PERF 关闭时返回 0（实现为末 tick − 首 tick 墙钟差）
inline uint64_t GetTotalTime(MetaPb &meta)
{
#ifdef ENABLE_PERF
    auto n = meta.ticks_size();
    if (n > 0) {
        return meta.ticks(n - 1).ts() - meta.ticks(0).ts();
    }
#else
    (void)meta;
#endif
    return 0;
}
```

### 2.3 修改 metrics 记录函数

将 **metrics 所需** 墙钟链从「仅 `GetLapTime`（PERF 关则全 0）」改为 **`zmq_constants.h` 的 `RecordTick`**（始终写墙钟）；客户端汇总用 **`RecordRpcLatencyMetrics`**（见 `zmq_stub_impl.h`）。

| 文件 | 原调用 / 问题 | 新调用 / 行为 |
|------|--------------|---------------|
| `zmq_service.cpp` | `GetLapTime` 在 PERF 关为 0 | `RecordTick(meta, TICK_SERVER_DEQUEUE)` 等 |
| `zmq_stub_conn.cpp` | 同上 | `RecordTick(meta, TICK_CLIENT_SEND)` 等 |
| `zmq_stub_impl.h` | 无墙钟则无法算 E2E / network | **`RecordRpcLatencyMetrics(meta)`**（CLIENT_RECV 后） |

### 2.4 时间单位转换

墙钟 tick 的 `ts` 为 **ns**；`kv_metrics` 中 ZMQ 分段直方图统一 **`ZmqDurationNsToMetricUs(Δns)`** 再 **`Histogram::Observe(...)`**（四舍五入到 µs，与 `METRIC_TIMER` / `zmq_send_io_latency` 一致）。

### 2.5 服务端回包路径（与设计图一致）

| 路径 | 行为 |
|------|------|
| `ZmqService::ServiceToClient` | 调用 **`ZmqRecordServerSendLatencyMetrics(meta)`**：`RecordTick(SERVER_SEND)` + `RecordServerLatencyMetrics`（`zmq_server_queue_wait` / `exec` / `reply` + 追加 **`SERVER_EXEC_NS`**） |
| `WorkAgent::ServiceToClient`（DirectExec / 独占） | **同一** `ZmqRecordServerSendLatencyMetrics`，避免只走 WorkAgent 时 server 分段从不 Observe |
| `WorkerEntryWithoutMsgQ` | 与 `WorkerEntry` 对齐：在业务前后打 **`SERVER_DEQUEUE` / `SERVER_EXEC_END`**，否则 `EXEC_END` 缺失会导致错误 reply 样本或全缺 |

`RecordServerLatencyMetrics` 对 **同名 tick 取最后一次 ts**、并对缺失 tick 做 **守卫**（见 `zmq_service.cpp` 与 sequence_diagram）。

## 3. 改动文件清单（0.8.1 核对）

| 文件 | 改动类型 |
|------|---------|
| `zmq_constants.h` | `RecordTick` / `GetTimeSinceEpoch` / `ZmqDurationNsToMetricUs`（墙钟 tick + µs 转换） |
| `zmq_common.h` | `GetLapTime` / `GetTotalTime` 在 `ENABLE_PERF=false` 时仍为 0；与 metrics 用 `RecordTick` 分离 |
| `zmq_service.cpp` | `RecordTick(SERVER_RECV/DEQUEUE/EXEC_END)`；**`ZmqRecordServerSendLatencyMetrics`**；**`RecordServerLatencyMetrics`**（守卫 + `FindLastTickTs` + **`SERVER_EXEC_NS`**） |
| `zmq_service.h` | 声明 `ZmqRecordServerSendLatencyMetrics` |
| `work_agent.cpp` | 回包前调用 **`ZmqRecordServerSendLatencyMetrics`**（与 `ServiceToClient` 对齐） |
| `zmq_stub_conn.cpp` | 关键路径 `RecordTick` 替代仅 `GetLapTime` |
| `zmq_stub_impl.h` | **`RecordRpcLatencyMetrics`**（client 侧 4 段 + E2E + network） |
| `kv_metrics.h/cpp` | 7 个 Queue Flow Latency 及既有 ZMQ I/O 直方图 |

## 4. 兼容性

- `ENABLE_PERF=true`：行为不变
- `ENABLE_PERF=false`：
  - `GetLapTime()`/`GetTotalTime()` 仍返回 0（保持原有行为）
  - **`RecordTick()`**（`zmq_constants.h`）与 **`RecordServerLatencyMetrics` / `RecordRpcLatencyMetrics`** 仍写墙钟与直方图
  - ZMQ 分段 metrics 正常工作

## 5. 相关 Tick 定义

| Tick 名称 | 进程 | 用途 |
|-----------|------|------|
| `CLIENT_ENQUEUE` | Client | 入口时间戳 |
| `CLIENT_TO_STUB` | Client | 进入 ZmqFrontend |
| `CLIENT_SEND` | Client | Socket 发送完成 |
| `CLIENT_RECV` | Client | 收到响应 |
| `SERVER_RECV` | Server | Socket 接收完成 |
| `SERVER_DEQUEUE` | Server | 队列出队 |
| `SERVER_EXEC_END` | Server | 业务处理完成 |
| `SERVER_SEND` | Server | 回复发送路径上打点（与 `ZmqRecordServerSendLatencyMetrics` 内 `RecordTick` 一致） |
| `SERVER_EXEC_NS` | Server | **派生**：`ts` = **(SERVER_EXEC_END − SERVER_RECV) ns**，duration 语义；供 client 算 **rpc_network**，勿当墙钟与邻 tick 乱减 |
