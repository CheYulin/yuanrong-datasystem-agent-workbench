# ZMQ TCP/RPC Metrics 定界可观测方案

**仓库**：`yuanrong-datasystem`（分支 `metrics`，已包含 PR #584 metrics commit `20ce4860`）  
**依赖**：`datasystem::metrics` 轻量级框架（Counter / Gauge / Histogram + 周期 LOG(INFO) 输出）  
**关联**：URMA/TCP 定界修复需求（同目录 `urma-tcp-定界修复需求/`）

---

## 一、背景与目标

### 问题

当前 RPC 框架（基于 ZMQ）出现网卡故障、连接断开、对端 hang 住等网络问题时，无法从日志或指标快速定界：

1. **`zmq_msg_send` / `zmq_msg_recv` 失败无计数指标**：只有 `PerfPoint` 记录时延，没有 send/recv 失败计数，无法从 metrics 维度发现通信故障。
2. **errno 信息逐层丢失**：`ZmqErrnoToStatus` 中 errno 仅存为 string，上层多次 StatusCode 重映射后原始故障信息湮没。
3. **ZMQ Monitor 事件无指标**：disconnect / connect / handshake 事件仅日志输出且有去重限频，无法观测趋势。
4. **连接健康状态不可观测**：gateway 重建等关键事件无指标导出。
5. **定界链路断裂**：运维无法区分"zmq socket 层网络故障" vs "RPC 队列拥塞" vs "业务处理慢"。

### 目标

利用已合入的 `datasystem::metrics` 框架，在 ZMQ 通信栈建立指标，实现：

- **10 秒内定界**：通过 metrics summary 的 delta 判断是哪一层先异常
- **区分网络故障 vs 逻辑故障**：`zmq.net_error` + `zmq.last_errno` 直接标识网卡类问题
- **零新增依赖**：复用已有 Counter / Gauge + `LOG(INFO)` 周期输出
- **不修改错误码**：不改 StatusCode 枚举，不影响 proto 兼容性

---

## 二、Metrics 框架能力分析（基于当前分支实际代码）

### 2.1 API 摘要

```cpp
namespace datasystem::metrics {
// 注册：传入 MetricDesc 数组，id 范围 [0, 1024)，全量替换式
Status Init(const MetricDesc *descs, size_t count);
void Start();   // 启动周期输出线程（受 FLAGS_log_monitor 控制）
void Stop();    // 停止并输出最终 summary

// 操作：通过 id 获取句柄，所有操作内部校验 Valid()
Counter GetCounter(uint16_t id);   // .Inc(delta=1)  — fetch_add relaxed
Gauge GetGauge(uint16_t id);       // .Set(val) / .Inc(delta) / .Dec(delta)
Histogram GetHistogram(uint16_t id); // .Observe(val) — count+sum+max
ScopedTimer(uint16_t id);         // RAII，析构时 Observe(elapsed_us)

// 测试辅助
std::string DumpSummaryForTest(int intervalMs = 10000);
void ResetForTest();
}
```

### 2.2 关键实现特征

| 特征 | 说明 | 对本方案的影响 |
|------|------|--------------|
| **全量 `std::atomic` relaxed**  | `Counter::Inc()` = 单条 `fetch_add(relaxed)`，无锁 | 失败路径打点开销 ≈ 1 次原子加，可接受 |
| **`Valid()` 内部校验** | 每次操作先检查 `g_inited && id < 1024 && used && type match` | 即使未 Init 也不会 crash，降级为 no-op |
| **`Init()` 全量替换** | 调用 `Init` 会 `Stop() + ClearAll()`，不支持增量注册 | **关键约束**：所有模块的 MetricDesc 需合并为一个数组，或约定不同 ID 段 |
| **Summary 输出格式** | `Total:` + `Compare with Xms before:` (delta) | delta 正好是定界所需：`+0` = 正常，`+N` = 有异常 |
| **`FLAGS_log_monitor` 开关** | `Start()` 受此 flag 控制；不开则不启动周期线程 | metric 值照常累加，只是不自动输出；可通过 `DumpSummaryForTest` 拉取 |
| **ID 空间 `[0, 1024)`** | `MAX_METRIC_NUM = 1024` | ZMQ 指标分配 100-109 段 |

### 2.3 输出示例（模拟 ZMQ 指标注册后）

```
Metrics Summary, version=v0, cycle=5, interval=10000ms

Total:
zmq.send.fail=3
zmq.recv.fail=7
zmq.send.eagain=0
zmq.recv.eagain=2
zmq.net_error=5
zmq.last_errno=113
zmq.gw_recreate=1
zmq.evt.disconn=2
zmq.evt.hs_fail=0

Compare with 10000ms before:
zmq.send.fail=+0
zmq.recv.fail=+2        ← 10s 内新增 2 次 recv 硬失败
zmq.send.eagain=+0
zmq.recv.eagain=+1      ← blocking recv 超时
zmq.net_error=+2        ← 网络类错误 → 大概率网卡/网络问题
zmq.last_errno=+0       ← gauge delta (113=EHOSTUNREACH)
zmq.gw_recreate=+0
zmq.evt.disconn=+0
zmq.evt.hs_fail=+0
```

**定界结论**：delta 里 `zmq.recv.fail=+2`, `zmq.net_error=+2`, `last_errno=113(EHOSTUNREACH)` → **网卡/网络层故障**。

---

## 三、设计原则

1. **只在失败/异常路径打点，成功路径零开销**。定界只需看失败侧 delta 是否从 0 变非 0。已有 `PerfPoint`（`ZMQ_SOCKET_SEND_MSG` / `ZMQ_SOCKET_RECV_MSG`）的 count 字段可作为吞吐参考。
2. **Layer 2 连接管理只采集低频异常事件**。gateway 重建、monitor 断连等本身是低频事件（秒级/分钟级），一次 `atomic fetch_add` 对这些路径无影响。
3. **Layer 3 队列拥塞复用已有 `CheckHWMRatio` 日志**，不在消息队列热路径加打点。

---

## 四、架构：两层指标 + 日志

```
┌──────────────────────────────────────────────────────────────┐
│  已有信号: 消息队列 (zmq_service.cpp CheckHWMRatio)            │
│  → 已有 LOG(WARNING) 60%/80%/100% 阈值日志，不加新指标         │
│  定界：本端处理能力不足 / 对端消费慢                             │
├──────────────────────────────────────────────────────────────┤
│  Layer 2: 连接管理 — 仅异常事件 (低频，非关键路径)               │
│  → gateway 重建、monitor disconnect/handshake_fail              │
│  定界：连接不稳定 / peer 被判死                                 │
├──────────────────────────────────────────────────────────────┤
│  Layer 1: ZMQ Socket 读写 — 仅失败路径 (zmq_socket_ref)        │
│  → send/recv 硬失败、EAGAIN、网络错误、最近 errno               │
│  定界：zmq_msg_send/recv 故障 → 网卡/TCP 层问题                │
└──────────────────────────────────────────────────────────────┘
```

**定界逻辑**：Layer 1 指标异常 → 网络/socket 层；Layer 2 指标异常 → 连接管理层；仅队列日志异常 → 业务/拥塞层。

---

## 五、Metric 清单（共 9 个）

### Layer 1 — ZMQ Socket 读写（仅失败路径）

> 采集位置：`zmq_socket_ref.cpp`（唯一调用 `zmq_msg_send` / `zmq_msg_recv` 的文件）  
> 打点时机：**仅当 `zmq_msg_send` / `zmq_msg_recv` 返回 -1 时**，成功路径零开销

| Metric Name | Type | ID | 触发条件 | 定界作用 |
|-------------|------|----|---------|---------|
| `zmq.send.fail` | Counter | 100 | `SendMsg` 返回 -1 且 errno 非 EAGAIN/EINTR | send 硬失败 |
| `zmq.recv.fail` | Counter | 101 | `RecvMsg` 返回 -1 且 errno 非 EAGAIN/EINTR | recv 硬失败 |
| `zmq.send.eagain` | Counter | 102 | `SendMsg` errno == EAGAIN | HWM 背压信号 |
| `zmq.recv.eagain` | Counter | 103 | `RecvMsg` errno == EAGAIN **且 blocking 模式** | 超时信号 |
| `zmq.net_error` | Counter | 104 | errno 属于网络类（见下文） | **网卡/网络定界核心** |
| `zmq.last_errno` | Gauge | 105 | 最近一次硬失败的 errno 值 | 快速查看故障类型 |

> **`recv.eagain` 说明**：`ZmqRecvFlags::DONTWAIT`（值 `ZMQ_DONTWAIT`）模式下 EAGAIN 是正常返回，不计数；`ZmqRecvFlags::NONE`（值 0，blocking）模式下 EAGAIN 意味着 `ZMQ_RCVTIMEO` 超时，才有定界意义。
>
> **实现依据**：`ZmqRecvFlags` 定义在 `rpc_message.h`：`enum class RpcRecvFlags : int { NONE = 0, DONTWAIT = ZMQ_DONTWAIT };`，`zmq_message.h` 中 `#define ZmqRecvFlags RpcRecvFlags`。在 `zmq_socket_ref.cpp::RecvMsg` 中 flags 参数可直接比较。

**网络类 errno 判定函数**：

```cpp
inline bool IsNetworkErrno(int e) {
    return e == ECONNREFUSED || e == ECONNRESET || e == ECONNABORTED ||
           e == EHOSTUNREACH || e == ENETUNREACH || e == ENETDOWN ||
           e == ETIMEDOUT || e == EPIPE || e == ENOTCONN;
}
```

### Layer 2 — 连接异常事件（低频，非关键路径）

> 采集位置：`zmq_stub_conn.cpp`（gateway 重建）、`zmq_monitor.cpp`（socket 事件）  
> 这些代码路径本身是异常/生命周期事件，不在 RPC 请求处理关键路径上

| Metric Name | Type | ID | 触发条件 | 定界作用 |
|-------------|------|----|---------|---------|
| `zmq.gw_recreate` | Counter | 106 | `InitFrontend` 成功创建新 gateway 后 | 连接不稳定信号 |
| `zmq.evt.disconn` | Counter | 107 | `OnEventDisconnected` 回调 | ZMQ socket 断连 |
| `zmq.evt.hs_fail` | Counter | 108 | handshake 失败回调（3 种均计入） | TLS/认证问题 |

> 裁剪掉的指标及理由：
> - ~~`zmq.liveness`~~：heartbeat loop 每次迭代都要 `Gauge.Set()`，属于正常路径高频操作
> - ~~`zmq.hb_fail`~~：需要改 `(void)SendHeartBeats()` 调用方式，改动侵入性较大
> - ~~`zmq.evt.connected`~~：连接成功是正常事件，不需要指标追踪

---

## 六、定界场景矩阵

| 场景 | Layer 1 变化 | Layer 2 变化 | 已有日志信号 | 定界结论 |
|------|-------------|-------------|-------------|---------|
| **网卡故障** | `net_error` ↑↑, `send.fail` ↑, `recv.fail` ↑, `last_errno`=101/113 | `evt.disconn` ↑, `gw_recreate` ↑ | — | 底层网络故障 |
| **对端进程 hang** | blocking `recv.eagain` ↑（超时返回）, `net_error` 不变 | `gw_recreate` ↑ | — | 对端处理慢/hang |
| **ZMQ HWM 限流** | `send.eagain` ↑↑, `net_error` 不变 | 正常 | CheckHWMRatio WARNING | 背压/HWM 限流 |
| **本端业务慢** | 全部 `+0` | 全部 `+0` | CheckHWMRatio WARNING | worker 线程不足 |
| **连接被拒/重置** | `net_error` ↑, `last_errno`=111/104 | `evt.disconn` ↑, `gw_recreate` ↑ | — | 对端拒绝/重置连接 |
| **TLS/证书错误** | 可能 `recv.fail` ↑ | `evt.hs_fail` ↑↑ | Gateway handshake failed | 认证/证书配置错误 |

---

## 七、实施计划

### Phase 1 — Socket 层指标 + 日志标签（核心定界能力）

**范围**：Layer 1 全部 6 个指标 + `zmq_socket.cpp` 超时日志

#### 7.1 新建 `zmq_metrics_def.h`

```cpp
// src/datasystem/common/rpc/zmq/zmq_metrics_def.h
#pragma once
#include <cerrno>
#include "datasystem/common/metrics/metrics.h"

namespace datasystem {
enum ZmqMetricId : uint16_t {
    ZMQ_M_SEND_FAIL     = 100,
    ZMQ_M_RECV_FAIL     = 101,
    ZMQ_M_SEND_EAGAIN   = 102,
    ZMQ_M_RECV_EAGAIN   = 103,
    ZMQ_M_NET_ERROR     = 104,
    ZMQ_M_LAST_ERRNO    = 105,
    ZMQ_M_GW_RECREATE   = 106,
    ZMQ_M_EVT_DISCONN   = 107,
    ZMQ_M_EVT_HS_FAIL   = 108,
};

inline bool IsNetworkErrno(int e) {
    return e == ECONNREFUSED || e == ECONNRESET || e == ECONNABORTED ||
           e == EHOSTUNREACH || e == ENETUNREACH || e == ENETDOWN ||
           e == ETIMEDOUT || e == EPIPE || e == ENOTCONN;
}

inline const metrics::MetricDesc ZMQ_METRIC_DESCS[] = {
    {ZMQ_M_SEND_FAIL,   "zmq.send.fail",     metrics::MetricType::COUNTER, "count"},
    {ZMQ_M_RECV_FAIL,   "zmq.recv.fail",     metrics::MetricType::COUNTER, "count"},
    {ZMQ_M_SEND_EAGAIN, "zmq.send.eagain",   metrics::MetricType::COUNTER, "count"},
    {ZMQ_M_RECV_EAGAIN, "zmq.recv.eagain",   metrics::MetricType::COUNTER, "count"},
    {ZMQ_M_NET_ERROR,   "zmq.net_error",     metrics::MetricType::COUNTER, "count"},
    {ZMQ_M_LAST_ERRNO,  "zmq.last_errno",    metrics::MetricType::GAUGE,   ""},
    {ZMQ_M_GW_RECREATE, "zmq.gw_recreate",   metrics::MetricType::COUNTER, "count"},
    {ZMQ_M_EVT_DISCONN, "zmq.evt.disconn",   metrics::MetricType::COUNTER, "count"},
    {ZMQ_M_EVT_HS_FAIL, "zmq.evt.hs_fail",   metrics::MetricType::COUNTER, "count"},
};
constexpr size_t ZMQ_METRIC_DESCS_COUNT = sizeof(ZMQ_METRIC_DESCS) / sizeof(ZMQ_METRIC_DESCS[0]);
}  // namespace datasystem
```

#### 7.2 改动 `zmq_socket_ref.cpp`

**改动前** `RecvMsg`：
```cpp
Status ZmqSocketRef::RecvMsg(ZmqMessage &msg, ZmqRecvFlags flags)
{
    CHECK_FAIL_RETURN_STATUS_PRINT_ERROR(sock_ != nullptr, K_INVALID, "Null reference pointer");
    int rc = zmq_msg_recv(msg.GetHandle(), sock_, static_cast<int>(flags));
    if (rc == -1) {
        return ZmqErrnoToStatus(errno, "ZMQ recv msg unsuccessful", K_RPC_UNAVAILABLE);
    }
    // ... size check ...
}
```

**改动后** `RecvMsg`：
```cpp
Status ZmqSocketRef::RecvMsg(ZmqMessage &msg, ZmqRecvFlags flags)
{
    CHECK_FAIL_RETURN_STATUS_PRINT_ERROR(sock_ != nullptr, K_INVALID, "Null reference pointer");
    int rc = zmq_msg_recv(msg.GetHandle(), sock_, static_cast<int>(flags));
    if (rc == -1) {
        int e = errno;
        if (e == EAGAIN) {
            if (flags == ZmqRecvFlags::NONE) {
                metrics::GetCounter(ZMQ_M_RECV_EAGAIN).Inc();
            }
        } else if (e != EINTR) {
            metrics::GetCounter(ZMQ_M_RECV_FAIL).Inc();
            metrics::GetGauge(ZMQ_M_LAST_ERRNO).Set(e);
            if (IsNetworkErrno(e)) {
                metrics::GetCounter(ZMQ_M_NET_ERROR).Inc();
            }
            LOG(WARNING) << FormatString("[ZMQ_RECV_FAIL] errno=%d(%s)", e, zmq_strerror(e));
        }
        return ZmqErrnoToStatus(e, "ZMQ recv msg unsuccessful", K_RPC_UNAVAILABLE);
    }
    // ... size check (unchanged) ...
}
```

> **注意**：`errno` 必须在 `zmq_msg_recv` 返回后立即保存到局部变量 `e`，避免被后续 `metrics::GetCounter()` 内部的 `Valid()` 等操作覆盖。原代码直接传 `errno` 给 `ZmqErrnoToStatus`，改后传 `e`。

**改动后** `SendMsg`：
```cpp
Status ZmqSocketRef::SendMsg(ZmqMessage &msg, ZmqSendFlags flags)
{
    CHECK_FAIL_RETURN_STATUS_PRINT_ERROR(sock_ != nullptr, K_INVALID, "Null reference pointer");
    const auto msgSize = msg.Size();
    int rc = zmq_msg_send(msg.GetHandle(), sock_, static_cast<int>(flags));
    if (rc == -1) {
        int e = errno;
        if (e == EAGAIN) {
            metrics::GetCounter(ZMQ_M_SEND_EAGAIN).Inc();
        } else if (e != EINTR) {
            metrics::GetCounter(ZMQ_M_SEND_FAIL).Inc();
            metrics::GetGauge(ZMQ_M_LAST_ERRNO).Set(e);
            if (IsNetworkErrno(e)) {
                metrics::GetCounter(ZMQ_M_NET_ERROR).Inc();
            }
            LOG(WARNING) << FormatString("[ZMQ_SEND_FAIL] errno=%d(%s)", e, zmq_strerror(e));
        }
        return ZmqErrnoToStatus(e, "ZMQ send msg unsuccessful", K_RPC_CANCELLED);
    }
    // ... size check (unchanged) ...
}
```

#### 7.3 改动 `zmq_socket.cpp`

`ZmqRecvMsg` blocking 超时分支添加日志标签（无新 metric，只是 LOG 改善）：

```cpp
if (status.GetCode() == K_TRY_AGAIN && blocking) {
    int64_t waitSec = std::chrono::duration_cast<std::chrono::seconds>(endTick - startTick).count();
    LOG(WARNING) << FormatString("[ZMQ_RECV_TIMEOUT] Blocking recv timed out after %d seconds", waitSec);
    RETURN_STATUS(K_RPC_UNAVAILABLE,
                  FormatString("[ZMQ_RECV_TIMEOUT] Waited %d seconds, no response from server", waitSec));
}
```

> 同时修正原代码变量名 `ms`（实际是 seconds）→ `waitSec`。

| 改动文件 | 改动内容 | 热路径开销 |
|---------|---------|-----------|
| 新建 `zmq_metrics_def.h` | ID 枚举 + MetricDesc 数组 + `IsNetworkErrno` | 纯声明，无运行时开销 |
| `zmq_socket_ref.cpp` | `rc == -1` 分支内增加 metric + LOG | **零**（成功路径无改动） |
| `zmq_socket.cpp` | 超时分支 LOG 标签 + 变量名修正 | **零**（仅超时路径） |

### Phase 2 — 连接异常事件指标（低频路径）

#### 7.4 改动 `zmq_stub_conn.cpp`

在两处 `LOG(INFO) << ... "New gateway created"` 之后各加一行：

```cpp
LOG(INFO) << FormatString("New gateway created %s", GetGatewayId());
metrics::GetCounter(ZMQ_M_GW_RECREATE).Inc();
```

具体位置：
- **第 326 行**：`WorkerEntry()` 初始创建后
- **第 381 行**：`liveness_ == 0` 重建成功后

#### 7.5 改动 `zmq_monitor.cpp`

```cpp
void ZmqMonitor::OnEventDisconnected(const Event &t, const std::string &addr, const std::string &gatewayId)
{
    LOG(WARNING) << FormatString("Gateway %s socket disconnected. fd %d. %s", gatewayId, t.value_, addr);
    metrics::GetCounter(ZMQ_M_EVT_DISCONN).Inc();
}
```

三个 handshake 失败回调统一加 `EVT_HS_FAIL.Inc()`：

```cpp
void ZmqMonitor::OnEventHandshakeFailedNoDetail(...)  { /* ... LOG ... */ metrics::GetCounter(ZMQ_M_EVT_HS_FAIL).Inc(); }
void ZmqMonitor::OnEventHandshakeFailedProtocol(...)   { /* ... LOG ... */ metrics::GetCounter(ZMQ_M_EVT_HS_FAIL).Inc(); }
void ZmqMonitor::OnEventHandshakeFailedAuth(...)       { /* ... LOG ... */ metrics::GetCounter(ZMQ_M_EVT_HS_FAIL).Inc(); }
```

| 改动文件 | 改动内容 | 热路径开销 |
|---------|---------|-----------|
| `zmq_stub_conn.cpp` | 2 处 gateway 创建后 `GW_RECREATE.Inc()` | **零**（分钟级低频事件） |
| `zmq_monitor.cpp` | disconnect + 3 种 handshake fail 回调 `Inc()` | **零**（monitor 独立线程） |

### Init 注册时机

```cpp
#include "datasystem/common/rpc/zmq/zmq_metrics_def.h"

// 在进程启动路径（如 worker main / client Init）中
metrics::Init(ZMQ_METRIC_DESCS, ZMQ_METRIC_DESCS_COUNT);
metrics::Start();
```

> **关键约束**：当前 `Init()` 是全量替换式（内部先 `Stop()` + `ClearAll()`）。如果业务模块也有指标，需在同一注册点把所有 `MetricDesc` 合并后调用一次 `Init()`。建议后续推动支持增量注册。

---

## 八、日志标签统一

与 URMA 计划对齐，ZMQ 层日志使用统一前缀标签，便于 grep：

| 标签 | 含义 | 出现位置 |
|------|------|---------|
| `[ZMQ_SEND_FAIL]` | zmq_msg_send 返回 -1（硬失败） | `zmq_socket_ref.cpp` |
| `[ZMQ_RECV_FAIL]` | zmq_msg_recv 返回 -1（硬失败） | `zmq_socket_ref.cpp` |
| `[ZMQ_RECV_TIMEOUT]` | blocking recv 超时（EAGAIN → K_RPC_UNAVAILABLE） | `zmq_socket.cpp` |

---

## 九、验证方案

### 9.1 UT 验证 — ZMQ Metrics 定义注册测试

**测试文件**: `tests/ut/common/rpc/zmq_metrics_test.cpp`（新建）

> 复用 PR #584 的 metrics 测试模式：`ResetForTest()` → `Init()` → 操作 → `DumpSummaryForTest()` → 验证字符串。
> **不依赖真实 ZMQ socket**，只验证 metrics 定义注册和打点机制。

```cpp
#include "datasystem/common/metrics/metrics.h"
#include "datasystem/common/rpc/zmq/zmq_metrics_def.h"
#include "gtest/gtest.h"
#include "ut/common.h"

namespace datasystem {
namespace ut {
namespace {

class ZmqMetricsTest : public CommonTest {
public:
    void SetUp() override
    {
        CommonTest::SetUp();
        metrics::ResetForTest();
        DS_ASSERT_OK(metrics::Init(ZMQ_METRIC_DESCS, ZMQ_METRIC_DESCS_COUNT));
    }
    void TearDown() override
    {
        metrics::ResetForTest();
    }
};

// Case 1: 验证所有 9 个 ZMQ metric 注册成功
TEST_F(ZmqMetricsTest, all_zmq_metrics_registered)
{
    auto summary = metrics::DumpSummaryForTest();
    EXPECT_NE(summary.find("zmq.send.fail=0"),   std::string::npos);
    EXPECT_NE(summary.find("zmq.recv.fail=0"),   std::string::npos);
    EXPECT_NE(summary.find("zmq.send.eagain=0"), std::string::npos);
    EXPECT_NE(summary.find("zmq.recv.eagain=0"), std::string::npos);
    EXPECT_NE(summary.find("zmq.net_error=0"),   std::string::npos);
    EXPECT_NE(summary.find("zmq.last_errno=0"),  std::string::npos);
    EXPECT_NE(summary.find("zmq.gw_recreate=0"), std::string::npos);
    EXPECT_NE(summary.find("zmq.evt.disconn=0"), std::string::npos);
    EXPECT_NE(summary.find("zmq.evt.hs_fail=0"), std::string::npos);
}

// Case 2: Counter Inc 后 summary 值正确
TEST_F(ZmqMetricsTest, send_fail_counter_inc)
{
    metrics::GetCounter(ZMQ_M_SEND_FAIL).Inc();
    metrics::GetCounter(ZMQ_M_SEND_FAIL).Inc();
    auto summary = metrics::DumpSummaryForTest();
    EXPECT_NE(summary.find("zmq.send.fail=2"),  std::string::npos);
    EXPECT_NE(summary.find("zmq.send.fail=+2"), std::string::npos);
}

// Case 3: recv.fail + net_error 联动
TEST_F(ZmqMetricsTest, recv_fail_with_net_error)
{
    metrics::GetCounter(ZMQ_M_RECV_FAIL).Inc();
    metrics::GetCounter(ZMQ_M_NET_ERROR).Inc();
    metrics::GetGauge(ZMQ_M_LAST_ERRNO).Set(ECONNRESET);
    auto summary = metrics::DumpSummaryForTest();
    EXPECT_NE(summary.find("zmq.recv.fail=1"),  std::string::npos);
    EXPECT_NE(summary.find("zmq.net_error=1"),  std::string::npos);
    // ECONNRESET = 104 on Linux
    EXPECT_NE(summary.find("zmq.last_errno=" + std::to_string(ECONNRESET)), std::string::npos);
}

// Case 4: Gauge Set 覆盖
TEST_F(ZmqMetricsTest, last_errno_gauge_override)
{
    metrics::GetGauge(ZMQ_M_LAST_ERRNO).Set(ECONNREFUSED);
    metrics::GetGauge(ZMQ_M_LAST_ERRNO).Set(EHOSTUNREACH);
    auto summary = metrics::DumpSummaryForTest();
    // 最后一次 Set 生效
    EXPECT_NE(summary.find("zmq.last_errno=" + std::to_string(EHOSTUNREACH)), std::string::npos);
}

// Case 5: Delta 正确——两次 dump 之间的增量
TEST_F(ZmqMetricsTest, delta_between_dumps)
{
    metrics::GetCounter(ZMQ_M_RECV_FAIL).Inc(5);
    (void)metrics::DumpSummaryForTest();  // snapshot 1
    metrics::GetCounter(ZMQ_M_RECV_FAIL).Inc(3);
    auto summary = metrics::DumpSummaryForTest();  // snapshot 2
    EXPECT_NE(summary.find("zmq.recv.fail=8"),  std::string::npos);  // total
    EXPECT_NE(summary.find("zmq.recv.fail=+3"), std::string::npos);  // delta
}

// Case 6: 零 delta 场景——无新错误时所有指标 delta 为 +0
TEST_F(ZmqMetricsTest, zero_delta_when_idle)
{
    metrics::GetCounter(ZMQ_M_SEND_FAIL).Inc(1);
    (void)metrics::DumpSummaryForTest();
    auto summary = metrics::DumpSummaryForTest();
    EXPECT_NE(summary.find("zmq.send.fail=+0"), std::string::npos);
    EXPECT_NE(summary.find("zmq.recv.fail=+0"), std::string::npos);
    EXPECT_NE(summary.find("zmq.net_error=+0"), std::string::npos);
}

// Case 7: IsNetworkErrno 判定正确
TEST_F(ZmqMetricsTest, is_network_errno)
{
    EXPECT_TRUE(IsNetworkErrno(ECONNREFUSED));
    EXPECT_TRUE(IsNetworkErrno(ECONNRESET));
    EXPECT_TRUE(IsNetworkErrno(EHOSTUNREACH));
    EXPECT_TRUE(IsNetworkErrno(ENETUNREACH));
    EXPECT_TRUE(IsNetworkErrno(ENETDOWN));
    EXPECT_TRUE(IsNetworkErrno(ETIMEDOUT));
    EXPECT_TRUE(IsNetworkErrno(EPIPE));
    EXPECT_TRUE(IsNetworkErrno(ENOTCONN));
    EXPECT_TRUE(IsNetworkErrno(ECONNABORTED));
    // 非网络类
    EXPECT_FALSE(IsNetworkErrno(EAGAIN));
    EXPECT_FALSE(IsNetworkErrno(EINTR));
    EXPECT_FALSE(IsNetworkErrno(ENOMEM));
    EXPECT_FALSE(IsNetworkErrno(ENOENT));
}

// Case 8: Layer 2 连接异常指标
TEST_F(ZmqMetricsTest, layer2_connection_metrics)
{
    metrics::GetCounter(ZMQ_M_GW_RECREATE).Inc();
    metrics::GetCounter(ZMQ_M_EVT_DISCONN).Inc(3);
    metrics::GetCounter(ZMQ_M_EVT_HS_FAIL).Inc(2);
    auto summary = metrics::DumpSummaryForTest();
    EXPECT_NE(summary.find("zmq.gw_recreate=1"), std::string::npos);
    EXPECT_NE(summary.find("zmq.evt.disconn=3"), std::string::npos);
    EXPECT_NE(summary.find("zmq.evt.hs_fail=2"), std::string::npos);
}

// Case 9: 并发安全——多线程同时 Inc 各 counter
TEST_F(ZmqMetricsTest, concurrent_counter_inc)
{
    const int threads = 32;
    const int loops = 500;
    std::vector<std::thread> workers;
    for (int i = 0; i < threads; ++i) {
        workers.emplace_back([&] {
            for (int j = 0; j < loops; ++j) {
                metrics::GetCounter(ZMQ_M_SEND_FAIL).Inc();
                metrics::GetCounter(ZMQ_M_RECV_FAIL).Inc();
                metrics::GetCounter(ZMQ_M_NET_ERROR).Inc();
            }
        });
    }
    for (auto &w : workers) {
        w.join();
    }
    auto summary = metrics::DumpSummaryForTest();
    auto expected = std::to_string(threads * loops);
    EXPECT_NE(summary.find("zmq.send.fail=" + expected), std::string::npos);
    EXPECT_NE(summary.find("zmq.recv.fail=" + expected), std::string::npos);
    EXPECT_NE(summary.find("zmq.net_error=" + expected), std::string::npos);
}

// Case 10: 未 Init 时操作不 crash（降级 no-op）
TEST_F(ZmqMetricsTest, noop_before_init)
{
    metrics::ResetForTest();  // 清除 SetUp 中的 Init
    // 以下操作不应 crash
    metrics::GetCounter(ZMQ_M_SEND_FAIL).Inc();
    metrics::GetGauge(ZMQ_M_LAST_ERRNO).Set(42);
    auto summary = metrics::DumpSummaryForTest();
    EXPECT_TRUE(summary.empty());  // 未 init，summary 为空
}

// Case 11: 定界场景验证 —— 模拟网卡故障指标模式
TEST_F(ZmqMetricsTest, scenario_network_card_failure)
{
    // 模拟：recv.fail + send.fail + net_error 同时上涨，last_errno = EHOSTUNREACH
    metrics::GetCounter(ZMQ_M_RECV_FAIL).Inc(5);
    metrics::GetCounter(ZMQ_M_SEND_FAIL).Inc(3);
    metrics::GetCounter(ZMQ_M_NET_ERROR).Inc(8);
    metrics::GetGauge(ZMQ_M_LAST_ERRNO).Set(EHOSTUNREACH);
    metrics::GetCounter(ZMQ_M_EVT_DISCONN).Inc(2);
    metrics::GetCounter(ZMQ_M_GW_RECREATE).Inc(1);
    auto summary = metrics::DumpSummaryForTest();
    // 验证定界特征：net_error > 0, fail > 0, last_errno = EHOSTUNREACH
    EXPECT_NE(summary.find("zmq.net_error=8"), std::string::npos);
    EXPECT_NE(summary.find("zmq.last_errno=" + std::to_string(EHOSTUNREACH)), std::string::npos);
    EXPECT_NE(summary.find("zmq.evt.disconn=2"), std::string::npos);
}

// Case 12: 定界场景验证 —— 模拟对端 hang（只有 recv timeout）
TEST_F(ZmqMetricsTest, scenario_peer_hang)
{
    // 只有 recv.eagain 上涨，net_error 不变
    metrics::GetCounter(ZMQ_M_RECV_EAGAIN).Inc(10);
    auto summary = metrics::DumpSummaryForTest();
    EXPECT_NE(summary.find("zmq.recv.eagain=10"), std::string::npos);
    EXPECT_NE(summary.find("zmq.net_error=0"), std::string::npos);
    EXPECT_NE(summary.find("zmq.send.fail=0"), std::string::npos);
}

// Case 13: 定界场景验证 —— 模拟 HWM 背压
TEST_F(ZmqMetricsTest, scenario_hwm_backpressure)
{
    metrics::GetCounter(ZMQ_M_SEND_EAGAIN).Inc(100);
    auto summary = metrics::DumpSummaryForTest();
    EXPECT_NE(summary.find("zmq.send.eagain=100"), std::string::npos);
    EXPECT_NE(summary.find("zmq.net_error=0"), std::string::npos);
}

}  // namespace
}  // namespace ut
}  // namespace datasystem
```

### 9.2 ST 验证方案

ST 场景需要真实 ZMQ 连接环境，基于现有 ST 框架（如 `zmq_fd_leak_repro_test.cpp` 的模式）。

#### ST Case 1: 正常 RPC 调用后指标全零

```
前置：启动 server + client，完成 N 次正常 RPC 调用
断言：metrics summary 中：
  - zmq.send.fail=0 (delta +0)
  - zmq.recv.fail=0 (delta +0)
  - zmq.net_error=0 (delta +0)
  - zmq.send.eagain=0 或极小值
验证：成功路径确实无额外打点开销
```

#### ST Case 2: 模拟连接断开后指标上涨

```
前置：启动 server + client，正常通信后 kill server 端
等待：client 侧触发 recv 超时 / gateway 重建
断言：
  - zmq.recv.fail > 0 或 zmq.recv.eagain > 0
  - zmq.gw_recreate > 0
  - zmq.evt.disconn > 0
验证：连接管理层指标响应连接断开
```

#### ST Case 3: 无效地址连接后的 handshake 失败

```
前置：client 连接到不存在的地址
断言：
  - zmq.evt.hs_fail > 0（如果使用 CURVE 认证）
  - zmq.gw_recreate > 0
验证：TLS/认证问题可定界
```

#### ST Case 4: Metrics Summary 格式验证

```
前置：触发若干失败后等待一个 metrics 周期
断言：从 worker.log 中 grep 得到：
  - "Metrics Summary, version=v0" 头存在
  - "zmq.send.fail=" 行存在
  - "zmq.net_error=" 行存在
  - "[ZMQ_SEND_FAIL]" 或 "[ZMQ_RECV_FAIL]" 日志标签可检索
验证：运维可用 grep 快速定位
```

### 9.3 自动化验证脚本

```bash
# 验证 metrics summary 输出
grep "zmq.send.fail=" /path/to/worker.log && echo "PASS: send.fail metric found"
grep "zmq.net_error=" /path/to/worker.log && echo "PASS: net_error metric found"
grep "zmq.last_errno=" /path/to/worker.log && echo "PASS: last_errno metric found"

# 验证日志标签
grep "\[ZMQ_SEND_FAIL\]" /path/to/worker.log && echo "PASS: SEND_FAIL tag found"
grep "\[ZMQ_RECV_FAIL\]" /path/to/worker.log && echo "PASS: RECV_FAIL tag found"
grep "\[ZMQ_RECV_TIMEOUT\]" /path/to/worker.log && echo "PASS: RECV_TIMEOUT tag found"

# 验证正常场景下 delta 全零
grep "zmq.send.fail=+0" /path/to/worker.log && echo "PASS: no send failures in period"
```

---

## 十、Build 改动

### CMakeLists.txt

`zmq_metrics_def.h` 是纯 header，只需要在 `zmq_socket_ref.cpp`、`zmq_stub_conn.cpp`、`zmq_monitor.cpp` 中 `#include`，并确保这些编译目标链接 `common_metrics`。

```cmake
# src/datasystem/common/rpc/zmq/CMakeLists.txt 中
# 已有 target 链接列表中添加 common_metrics
target_link_libraries(common_rpc_zmq PRIVATE ... common_metrics)
```

### BUILD.bazel

```python
# src/datasystem/common/rpc/zmq/BUILD.bazel 中
deps = [
    # 已有依赖...
    "//src/datasystem/common/metrics:common_metrics",
]
```

### UT 编译

```cmake
# tests/ut/common/rpc/ 目录（可能需新建）
# zmq_metrics_test.cpp 链接 common_metrics + gtest
```

UT 的 top-level `CMakeLists.txt` 已通过 `file(GLOB_RECURSE DS_TEST_UT_SRCS ...)` 自动发现新 `.cpp`，且 `DS_UT_DEPEND_LIBS` 已包含 `common_metrics`。

---

## 十一、风险评估

| 风险点 | 影响 | 缓解措施 |
|--------|------|---------|
| `metrics::Init()` 全量替换 | 多模块指标需统一注册 | 约定统一注册入口；或后续推动增量注册 |
| Metric ID 冲突 | 不同模块用了相同 ID | ZMQ 使用 100-109 段；业务用 0-99 段；URMA 用 200-299 段 |
| metrics writer 线程开销 | 与 `log_monitor` 共用间隔 | 极低：只读 9 个 atomic 值拼字符串 |
| **成功路径开销** | **零** | 所有 `Counter.Inc()` 仅在 `rc == -1` 分支内 |
| `errno` 被覆盖 | metrics 调用改变 `errno` | **已处理**：进入 `-1` 分支后立即 `int e = errno;` 保存 |
| 未 Init 时打点 | 降级为 no-op | `Valid()` 内部检查 `g_inited`，安全返回 |

---

## 十二、与 URMA/TCP 计划的关系

| 维度 | URMA/TCP 计划（已有） | ZMQ Metrics 计划（本文） |
|------|---------------------|----------------------|
| 核心问题 | URMA 错误码语义 + 故障日志缺失 | ZMQ send/recv 无指标，定界链断裂 |
| 改动层 | `urma_manager.cpp`, `rdma_util.h` | `zmq_socket_ref.cpp`, `zmq_stub_conn.cpp`, `zmq_monitor.cpp` |
| 是否改错误码 | 是（新增 `K_URMA_WAIT_TIMEOUT`） | **否** |
| 指标框架 | 不涉及 | 复用 `datasystem::metrics` |
| 日志标签 | `[URMA_*]` 系列 | `[ZMQ_*]` 系列 |
| 可独立提交 | 是 | 是（Phase 1 日志部分甚至不依赖 metrics） |

两个计划互补，合并后覆盖"URMA 数据面 + TCP/RPC 控制面"的完整定界需求。
