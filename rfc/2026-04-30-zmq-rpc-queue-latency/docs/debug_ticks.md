# ZMQ RPC Tick Debugging Guide

## Overview

This document describes the tick recording and latency metrics debugging functions for ZMQ RPC.

## Generic Tick Helper Functions

### `DumpTicksFormatted(meta, prefix)` - Dump all ticks as single LOG(ERROR)

Location: `zmq_constants.h`

```cpp
inline void DumpTicksFormatted(const MetaPb& meta, const char* prefix = "TICK")
```

**Purpose**: Dump all ticks in MetaPb as a single formatted LOG(ERROR).

**Output Example**:
```
[TICK] tick_name=CLIENT_ENQUEUE | tick_name=CLIENT_STUB_SEND | tick_name=CLIENT_DEQUEUE
```

**Usage**: Easy to enable/disable for debugging:
```cpp
DumpTicksFormatted(clientMeta, "CLIENT_LATENCY");
```

### `DumpTicks(meta, prefix)` - Dump each tick as separate LOG(ERROR)

```cpp
inline void DumpTicks(const MetaPb& meta, const char* prefix = "TICK")
```

**Purpose**: Dump each tick as separate LOG(ERROR) line.

**Output Example**:
```
[TICK] CLIENT_ENQUEUE=1777460739013202198
[TICK] CLIENT_STUB_SEND=1777460739013238230
[TICK] CLIENT_DEQUEUE=1777460739018790776
```

### `GetAllTicks(meta)` - Get all ticks as vector

```cpp
inline std::vector<TickTimestamp> GetAllTicks(const MetaPb& meta)
```

**Purpose**: Iterate all ticks programmatically.

**Usage**:
```cpp
auto ticks = GetAllTicks(clientMeta);
for (const auto& t : ticks) {
    LOG(ERROR) << t.name << "=" << t.ts;
}
```

### `FindTick(meta, tickName)` - Find specific tick by name

```cpp
inline int64_t FindTick(const MetaPb& meta, const char* tickName)
```

**Purpose**: Find a specific tick by name, returns 0 if not found.

**Usage**:
```cpp
int64_t enqueueTs = FindTick(meta, TICK_CLIENT_ENQUEUE);
int64_t recvTs = FindTick(meta, TICK_CLIENT_RECV);
```

## Debug Functions

### Client-Side: `RecordClientLatencyMetrics` (in `zmq_constants.h`)

```cpp
static inline void RecordClientLatencyMetrics(const MetaPb& clientMeta)
```

**Purpose**: Debug function to print all client-side ticks and calculated metrics using `LOG(ERROR)`.

**Output Example**:
```
[CLIENT_LATENCY] CLIENT_ENQUEUE=... | CLIENT_STUB_SEND=... | CLIENT_DEQUEUE=... | CLIENT_ZMQ_SEND=... | CLIENT_RECV=...
[CLIENT_LATENCY] CLIENT_STUB_SEND=5552546 ns
[CLIENT_LATENCY] CLIENT_QUEUING=0 ns (MISSING if ZMQ_SEND=0)
```

**Usage**: Call after merging client ticks in `AsyncReadImpl`:

```cpp
const MetaPb &clientMeta = asyncCall->GetClientMeta();
RecordClientLatencyMetrics(clientMeta);  // Add debug log
```

### Server-Side: `RecordServerLatencyMetrics` (in `zmq_constants.h`)

```cpp
static inline void RecordServerLatencyMetrics(MetaPb &meta)
```

**Purpose**: Debug function to print all server-side ticks and calculated metrics using `LOG(ERROR)`.

**Output Example**:
```
[SERVER_LATENCY] SERVER_RECV=... | SERVER_DEQUEUE=... | SERVER_EXEC_END=... | SERVER_ZMQ_SEND=...
[SERVER_LATENCY] SERVER_QUEUE_WAIT=258481 ns
[SERVER_LATENCY] SERVER_EXEC=1904426 ns
[SERVER_LATENCY] SERVER_REPLY=5073 ns
[SERVER_LATENCY] SERVER_EXEC_NS=2162907 ns (total exec from recv)
```

**Usage**: Call in `ZmqServerImpl::ServiceToClient` after `zmq_msg_send`:

```cpp
RecordTick(meta, TICK_SERVER_ZMQ_SEND);
RecordServerLatencyMetrics(meta);  // Add debug log
PushFrontProtobufToFrames(meta, frames);
frontend_->SendAllFrames(...);
```

## Tick Constants

### Client Ticks

| Constant | Definition | Location |
|----------|------------|----------|
| `TICK_CLIENT_ENQUEUE` | Message put into outBound queue | `zmq_stub_impl.h` L163 |
| `TICK_CLIENT_STUB_SEND` | SendMsg returns, waiting for prefetcher | `zmq_stub_impl.h` L172 |
| `TICK_CLIENT_DEQUEUE` | Prefetcher dequeues from queue | `zmq_msg_queue.h` L682 |
| `TICK_CLIENT_ZMQ_SEND` | zmq_msg_send completes | ❌ MISSING (architectural) |
| `TICK_CLIENT_RECV` | Client receives response | `zmq_stub_impl.h` L232 |

### Server Ticks

| Constant | Definition | Location |
|----------|------------|----------|
| `TICK_SERVER_RECV` | zmq_msg_recv returns | `zmq_service.cpp` L1189 |
| `TICK_SERVER_DEQUEUE` | Dequeued from worker queue | `zmq_service.cpp` L722 |
| `TICK_SERVER_EXEC_END` | Business handler completes | `zmq_service.cpp` L729 |
| `TICK_SERVER_SEND` | Legacy - to be removed | `zmq_server_stream_base.h` L375 |
| `TICK_SERVER_ZMQ_SEND` | zmq_msg_send succeeds | `zmq_server_impl.cpp` L187 |

## Known Issues

### 1. CLIENT_ZMQ_SEND MISSING

**Problem**: `CLIENT_ZMQ_SEND` tick cannot be recorded due to architectural limitation.

**Root Cause**:
- `SendDirect` runs in prefetcher thread
- `clientMeta` is stored in AsyncWriteImpl thread
- Tick recorded in prefetcher cannot propagate to `clientMeta`

**Impact**: `CLIENT_QUEUING` metric shows `MISSING`.

**Workaround**: Use `ZMQ_CLIENT_QUEUING_LATENCY` histogram which may be populated through other paths.

### 2. TICK_SERVER_SEND vs TICK_SERVER_ZMQ_SEND

**Problem**: `TICK_SERVER_SEND` is still being recorded in multiple places but the semantic is wrong.

**Solution**:
- Remove `TICK_SERVER_SEND` usage from `zmq_server_stream_base.h` and `zmq_service.cpp`
- Use only `TICK_SERVER_ZMQ_SEND` in `ZmqServerImpl::ServiceToClient`

## Metric Formulas

### Client Metrics

| Metric | Formula | Status |
|--------|---------|--------|
| `CLIENT_QUEUING` | `CLIENT_ZMQ_SEND - CLIENT_ENQUEUE` | ❌ MISSING |
| `CLIENT_STUB_SEND` | `CLIENT_DEQUEUE - CLIENT_STUB_SEND` | ✓ Working |

### Server Metrics

| Metric | Formula | Status |
|--------|---------|--------|
| `SERVER_QUEUE_WAIT` | `SERVER_DEQUEUE - SERVER_RECV` | ✓ Working |
| `SERVER_EXEC` | `SERVER_EXEC_END - SERVER_DEQUEUE` | ✓ Working |
| `SERVER_REPLY` | `SERVER_ZMQ_SEND - SERVER_EXEC_END` | ✓ Working |

### E2E Metrics

| Metric | Formula | Status |
|--------|---------|--------|
| `RPC_E2E` | `CLIENT_RECV - CLIENT_ENQUEUE` | ✓ Working |
| `RPC_NETWORK` | `E2E - SERVER_EXEC` | ✓ Working |

## Debugging Tips

1. **Enable ERROR logs**: Pass `--logtostderr=1` to see tick logs
2. **Filter logs**: `grep "TICK\|LATENCY" <logfile>`
3. **Check tick sequence**: Ensure ticks are in chronological order
4. **Verify timestamps**: All timestamps should be nanoseconds since epoch

## Adding/Removing Debug Logs

### Easy Toggle with `DumpTicksFormatted`

To enable debug logging, simply add:
```cpp
DumpTicksFormatted(clientMeta, "CLIENT_LATENCY");
```

To disable, comment out or remove the line. No need to modify multiple places.

### Quick Tick Iteration Example

```cpp
// Dump all ticks
DumpTicksFormatted(meta, "DEBUG");

// Find specific tick
int64_t ts = FindTick(meta, TICK_CLIENT_ENQUEUE);

// Iterate programmatically
for (const auto& t : GetAllTicks(meta)) {
    LOG(ERROR) << t.name << "=" << t.ts;
}
```
