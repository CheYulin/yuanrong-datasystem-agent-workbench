# ZMQ 重连机制与 IF Down 场景深度分析

## 1. ZMQ 默认重连配置

| 配置项 | 默认值 | 说明 | 源码位置 |
|--------|--------|------|----------|
| `ZMQ_RECONNECT_IVL` | **100ms** | 首次重连间隔 | `options.cpp:188` |
| `ZMQ_RECONNECT_IVL_MAX` | **0** (禁用指数退避上界) | =0时: interval = prev + jitter[0, reconnect_ivl) | `options.hpp:103` |
| `ZMQ_TCP_KEEPALIVE` | **-1** (不改动系统默认) | 不主动设置 SO_KEEPALIVE | `options.cpp:201` |
| `ZMQ_TCP_KEEPALIVE_IDLE` | **-1** | 不设置 | `options.cpp:202` |
| `ZMQ_TCP_KEEPALIVE_CNT` | **-1** | 不设置 | `options.cpp:203` |
| `ZMQ_TCP_KEEPALIVE_INTVL` | **-1** | 不设置 | `options.cpp:204` |
| `ZMQ_HEARTBEAT_IVL` | **0** (禁用) | ZMTP PING/PONG 心跳间隔 | `options.cpp:215` |
| `ZMQ_HEARTBEAT_TIMEOUT` | **-1** (跟随 IVL) | PONG 超时 | `options.cpp:216` |
| `ZMQ_HEARTBEAT_TTL` | **0** | 通知对端 TTL | `options.cpp:214` |
| `ZMQ_HANDSHAKE_IVL` | **30000ms** | ZMTP 握手超时 | `options.cpp:212` |
| `ZMQ_SNDHWM / ZMQ_RCVHWM` | **1000** | 消息高水位 | `options.cpp:167-172` |
| `ZMQ_LINGER` | **-1** (无限等待) | close 时等待发送 | `options.cpp:176` |
| `ZMQ_CONNECT_TIMEOUT` | **0** (使用 OS 默认) | TCP connect 超时 | `options.cpp:185` |

### 数据系统 RPC 框架实际配置

| 配置项 | 实际设置值 | 位置 |
|--------|-----------|------|
| `ZMQ_LINGER` | **0** (立即丢弃) | `zmq_context.cpp:123` |
| `ZMQ_SNDTIMEO` | **60000ms** (Server) / **3000ms** (Client DEALER) | `rpc_constants.h:27,46` |
| `ZMQ_RCVTIMEO` | **60000ms** / **3000ms** | 同上 |
| `ZMQ_SNDHWM` | **0** (无限) | Server & Client 都设为 0 |
| `ZMQ_RCVHWM` | **0** (无限) | 同上 |
| `ZMQ_IMMEDIATE` | **1** (true) | `zmq_context.cpp:126` |
| `ZMQ_PROBE_ROUTER` | **1** (true) | Client DEALER 特有 |
| `ZMQ_BACKLOG` | **1024** | Server ROUTER |
| `ZMQ_RECONNECT_IVL` | **未设置 → 100ms** | 使用 libzmq 默认 |
| `ZMQ_RECONNECT_IVL_MAX` | **未设置 → 0** | 使用 libzmq 默认 |
| `ZMQ_TCP_KEEPALIVE` | **未设置 → -1** | 不启用 TCP keepalive |
| `ZMQ_HEARTBEAT_IVL` | **未设置 → 0** | 不启用 ZMTP 心跳 |

> **关键发现**: 数据系统 RPC 框架 **未配置** ZMQ 的 TCP keepalive、ZMTP 心跳、重连间隔。完全依赖 libzmq 默认值。

---

## 2. ZMQ 怎么知道 TCP 链路异常？故障检测时间

### 检测机制层次

```
┌─────────────────────────────────────────────────────┐
│ 应用层 ZMQ 心跳 (ZMTP PING/PONG)  ← 当前未启用!   │
├─────────────────────────────────────────────────────┤
│ TCP Keepalive (SO_KEEPALIVE)       ← 当前未启用!   │
├─────────────────────────────────────────────────────┤
│ TCP Read/Write 错误               ← 唯一的检测手段  │
│   - recv() 返回 0 (FIN)                            │
│   - recv()/send() 返回 ECONNRESET/EPIPE/ETIMEDOUT  │
└─────────────────────────────────────────────────────┘
```

### 检测路径详解

#### 路径 A: Read 侧 (主要检测)
```
recv() 返回 0 → tcp_read() → stream_engine_base::in_event_internal()
  → error(connection_error) → session_base::engine_error()
  → clean_pipes() + reconnect()
```

#### 路径 B: Write 侧 (不够可靠)
```
send() 返回 EPIPE/ECONNRESET → tcp_write() → stream_engine_base::out_event()
  → 仅 reset_pollout()，不拆连接！
  → 必须等 read 侧发现错误才会真正拆连接
```

#### 路径 C: TCP Keepalive (当前未启用)
- 默认 `tcp_keepalive = -1`，ZMQ 不设置 `SO_KEEPALIVE`
- Linux 默认 keepalive: idle=7200s, interval=75s, count=9 → 检测需要 **~7200 + 75*9 = 7875s**
- 即使 OS 开了 keepalive，默认参数也无实际意义

#### 路径 D: ZMTP 心跳 (当前未启用)
- `heartbeat_interval = 0`，不发送 PING
- 如果启用: 每 `heartbeat_interval` ms 发送 PING，`heartbeat_timeout` ms 内没收到 PONG 则 `error(timeout_error)`

### IF Down 场景的故障检测时间

**IF Down = 网卡链路层断开，TCP 连接不会收到 FIN/RST**

| 检测手段 | 当前是否启用 | 检测时间 |
|----------|-------------|----------|
| 对端 FIN/RST | IF Down 时无法发送 | **不适用** |
| TCP write 失败 | 需要有数据发送 | 取决于 TCP 重传超时 (Linux 默认 ~15-20min) |
| TCP Keepalive | **未启用** | 不适用 |
| ZMTP Heartbeat | **未启用** | 不适用 |
| 应用层 RPC 超时 | 是 (60s/3s) | **60s (Server) / 3s (Client)** |
| 应用层 liveness | 是 | heartbeatInterval * K_LIVENESS (120次) |

> **结论**: 在当前配置下，IF Down 的检测几乎完全依赖 **应用层 RPC 超时** 和 **TCP 重传超时**。Client 端 3s 的 SNDTIMEO/RCVTIMEO 会首先触发。

---

## 3. ZMQ 应对 1s IF Down 的时间线

```
时间线 (ms)    事件
────────────────────────────────────────────────────────────────
  0            IF Down 发生，TCP 链路断开
               ZMQ 不知情，socket 状态看起来正常
               
  0~1000       ┌─ IF Down 期间 ──────────────────────────┐
               │                                          │
               │ [如果有数据正在传输]                      │
               │   TCP 层: 数据包丢失，开始重传            │
               │   TCP RTO: 初始~200ms, 指数退避          │
               │   第1次重传: ~200ms                       │
               │   第2次重传: ~400-600ms                   │
               │                                          │
               │ [如果没有数据传输]                        │
               │   ZMQ 完全不知情，pipe 缓冲区正常         │
               │   send() 照常成功（放入 ZMQ 内部队列）    │
               │                                          │
               └──────────────────────────────────────────┘
               
 1000          IF Up 恢复
               
 1000~1200     TCP 层: 重传的数据包被确认（或下一次重传成功）
               已缓冲的数据继续发送
               TCP 连接 **未断开**，sequence number 连续
               
 ~1200         数据传输恢复正常
               ZMQ 全程不知道发生了 IF Down
               **没有触发重连**

═══════════════════════════════════════════════════════════════
结论: 1s IF Down → TCP 未断 → ZMQ 不感知 → 不重连 → 数据无丢失
═══════════════════════════════════════════════════════════════
```

### 关键逻辑: TCP 重传超时 vs IF Down 持续时间

```
Linux TCP 重传行为:
  RTO 初始值: ~200ms (取决于 RTT)
  重传次数上限: net.ipv4.tcp_retries2 = 15 (默认)
  总超时: 约 13-30 分钟 (指数退避累计)

  重传时间表 (大致):
  ┌──────────────┬────────┐
  │ 重传次数      │ 累计时间 │
  ├──────────────┼────────┤
  │ 1            │ ~200ms  │
  │ 2            │ ~600ms  │
  │ 3            │ ~1.4s   │
  │ 4            │ ~3s     │
  │ 5            │ ~6.2s   │
  │ 6            │ ~12.6s  │
  │ ...          │ ...     │
  │ 15           │ ~15min  │
  └──────────────┴────────┘

IF Down < ~15min → TCP 连接存活 → ZMQ 无感 → 不重连
IF Down > TCP重传总超时 → TCP RST → ZMQ recv()=0 → 重连
```

---

## 4. 2.5s 周期注入 0.5s IF Down 的表现

### 场景参数
- 周期: 2.5s
- IF Down 持续: 0.5s
- IF Up 持续: 2.0s
- 实际观测故障时间: 0.7~0.8ms

```
时间线:
  0.0s ─── IF Down ───  0.5s ─── IF Up ─── 2.5s ─── IF Down ─── 3.0s ─── ...

每个 IF Down 窗口 (0.5s = 500ms):
┌──────────────────────────────────────────────────────────────┐
│ TCP RTO ~200ms，0.5s 内最多重传 1-2 次                       │
│ IF Up 恢复后，重传包到达 → TCP 连接保持                      │
│ 观测: 延迟抖动 ~200-500ms，吞吐量短暂降低                   │
│ ZMQ: 不触发重连，不丢消息                                    │
└──────────────────────────────────────────────────────────────┘
```

### 预期表现

| 指标 | 预期 |
|------|------|
| TCP 连接是否断开 | **否** (0.5s 远小于 TCP 重传超时) |
| ZMQ 是否触发重连 | **否** |
| 消息是否丢失 | **否** (TCP 可靠传输保证) |
| RPC 是否超时 | **可能** (如果恰好在 IF Down 期间等待 response, 累计延迟可能导致应用超时) |
| 延迟增加 | **是**, 每次 IF Down 增加 ~200-500ms 延迟 |
| 吞吐量影响 | 瞬时降低, TCP 拥塞窗口可能收缩 |

### 实际观测 0.7~0.8ms 故障时间的解释

0.7~0.8ms 非常短，远小于 TCP RTO (~200ms)。这个时间尺度上:
- TCP 的重传还没有触发
- 可能是 **链路层恢复** 的时间（网卡 link state 变化的 carrier detect 时间）
- 在这个尺度上，**可能丢失了 1-2 个 TCP segment**，但 TCP 的快速重传 (3 个 duplicate ACK) 或 RTO 会恢复
- 对 ZMQ 和上层应用 **几乎无感知**

---

## 5. ZMQ 重连后是否会丢包？

### 核心结论: **会丢消息！**

ZMQ 虽然看起来像可靠消息队列，但 **在 TCP 连接断开重连时，消息可能丢失**。

### 丢失场景分析

#### 场景 1: 已发出但未确认的消息 (TCP 缓冲区中)

```
Client                          Network                        Server
  │                               │                               │
  │── zmq_send(request) ──→      │                               │
  │   (进入 TCP send buffer)      │                               │
  │                               │←── IF Down ──                │
  │                               │   TCP buffer 中的数据         │
  │                               │   在重传超时后丢失            │
  │                               │                               │
  │   TCP连接最终断开              │                               │
  │   ZMQ engine_error             │                               │
  │   clean_pipes() + reconnect   │                               │
  │                               │                               │
  │   *** request 丢失 ***        │                               │
```

**TCP send buffer 里的数据**: TCP 连接断开时，尚在 send buffer 中未被对端 ACK 的数据 **全部丢失**。ZMQ 的 `clean_pipes()` 只回滚 **ZMQ pipe 里的未完成多帧消息**，已经交给 TCP 的数据不会被回滚。

#### 场景 2: Server 发出 response, Client TCP 断开

```
Server                          Network                        Client
  │                               │                               │
  │── zmq_send(response) ──→     │                               │
  │   response 进入 Server 的     │                               │
  │   ROUTER socket 的发送队列    │                               │
  │                               │←── Client TCP 断开 ──        │
  │                               │                               │
  │   ROUTER: 目标 peer 断开      │                               │
  │   该 peer 的消息被丢弃        │                               │
  │                               │                               │
  │   *** response 丢失 ***       │                               │
```

**ROUTER socket 行为**: 当 ROUTER 发送到一个已断开的 peer 时，`EHOSTUNREACH` → 消息直接丢弃（ROUTER 默认行为）。

#### 场景 3: ZMQ 内部 pipe 中的消息

```
重连后:
- Client 创建新的 DEALER socket
- 新 socket 有新的 routing-id (UUID)
- Server ROUTER 视其为新 peer
- 旧连接的 pipe 中缓冲的消息:
  - 入站消息: clean_pipes() 丢弃不完整的多帧
  - 出站消息: rollback() 回滚不完整多帧
  - 完整消息: 随 pipe terminate 被丢弃
```

### RPC Request/Response 丢失矩阵

```
┌──────────────────────┬──────────────────────────────────────────┐
│     IF Down 时刻      │              是否丢失                     │
├──────────────────────┼──────────────────────────────────────────┤
│                      │                                          │
│ Request 在 ZMQ pipe  │ 如果TCP不断: 不丢                        │
│ (zmq_send 成功)      │ 如果TCP断开: 丢失(pipe drain或TCP buf丢) │
│                      │                                          │
│ Request 在 TCP buf   │ 如果TCP不断: 不丢(TCP重传)               │
│                      │ 如果TCP断开: 丢失                        │
│                      │                                          │
│ Request 在网络中     │ 如果TCP不断: 不丢(TCP重传)               │
│ (未达Server)         │ 如果TCP断开: 丢失                        │
│                      │                                          │
│ Request 已达Server   │ 不丢(已被Server收到)                     │
│ Server正在处理       │ 但response可能丢失(见下)                  │
│                      │                                          │
│ Response 在Server    │ 如果TCP不断: 不丢                        │
│ TCP buf (回传中)     │ 如果TCP断开:                              │
│                      │   ROUTER丢弃该peer的pending msg          │
│                      │   *** Response 丢失 ***                   │
│                      │                                          │
│ Response 在网络中    │ 如果TCP不断: 不丢                        │
│                      │ 如果TCP断开: 丢失                        │
│                      │                                          │
│ Response 在Client    │ 如果TCP不断: 不丢                        │
│ TCP recv buf         │ 如果TCP断开: 丢失(socket被close)         │
│                      │                                          │
│ Response 已到ZMQ     │ 不丢(已在应用层)                         │
│ MsgQue               │                                          │
└──────────────────────┴──────────────────────────────────────────┘
```

### 判断是否触发TCP断开的关键

**0.5s IF Down**: TCP **不断开** → 不丢包
**长时间 IF Down (>15min)**: TCP 断开 → 丢包

**中间地带** 取决于:
1. TCP RTO 和重传次数 (`tcp_retries2=15`, 约15-30min)
2. 是否有 TCP keepalive 缩短检测时间 (当前未启用)
3. 是否有 ZMTP heartbeat 缩短检测时间 (当前未启用)
4. 应用层超时 (Client 3s, Server 60s) → 应用层可能先超时放弃

---

## 6. 数据系统 RPC 框架的保护机制

### 6.1 应用层 Liveness 检测

```cpp
// zmq_stub_conn.cpp:332-383 - WorkerEntry 主循环
// 每次 idle (K_NOT_FOUND) 时 liveness_--
// 收到 response 时 ResetLiveness()
// liveness_ == 0 时: 销毁 DEALER, 重建连接

K_LIVENESS = 120  // zmq_constants.h:38
heartbeatInterval = 动态计算 (基于 timeoutMs)
```

这是 **Client 侧的应用层心跳**，不依赖 ZMQ ZMTP 心跳。

### 6.2 GetStreamPeer 重试

```cpp
// zmq_stub_impl.cpp - GetStreamPeer
// 循环直到 retryTimeout (默认60s)
// K_RPC_UNAVAILABLE 或 K_TRY_AGAIN 时重试
// 每次重试 sleep 100ms
// 错误时重建 MsgQue
```

### 6.3 EAGAIN 处理

```cpp
// zmq_stub_conn.cpp:348-351
if (rc.GetCode() == K_TRY_AGAIN) {
    // force liveness to 0 → 触发 DEALER 重建
    liveness_ = 0;
}
```

### 6.4 ZMQ_IMMEDIATE = true

```cpp
// zmq_context.cpp:126
// ZMQ_IMMEDIATE=1: 只在连接真正建立后才路由消息到该 peer
// 避免向未完成连接的 peer 发送消息
```

这很关键！`ZMQ_IMMEDIATE=true` 意味着:
- 在 DEALER 连接未完成时，`zmq_send()` 会阻塞/返回 EAGAIN
- 不会把消息扔进一个还没建链的 pipe

---

## 7. 自动建链开销

### TCP 连接建立
- **3-way handshake**: 1.5 RTT (~0.1-1ms 局域网)
- ~50-100μs CPU (socket 创建 + 系统调用)

### ZMTP 握手
- **ZMTP greeting exchange**: 64 bytes 互发, 1 RTT
- **Mechanism handshake**:
  - NULL (无认证): 0-1 RTT, 几十字节
  - CURVE (当前使用): 4 RTT + 加密计算 (~1-5ms)
- **Properties exchange**: 1 RTT

### 总建链开销

| 阶段 | 延迟 (局域网) | 说明 |
|------|-------------|------|
| TCP 3-way handshake | ~0.2ms | |
| ZMTP greeting | ~0.1ms | 64字节交换 |
| NULL mechanism | ~0.1ms | 如果不用 CURVE |
| CURVE mechanism | ~2-5ms | 4次 box/unbox |
| Properties | ~0.1ms | |
| **总计 (NULL)** | **~0.5ms** | |
| **总计 (CURVE)** | **~3-6ms** | |

加上 ZMQ 的 reconnect_ivl (默认 100ms wait):

```
实际重连延迟 = reconnect_ivl (100ms) + TCP建链 + ZMTP握手
             ≈ 100ms + 0.5ms (NULL) 或 100ms + 5ms (CURVE)
             ≈ 100-105ms
```

---

## 8. 综合建议

### 8.1 当前风险

1. **无主动故障检测**: TCP keepalive 和 ZMTP heartbeat 都未启用
2. **长 IF Down 无法快速感知**: 依赖 TCP 重传超时 (~15min) 或应用层超时 (3-60s)
3. **ROUTER → 断开 peer 的 response 丢失**: 无重试机制

### 8.2 推荐配置

```cpp
// 在 ZmqSocket 创建后或 zmq_context.cpp 中添加:

// 启用 ZMTP 心跳 (推荐)
sock.setsockopt(ZMQ_HEARTBEAT_IVL, 500);    // 每500ms发PING
sock.setsockopt(ZMQ_HEARTBEAT_TIMEOUT, 1500); // 1.5s无PONG判死
sock.setsockopt(ZMQ_HEARTBEAT_TTL, 1500);    // 通知对端1.5s TTL

// 启用 TCP Keepalive (辅助)
sock.setsockopt(ZMQ_TCP_KEEPALIVE, 1);
sock.setsockopt(ZMQ_TCP_KEEPALIVE_IDLE, 10);   // 10s空闲后开始探测
sock.setsockopt(ZMQ_TCP_KEEPALIVE_CNT, 3);     // 3次探测
sock.setsockopt(ZMQ_TCP_KEEPALIVE_INTVL, 5);   // 5s探测间隔
// → 最慢 10+3*5=25s 检测到断链

// 重连间隔
sock.setsockopt(ZMQ_RECONNECT_IVL, 100);       // 100ms (默认已是)
sock.setsockopt(ZMQ_RECONNECT_IVL_MAX, 5000);  // 5s 上界, 启用指数退避
```

### 8.3 针对 2.5s/0.5s IF Down 场景

| 方面 | 当前表现 | 启用心跳后 |
|------|---------|-----------|
| TCP 连接 | 不断开 | 不断开 |
| 消息丢失 | 不丢失 | 不丢失 |
| 延迟抖动 | ~200-500ms | ~200-500ms |
| 故障感知 | 不感知 | PING 可能超时触发误断 |

> **注意**: 如果配置了 `HEARTBEAT_TIMEOUT=1500ms`，在 2.5s 周期的 0.5s IF Down 下，心跳大概率不会超时（因为窗口仅 0.5s，而 timeout 是 1.5s 且最近的 PONG/数据会重置计时器）。但如果心跳恰好在 IF Down 期间到期且网络恢复后数据又堆积，可能有少量误判。建议 heartbeat_timeout > IF Down 最大持续时间。

### 8.4 RPC 丢失防护

对于 **request 丢失**:
- 当前 RPC 框架的 `GetStreamPeer` 有重试机制，可以覆盖
- 但前提是 `zmq_send` 返回了错误，如果消息已经进入 TCP buffer 后 TCP 断开，RPC 框架无法感知

对于 **response 丢失**:
- 这是更隐蔽的问题: Server 已处理，response 丢在网络/ROUTER 里
- Client 侧表现为 **RPC 超时** (K_RPC_UNAVAILABLE)
- 上层需要 **幂等性** 设计来安全重试
