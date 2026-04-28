# [RFC]：ZMQ RPC 队列时延可观测（自证清白 + 定界）

## 背景与目标描述

### 问题

当前 RPC 框架的延迟只能看到端到端（E2E）时间，当延迟高时无法定位瓶颈在哪个阶段：

```
E2E 延迟高 = Client 框架慢？ Client Socket 慢？ 网络慢？ Server 队列慢？ Server 执行慢？ Server 回复慢？
```

### 目标

通过在 `MetaPb.ticks` 中记录关键时间点，在 Client 侧计算各阶段延迟，实现：

1. **自证清白**：任何一个阶段的延迟都可以被独立识别
2. **网络延迟可计算**：通过 E2E - ServerExec 得出网络开销
3. **最小代价**：不新增 proto 字段，不修改网络协议，零网络开销
4. **进程内计算**：所有 Metric 在进程内完成统计

---

## 建议的方案

### Tick 定义（8个，零网络开销）

| Tick 名称 | 位置 | 进程 | 含义 |
|-----------|------|------|------|
| `CLIENT_ENQUEUE` | `mQue->SendMsg()` 之前 | Client | 入口时间戳 |
| `CLIENT_TO_STUB` | `RouteToZmqSocket()` 之前 | Client | 即将进入 ZmqFrontend |
| `CLIENT_SEND` | `RouteToZmqSocket()` 末尾 | Client | Socket 发送完成 |
| `CLIENT_RECV` | `AsyncReadImpl()` 收到响应后 | Client | Client 收到响应 |
| `SERVER_RECV` | `ClientToService()` ParseMsgFrames 之后 | Server | Socket 接收完成 |
| `SERVER_DEQUEUE` | `RouteToRegBackend()` lambda 执行前 | Server | Server 队列出队 |
| `SERVER_EXEC_END` | `WorkerEntryImpl()` SendStatus 之前 | Server | Server 业务处理完成 |
| `SERVER_SEND` | `ServiceToClient()` replyQueue->Put 之前 | Server | Server 回复已入队 |

### Metric 定义（7个 Histogram）

| Metric | 含义 | 计算公式 | 进程 | 可能根因 |
|--------|------|---------|------|---------|
| `ZMQ_CLIENT_QUEUING_LATENCY` | Client 队列等待 | `CLIENT_TO_STUB - CLIENT_ENQUEUE` | Client | MsgQue 队列堆积，prefetcher 处理不过来 |
| `ZMQ_CLIENT_STUB_SEND_LATENCY` | Client Stub 发送 | `CLIENT_SEND - CLIENT_TO_STUB` | Client | ZmqFrontend 线程繁忙，或 zmq_msg_send 慢 |
| `ZMQ_SERVER_QUEUE_WAIT_LATENCY` | Server 队列等待延迟 | `SERVER_DEQUEUE - SERVER_RECV` | Server | Server 请求队列堆积 |
| `ZMQ_SERVER_EXEC_LATENCY` | Server 业务执行延迟 | `SERVER_EXEC_END - SERVER_DEQUEUE` | Server | 业务逻辑慢 |
| `ZMQ_SERVER_REPLY_LATENCY` | Server 回复延迟 | `SERVER_SEND - SERVER_EXEC_END` | Server | Server 回复入队慢 |
| `ZMQ_RPC_E2E_LATENCY` | 端到端延迟 | `CLIENT_RECV - CLIENT_ENQUEUE` | Client | - |
| `ZMQ_RPC_NETWORK_LATENCY` | 网络延迟 | `E2E - SERVER_EXEC` | Client | 网络本身或 Server 框架慢 |

### 时间线示意

```
|<--- CLIENT_QUEUING --->|<-CLIENT_STUB_SEND->|<-SERVER_QUE_WAIT->|<-SERVER_EXEC->|<-SERVER_REPLY->|<-NETWORK->|
         │                       │                  │                │              │            │
         ▼                       ▼                  ▼                ▼              ▼            ▼
   ──────┴─────────────────────┴──────────────────┴────────────────┴──────────────┴────────────┴─────────────────►
   CLIENT_ENQUEUE           CLIENT_SEND         SERVER_RECV    SERVER_DEQUEUE  SERVER_EXEC_END  SERVER_SEND  CLIENT_RECV
                                                                                           │
                                                                              SERVER_EXEC_NS (计算值传回 Client)
```

### 核心等式

```
NETWORK_LATENCY = E2E_LATENCY - SERVER_EXEC_LATENCY

验证：
  E2E = CLIENT_QUEUING + CLIENT_STUB_SEND + SERVER_QUEUE_WAIT + SERVER_EXEC + SERVER_REPLY + NETWORK

如果 NETWORK 异常大：
  - CLIENT_QUEUING 高 → MsgQue 队列堆积
  - CLIENT_STUB_SEND 高 → ZmqFrontend 或 zmq_msg_send 慢
  - SERVER_QUEUE_WAIT 高 → Server 请求队列堆积
  - SERVER_REPLY 高 → Server 回复入队慢
  - SERVER_EXEC 正常 → 证明不是业务逻辑问题
```

---

## 改动文件

### 新增常量

**文件**: `src/datasystem/common/rpc/zmq/zmq_constants.h`

```cpp
// ==================== RPC Tracing Ticks ====================
inline constexpr const char* TICK_CLIENT_ENQUEUE = "CLIENT_ENQUEUE";
inline constexpr const char* TICK_CLIENT_TO_STUB = "CLIENT_TO_STUB";
inline constexpr const char* TICK_CLIENT_SEND = "CLIENT_SEND";
inline constexpr const char* TICK_CLIENT_RECV = "CLIENT_RECV";
inline constexpr const char* TICK_SERVER_RECV = "SERVER_RECV";
inline constexpr const char* TICK_SERVER_DEQUEUE = "SERVER_DEQUEUE";
inline constexpr const char* TICK_SERVER_EXEC_END = "SERVER_EXEC_END";
inline constexpr const char* TICK_SERVER_SEND = "SERVER_SEND";
```

### 新增 MetricId

**文件**: `src/datasystem/common/metrics/kv_metrics.h`

```cpp
ZMQ_RPC_SERIALIZE_LATENCY,
ZMQ_RPC_DESERIALIZE_LATENCY,
// Client 侧
ZMQ_CLIENT_QUEUING_LATENCY,       // CLIENT_TO_STUB - CLIENT_ENQUEUE
ZMQ_CLIENT_STUB_SEND_LATENCY,     // CLIENT_SEND - CLIENT_TO_STUB
// Server 侧
ZMQ_SERVER_QUEUE_WAIT_LATENCY,     // SERVER_DEQUEUE - SERVER_RECV
ZMQ_SERVER_EXEC_LATENCY,           // SERVER_EXEC_END - SERVER_DEQUEUE
ZMQ_SERVER_REPLY_LATENCY,         // SERVER_SEND - SERVER_EXEC_END
// E2E
ZMQ_RPC_E2E_LATENCY,             // CLIENT_RECV - CLIENT_ENQUEUE
ZMQ_RPC_NETWORK_LATENCY,          // E2E - SERVER_EXEC

WORKER_ALLOCATOR_ALLOC_BYTES_TOTAL,
```

### 改动文件清单

| 文件 | 改动说明 |
|------|---------|
| `zmq_stub_impl.cpp` | `AsyncWriteImpl`: +CLIENT_ENQUEUE; `AsyncReadImpl`: +CLIENT_RECV + 计算逻辑 |
| `zmq_stub_conn.cpp` | `RouteToZmqSocket()`: +CLIENT_TO_STUB, +CLIENT_SEND |
| `zmq_service.cpp` | `ClientToService()`: +SERVER_RECV; `RouteToRegBackend()`: +SERVER_DEQUEUE; `WorkerEntryImpl()`: +SERVER_EXEC_END; `ServiceToClient()`: +SERVER_SEND + SERVER_EXEC_NS 计算 + 计算逻辑 |

---

## 涉及到的变更

### 不修改的内容

- **不修改 proto**：复用现有的 `MetaPb.ticks` 字段
- **不修改网络协议**：Tick 存在 meta 中，随请求/响应自动传输
- **不修改 StatusCode**：无错误码变更
- **无新增依赖**：仅使用现有的 `metrics::Histogram` 框架

### 性能开销

- Tick 记录：`GetLapTime()` ~10ns/call
- Metric 计算：遍历 ticks 数组 ~50ns/call
- 总开销：~60ns/request（可忽略）

---

## 测试验证计划

### UT 测试用例

1. **Tick 传递测试**：验证 Server 追加的 tick 能正确传回 Client
2. **E2E 计算测试**：模拟 Client → Server → Client 完整流程
3. **Metric 记录测试**：验证各阶段延迟正确计算
4. **向后兼容测试**：旧 Server 无 SERVER_EXEC_NS 时 Client 行为

### 验证命令

```bash
# 构建
bazel build //src/datasystem/common/rpc/zmq:zmq_stub_impl
bazel build //src/datasystem/common/rpc/zmq:zmq_service

# UT
bazel test //tests/ut/common/rpc:zmq_rpc_queue_latency_test
```

---

## 期望的反馈时间

- 建议反馈周期：5~7 天
- 重点反馈：
  1. Tick 名称命名是否合适
  2. Metric 划分是否满足定界需求
  3. 计算位置是否正确

---

## PR #707 合并后：设计 vs 实现 差异清单

> 以下差异基于 `yuanrong-datasystem` master 分支（commit PR #707 合并后）的实际代码。

### 1. `SERVER_EXEC_END` 记录位置

| | 设计（issue-rfc.md） | 实现（PR #707） |
|--|---------------------|----------------|
| `SERVER_EXEC_END` 位置 | `WorkerEntryImpl()` SendStatus **之前** | `WorkerEntryImpl()` **返回之后** |

**原因**：`WorkerEntryImpl` 内部调用 `ServiceToClient`，`SERVER_EXEC_END` 必须在调用 `ServiceToClient` 之前记录，才能在 reply meta 中正确追加 tick。

### 2. `SERVER_EXEC_NS` 定义错误（Bug）

| | 设计意图 | 实现（PR #707） |
|--|---------|----------------|
| `SERVER_EXEC_NS` | `SERVER_EXEC_END - SERVER_DEQUEUE`（纯业务执行时间） | `SERVER_EXEC_END - SERVER_RECV`（包含 SERVER_QUEUE_WAIT） |

**影响**：`zmq_rpc_network_latency = E2E - SERVER_EXEC_NS` 实际上等于 `E2E - EXEC - QUEUE_WAIT`，不是纯网络耗时。

**修复方案**：`zmq_service.cpp L81` 改为：
```cpp
uint64_t serverExecNs = (serverExecEndTs > serverDequeuTs) ? (serverExecEndTs - serverDequeuTs) : 0;
```

### 3. `NETWORK` 语义不准确

设计说 `NETWORK = E2E - SERVER_EXEC`（纯网络耗时），但由于 Bug #2，实际 `NETWORK` 包含：
- CLIENT_STUB_SEND 之后的全部时间（request 发送 + 网络传播 + server 处理 + reply 接收）

### 4. offlineRpc 路径 tick 丢失（Bug）

**位置**：`zmq_service.cpp` L1040-1061

当 `offlineRpc = true` 时，`rpc2.first`（不含 `SERVER_SEND`）单独发送。不影响当前 metrics 计算（`RecordServerLatencyMetrics` 在 move 前调用），但 reply meta 诊断数据不完整。

### 5. `GetTotalTicksTime` 假设

```cpp
// zmq_constants.h L72-79
inline uint64_t GetTotalTicksTime(const MetaPb& meta) {
    auto n = meta.ticks_size();
    if (n > 1) {
        return meta.ticks(n - 1).ts() - meta.ticks(0).ts();  // 假设 ticks[0]=ENQUEUE
    }
    return 0;
}
```

正常流程下 `ticks[0]=CLIENT_ENQUEUE`、`ticks[n-1]=CLIENT_RECV` 成立。边界场景需验证。

### 差异影响分析

| 差异 | 严重程度 | 是否影响 metrics 正确性 |
|------|---------|----------------------|
| `SERVER_EXEC_END` 位置变更 | 低 | 不影响，语义不变 |
| `SERVER_EXEC_NS` 包含 QUEUE_WAIT | **高** | `NETWORK` 指标含义不准确 |
| offlineRpc tick 丢失 | 低 | 不影响 metrics，诊断数据不完整 |
| `GetTotalTicksTime` 假设 | 低 | 正常流程成立，需边界验证 |
