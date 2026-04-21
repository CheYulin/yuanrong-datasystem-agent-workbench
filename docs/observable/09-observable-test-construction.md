# 09 · 可观测用例构造与验证指南

> **关键目标**：在测试里**故意造出**每一类故障，验证运行时的**四类观测信号**能够让值班 / 客户按 [08](08-fault-triage-consolidated.md) / [10](10-customer-fault-scenarios.md) 的流程**定界到五边界并定位根因**。
>
> - **错误码**：`Status::GetCode()` + `respMsg` → 业务面首要可见
> - **错误日志**：`[PREFIX]` 结构化标签 / `etcd is ...` / `[HealthCheck]` 等关键字
> - **统计信息**：Metrics Summary（Counter delta / Gauge / Histogram 的 count/avg/max）+ `resource.log` 22 项聚合
> - **节点 / 容器视图**：进程、端口、fd、iptables、tc、ulimit、dmesg、UB 端口、etcd 健康
>
> **五边界**：**用户 / DS 进程内 / 三方 etcd / URMA / OS** —— 每条用例明确覆盖哪一个边界的哪一类信号。
>
> **面向**：功能测试 / 集成测试 / 性能测试同学；值班联调。
>
> **使用姿势**：本文**不提供代码级 ST 样板**，也**不依赖特定代码仓**里的脚本。测试同学按自己的测试框架、部署方式、脚本风格落地即可；本文只规定**注入手段 / 期望证据 / 验收清单**。
>
> **前置阅读**：
> - [`08-fault-triage-consolidated.md`](08-fault-triage-consolidated.md) 值班内部五边界定界
> - [`10-customer-fault-scenarios.md`](10-customer-fault-scenarios.md) 客户侧场景

---

## 1. 四类观测面 × 取证 × 断言

**核心原则**：每个用例**四类证据全部到位**，否则视为半成品。四类缺一 → 定位定界链条就断一段。

| 观测面 | 证据形态 | 测试里怎么取 | 通用断言手段 |
|--------|---------|------------|------------|
| **错误码** | `Status::GetCode()` + `GetMsg()`；`ds_client_access_<pid>.log` 第一列 `code` | SDK 返回值直接断；`grep '^<code> \|' ...`；`awk -F'\|' '{print $1}' \| sort \| uniq -c` | 等值断言 / 子串匹配 / 错误码分布阈值 |
| **错误日志** | `[PREFIX]` 结构化标签、`etcd is ...` / `[HealthCheck]` / `fallback to TCP/IP payload` 等 | 捕获 glog 文件；`grep -E '\[...\]'`；`grep 'etcd is'` | `grep -c '[PREFIX]' ≥ 1`；前缀家族覆盖数 |
| **统计信息** | Metrics Summary 的 Counter delta、Gauge 当前值、Histogram count/avg/max；`resource.log` 22 项 | `grep 'Compare with' datasystem_worker.INFO.log`；读 `resource.log`；业务监控平台 | delta ≥ N / Gauge 阈值 / Histogram 比值 |
| **节点 / 容器视图** | 进程、端口、fd 数、iptables、tc、ulimit、dmesg、UB 端口、etcd 健康 | `pgrep` / `ss -tnlp` / `ls /proc/<pid>/fd` / `iptables -L -n` / `tc qdisc show` / `ifconfig ub0` / `etcdctl endpoint status` | shell 返回码 + grep；或用例报告附贴文本 |

**陷阱清单**（写进用例备注）：

1. **`K_NOT_FOUND → K_OK` 陷阱**：`Get` 查不到对象时 access log 会记成 `code=0` + `respMsg` 含 NOT_FOUND。**断言必须用 SDK 返回的 `rc.GetCode()==K_NOT_FOUND`**，不要只数 access log 第一列非 0。
2. **客户端 stub 的主路径不走 blocking recv**：服务端慢导致的超时**不会**让 `zmq_receive_failure_total` 增长。**期望 `zmq_*_failure_total = 0` + client 侧 `K_RPC_DEADLINE_EXCEEDED` 才是正确的"对端慢"断言**。
3. **`zmq_receive_try_again_total` 仅在 blocking 模式计数**。非 blocking 场景下永远 0，别当超时断言。
4. **Histogram delta 的 `max` 是 periodMax**（每次 dump 后清零），不是累计 max。跨周期比较必须锚定同一个 `cycle=N`。
5. **Metrics Summary 周期默认 10 秒**。用例 wall time 必须 **≥ 2 个周期 + 约 15 秒缓冲**，否则 grep 到空 Summary 是常见翻车。
6. **`log_monitor_exporter` 必须 `harddisk`**，否则 `resource.log` 不会生成。
7. **`[SHM_FD_TRANSFER_FAILED]` 归 OS 边界**（同机 UDS / SCM_RIGHTS / fd / `ulimit -l`），不要误归 DS 进程内。

---

## 2. 用例流程（五步）

每条故障注入用例都按这个顺序跑，任一步骤失败立即停并清理：

```
┌────────────────────────────────────────────┐
│ ① 故障前 · 记录 baseline                    │
│    grep 'Compare with' $LOG | tail -3       │
│    拿基线 metric 值、错误码分布              │
└──────────────┬─────────────────────────────┘
               ▼
┌────────────────────────────────────────────┐
│ ② 注入 · 执行故障构造命令                    │
│    iptables / tc / kill / ulimit / systemctl│
│    / ifconfig ub0 down / dd 等              │
└──────────────┬─────────────────────────────┘
               ▼
┌────────────────────────────────────────────┐
│ ③ 故障中 · 触发业务并实时观察               │
│    业务压测脚本 or SDK 单次调用               │
│    同步 watch 日志与 metrics                │
└──────────────┬─────────────────────────────┘
               ▼
┌────────────────────────────────────────────┐
│ ④ 故障后 · 四类证据对比                      │
│    错误码分布 / [PREFIX] 命中 / metrics delta│
│    与基线 diff，写入用例报告                  │
└──────────────┬─────────────────────────────┘
               ▼
┌────────────────────────────────────────────┐
│ ⑤ 恢复验证 · 撤销注入，确认自动恢复          │
│    iptables -D / tc del / kill -CONT / up   │
│    metrics 回 baseline；业务成功率回 100%    │
└────────────────────────────────────────────┘
```

**关键原则**：

- **完整业务路径**：故障注入 → 断言之间必须**走业务 SDK 调用**（Put/Get/Create/Publish 等完整一次），不要只跑 `ping`。否则 metric 埋点走不到。
- **业务流量持续 ≥ 20 秒**（Metrics Summary 周期 × 2），避免"周期触发但未 flush" → grep 为空。
- **正常 + 故障两段**：每个用例同时覆盖"基线正常"和"注入故障"，否则无法证明是本次故障造成 delta。
- **注入必成对**：任何 `iptables -I` / `tc qdisc add` / `ulimit -*` / `ifconfig down` / `kill -STOP` **都要有配对的清理**（`-D` / `del` / `restore` / `up` / `-CONT`），建议用 `trap EXIT` 保证异常退出也清理。
- **断言映射到边界**：用例命名 / 备注明确声明"覆盖五边界中的哪一个"。
- **恢复验证不是可选**：扩缩容 / Worker 重启 / UB 降级场景必须证明系统能自恢复。

---

## 3. 按五边界的用例构造

每个小节结构统一：**注入方法 → 四类证据 → 恢复 / 自恢复验证**。

### 3.1 用户边界

> 目标：业务通过**错误码 + respMsg** 立即自证是业务侧参数 / Init / 生命周期问题，不打扰 DS。

#### 3.1.1 `K_INVALID(2)`：参数非法

**注入**：空 key / `dataSize=0` / keys 与 sizes 长度不等 / batch 超过 `OBJECT_KEYS_MAX_SIZE_LIMIT`。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_INVALID(2)` + `respMsg` 含 `The objectKey is empty` / `dataSize should be bigger than zero` / `length not match` / `OBJECT_KEYS_MAX_SIZE_LIMIT` |
| 日志 | Client access log 第一列 `2`；Client INFO log 含 `respMsg` 字符串 |
| 统计 | `client_put_error_total` / `client_get_error_total` +1 |
| 节点视图 | 无需检查（业务层） |

#### 3.1.2 `K_NOT_FOUND(3)`：对象不存在（陷阱）

**注入**：直接 `Get` 一个未 Put 过的 key。

| 观测面 | 期望 |
|--------|------|
| 错误码 | **`rc.GetCode() == K_NOT_FOUND`** + `respMsg` 含 `Can't find object` 或 `NOT_FOUND` |
| 日志 | Client access log **第一列可能是 `0`**（K_NOT_FOUND→K_OK 陷阱）；`respMsg` 里才有真相 |
| 统计 | 通常不影响 `client_get_error_total` |

**陷阱验收**：断言同一 TraceID 的 access log 行 `code=0` 但 `respMsg` 含 `Can't find object`。

#### 3.1.3 `K_NOT_READY(8)`：未 Init

**注入**：业务代码不调 `Init` 直接调用 `MSet` / `MGet`。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_NOT_READY(8)` |
| 日志 | Client INFO log 含 `ConnectOptions was not configured` 等关键字 |

### 3.2 DS 进程内边界

> 目标：信号出现在 **DS 实现**（Worker / Master / SDK 处理路径）时，用"错误码 + `[RPC_*]` 日志 + 线程池 metrics + 对端进程仍活"合证，运维可直接判给 DS。

#### 3.2.1 对端处理慢 → `[RPC_RECV_TIMEOUT]` + 所有 ZMQ fault counter = 0

**注入**（任选一种）：
- 在服务端插入人为延迟（需华为 DS 提供 debug 构建或注入钩子让服务端 handler sleep 超过客户端 RPC 超时）；
- `tc qdisc add dev eth0 root netem delay <timeout+100>ms` 给网络路径加延迟。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_RPC_DEADLINE_EXCEEDED(1001)` 或 `K_TRY_AGAIN(19)` |
| 日志 | `[RPC_RECV_TIMEOUT]` |
| 统计 | **`zmq_send_failure_total` delta == 0**；**`zmq_receive_failure_total` delta == 0**；`zmq_receive_io_latency.avg` 升高；`resource.log` 对端 `WORKER_OC_SERVICE_THREAD_POOL.WAITING_TASK_NUM` 可能堆积 |
| 节点 | 对端 Worker 进程仍活（`pgrep datasystem_worker` 有结果）且端口 LISTEN |

> **这是典型"对端慢 vs 网络坏"分流**。`fault counter = 0` 是关键断言，不是"没加上"。

**恢复**：撤销注入（`tc qdisc del ...` 或关闭服务端延迟）→ `K_RPC_DEADLINE_EXCEEDED` 停止出现。

#### 3.2.2 对端拒绝 → `[RPC_SERVICE_UNAVAILABLE]`

**注入**：需华为 DS 提供注入钩子让 service 主动回失败（模拟 shutting down / 状态不对）。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_RPC_UNAVAILABLE(1002)` |
| 日志 | `[RPC_SERVICE_UNAVAILABLE]` |
| 统计 | 所有 ZMQ fault counter 可能为 0（服务端主动回包，非网络失败） |
| 节点 | 对端进程活且端口 LISTEN（→ 确认**不是 OS 网络**） |

#### 3.2.3 ZMQ 相关问题 → `zmq_gateway_recreate_total++` / `zmq_event_disconnect_total++`

**注入**：`kill -9 <worker_pid>` 让对端 crash；SDK 再发请求。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_RPC_UNAVAILABLE(1002)` |
| 日志 | `[TCP_CONNECT_RESET]` 或 `[RPC_SERVICE_UNAVAILABLE]` |
| 统计 | `zmq_gateway_recreate_total` delta > 0；`zmq_event_disconnect_total` 异步可能迟到；`zmq_last_error_number` 可能 `104=ECONNRESET` |
| 节点 | 对端进程**不在**（确认 DS 边界：crash / 未拉起） |

**恢复验证**（必做）：等待编排拉起新 Worker → 断言 SDK **自动切流**到新 pid、业务成功率回 100%、`worker_object_count` 回升。

#### 3.2.4 握手失败 → `zmq_event_handshake_failure_total++`

**注入**：TLS / 认证配置错配。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_RPC_UNAVAILABLE(1002)` |
| 统计 | `zmq_event_handshake_failure_total` delta > 0 |

#### 3.2.5 心跳超时 → `K_CLIENT_WORKER_DISCONNECT(23)`

**注入**：`kill -STOP <worker_pid>` 让 Worker 不响应心跳。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_CLIENT_WORKER_DISCONNECT(23)` |
| 日志 | Client log `Cannot receive heartbeat from worker.` |
| 统计 | `worker_object_count` 可能无变化（Worker 未真死） |
| 节点 | `pgrep` 显示进程**在**但不响应 —— 排除 OS 网络 & 进程死 |

**恢复验证**（必做）：`kill -CONT <worker_pid>` → 心跳自恢复、`K_CLIENT_WORKER_DISCONNECT` 停止增长、SDK 下一次调用返回 `K_OK`。

#### 3.2.6 Worker 优雅退出 / 扩缩容 → `[HealthCheck]` / `K_SCALE_DOWN(31)` / `K_SCALING(32)`

**注入**：走正常扩缩容 / 灰度 / 升级流程（部署侧控制面操作）。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_SCALE_DOWN(31)` / `K_SCALING(32)` 或短暂 `K_RPC_UNAVAILABLE` |
| 日志 | Worker log `[HealthCheck] Worker is exiting now`；RPC 回包 `meta_is_moving` |
| 统计 | `worker_object_count` 下降；新 Worker 起后回升 |
| 节点 | 新 Worker pid 出现，旧 pid 消失 |

**恢复验证**（必做）：断言 **SDK 自动重试并切流到新 Worker**、维护窗口后业务成功率回 100%。如未恢复 → 判 DS bug（§4 场景 6）。

#### 3.2.7 SHM 钉住泄漏

**注入**：构造 client 不释放 ref（忘记 `DecRef`）的业务；或需华为 DS 提供注入钩子跳过 `RemoveClientRefs`。

| 观测面 | 期望 |
|--------|------|
| 错误码 | 通常 `K_OK`（容量型，最终才 `K_OUT_OF_MEMORY`） |
| 日志 | 无特征标签；监控靠 metrics |
| 统计 | **`worker_shm_ref_table_bytes` Gauge 持续上涨**；`worker_allocator_alloc_bytes_total` delta > `worker_allocator_free_bytes_total` delta；`worker_shm_unit_created_total` > `worker_shm_unit_destroyed_total`；**`worker_object_count` 持平或下降**；`resource.log` **`SHARED_MEMORY.MEMORY_USAGE` 飙升**；**`OBJECT_COUNT` 与 `OBJECT_SIZE` 反向变化**（object 数降但总字节反升，是钉住泄漏的决定性判据） |
| 节点 | 可选 `/proc/<worker_pid>/status` `VmRSS` 跟随 SHM 涨 |

**典型数值样板**：
```
SHARED_MEMORY.MEMORY_USAGE: 3.58GB → 37.5GB (100s 内)
OBJECT_COUNT:               438    → 37      (反向降)
OBJECT_SIZE:                →      ↑ 上升   (与 COUNT 反向！)
```

**恢复验证**：重启 Worker → `worker_shm_ref_table_bytes` Gauge 清 0；`SHARED_MEMORY.MEMORY_USAGE` 回到合理水位（注意 shm 对象数据会丢失，测试后续业务应有补偿逻辑）。

### 3.3 三方 etcd 边界

> 目标：让"etcd 问题"信号一目了然归给 etcd 运维，不误诊为 DS bug。

#### 3.3.1 etcd 不可用 / 超时

**注入**（任选）：
- `systemctl stop etcd`
- iptables 阻断到 etcd 的端口（`iptables -I OUTPUT -p tcp --dport 2379 -j DROP`）
- `tc netem` 给 etcd 节点加大延迟

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_MASTER_TIMEOUT(25)` 或 `K_RPC_UNAVAILABLE(1002)`（Master/Worker 视角不同） |
| 日志 | **两种字符串都要 grep 到**：Master `etcd is timeout`；Worker `etcd is unavailable` |
| 统计 | `resource.log` `ETCD_REQUEST_SUCCESS_RATE` 下跌；`ETCD_QUEUE.CURRENT_SIZE` 可能堆积；`worker_rpc_create_meta_latency` max 飙升 |
| 节点 | `etcdctl endpoint status -w table` / `etcdctl endpoint health` 失败；`ss` 确认 etcd 端口不可达；`systemctl status etcd` 非 active |

```bash
# 自动判据
grep -E 'etcd is (timeout|unavailable)' $LOG/*.INFO.log | wc -l    # > 0
etcdctl endpoint health 2>&1 | grep -c 'unhealthy\|refused'       # > 0
```

**恢复**：`systemctl start etcd` 或撤回 iptables/tc → `etcd is ...` 停止新增；`ETCD_REQUEST_SUCCESS_RATE` 回基线。

### 3.4 URMA 边界

> 目标：UB / UMDK / 驱动层问题通过 `[URMA_*]` + UB/TCP 字节对比能明确判给 UB 运维；降级场景功能正常 + TCP 字节上涨可自证。

#### 3.4.1 URMA 需要重连 → `[URMA_NEED_CONNECT]`

**注入**：`kill -9` 远端 Worker 后 SDK 发 UB 请求。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_URMA_NEED_CONNECT(1006)` 或 `K_URMA_TRY_AGAIN(1008)`（SDK 内部重试） |
| 日志 | `[URMA_NEED_CONNECT] remoteAddress=... remoteInstanceId=...` |
| 统计 | `zmq_gateway_recreate_total` 可能 +N（对端重启伴随） |
| 节点 | 对端 Worker（重启后）pid 变化；`ifconfig ub0` UP |

#### 3.4.2 JFS 重建 → `[URMA_RECREATE_JFS]` / `cqeStatus=9`

**注入**：`cqeStatus=9` 事件需要驱动层协作或华为 DS 提供模拟注入钩子；纯黑盒测试可通过压力 + UB 链路抖动间接触发。

| 观测面 | 期望 |
|--------|------|
| 日志 | `[URMA_RECREATE_JFS] requestId=... op=READ/WRITE remoteAddress=... cqeStatus=9`；可能伴 `[URMA_RECREATE_JFS_FAILED]` / `[URMA_RECREATE_JFS_SKIP]` |
| 统计 | `worker_urma_write_latency` max 飙升；`client_*_urma_*_bytes` 短暂下跌再回升 |

#### 3.4.3 CQ poll 错 → `[URMA_POLL_ERROR]`

**注入**：真实硬件 / 驱动异常；黑盒测试无法直接构造，需华为 DS 或 UB 团队提供注入能力。

| 观测面 | 期望 |
|--------|------|
| 日志 | `[URMA_POLL_ERROR] PollJfcWait failed: <status>, success=<N>, failed=<M>` |
| 节点 | `ubinfo` / `dmesg` 可能有硬件错 |

#### 3.4.4 URMA 等待超时 → `[URMA_WAIT_TIMEOUT]` / `K_URMA_WAIT_TIMEOUT(1010)`

**注入**：使远端不响应 UB 事件（可通过 `kill -STOP` 对端 Worker 模拟）；或华为 DS 提供钩子模拟 `timeoutMs<0`。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_URMA_WAIT_TIMEOUT(1010)` |
| 日志 | `[URMA_WAIT_TIMEOUT] timedout waiting for request: <requestId>` |

#### 3.4.5 UB 降级 TCP → `fallback to TCP/IP payload`（功能不降）

**注入**：`ifconfig ub0 down`。

| 观测面 | 期望 |
|--------|------|
| 错误码 | **仍为 `K_OK`**（降级成功，业务不挂） |
| 日志 | `..., fallback to TCP/IP payload.` |
| 统计 | `client_*_urma_*_bytes` delta **= 0**；`client_*_tcp_*_bytes` delta **> 0**；`worker_tcp_write_latency` 开始有分布 |
| 节点 | `ifconfig ub0` DOWN；`ubinfo` 异常 |

**恢复验证**（必做）：`ifconfig ub0 up` → `client_*_urma_*_bytes` delta 恢复 > 0；`fallback` 日志停止新增；P99 回基线。

> **建议告警条件**：`fallback to TCP/IP payload / 总 Get 请求 > 基线 3σ`。

### 3.5 OS 边界

> 目标：系统调用 / 网络栈 / 资源（fd / mmap / 磁盘 / 内存）类故障，通过 **errno / ulimit / iptables / tc / dmesg / df / ss** 的节点视图直接归 OS 运维。

#### 3.5.1 TCP 建连失败 → `[TCP_CONNECT_FAILED]` + 对端仍活

**注入**：`iptables -I OUTPUT -p tcp --dport <worker_port> -j REJECT` 或指向不存在的端口。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_RPC_UNAVAILABLE(1002)` |
| 日志 | `[TCP_CONNECT_FAILED]` |
| 统计 | `zmq_last_error_number` 非 0（如 `111=ECONNREFUSED`） |
| 节点 | **对端 Worker 进程在 + 端口 LISTEN**（→ 确认是 OS 网络而非 DS） |

**恢复**：`iptables -D OUTPUT -p tcp --dport <worker_port> -j REJECT`。

#### 3.5.2 TCP Reset / 网络闪断 → `[TCP_CONNECT_RESET]` / `[TCP_NETWORK_UNREACHABLE]`

**注入**：`tc qdisc add dev eth0 root netem loss 100%`；或随机断连。

| 观测面 | 期望 |
|--------|------|
| 日志 | `[TCP_CONNECT_RESET]` / `[TCP_NETWORK_UNREACHABLE]` |
| 统计 | `zmq_network_error_total` delta > 0；`zmq_last_error_number` ∈ `{104=ECONNRESET, 101=ENETUNREACH, 113=EHOSTUNREACH}` |
| 节点 | `tc qdisc show` 显示注入规则；`nstat / ss -ti` 重传飙升 |

**恢复**：`tc qdisc del dev eth0 root netem`。

#### 3.5.3 ZMQ 硬失败 → `[ZMQ_SEND/RECV_FAILURE_TOTAL]`

**注入**：`iptables -I OUTPUT -p tcp --dport <port> -j DROP`。

| 观测面 | 期望 |
|--------|------|
| 日志 | `[ZMQ_SEND_FAILURE_TOTAL] errno=<n>(<str>)` |
| 统计 | `zmq_send_failure_total` delta > 0；若 errno 属网络类则 `zmq_network_error_total` 同步增长；`zmq_last_error_number` ∈ 网络 errno 集 |
| 节点 | `iptables -L -n` 有 DROP / REJECT 规则 |

**恢复**：`iptables -D OUTPUT -p tcp --dport <port> -j DROP`。**验证**：`zmq_send_failure_total` delta 归零；业务成功率回 100%。

#### 3.5.4 建连等待超时 → `[SOCK_CONN_WAIT_TIMEOUT]` / `[REMOTE_SERVICE_WAIT_TIMEOUT]`

**注入**：一端监听端口但不 accept，或让 TCP 握手长时间挂起（可结合 `tc` 与防火墙策略模拟）。

| 观测面 | 期望 |
|--------|------|
| 日志 | `[SOCK_CONN_WAIT_TIMEOUT]` **或** `[REMOTE_SERVICE_WAIT_TIMEOUT]` |

> ⚠️ 旧文档里的 `[TCP_CONN_WAIT_TIMEOUT]` 在代码里**不存在**，用上述两个之一断言。

#### 3.5.5 UDS / SHM fd 传递失败 → `[UDS_CONNECT_FAILED]` / `[SHM_FD_TRANSFER_FAILED]`

**注入**：构造无效 UDS socket path（部署配置故意错）；或 `ulimit -n 64` 后起客户端触发 SCM_RIGHTS 失败。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_RPC_UNAVAILABLE(1002)` |
| 日志 | `[UDS_CONNECT_FAILED]` 或 `[SHM_FD_TRANSFER_FAILED]` |
| 节点 | `ls -la <uds_path>` 权限 / 存在性；`ulimit -n` / `/proc/sys/fs/file-max`；SELinux / AppArmor 状态 |

#### 3.5.6 fd 耗尽 → `K_FILE_LIMIT_REACHED(18)` / `EMFILE`

**注入**：`ulimit -n 64`，并发开大量客户端。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_FILE_LIMIT_REACHED(18)` |
| 节点 | `ls /proc/<pid>/fd \| wc -l` 接近 `ulimit -n` |

#### 3.5.7 mmap 失败 → `K_RUNTIME_ERROR(5)` + `Get mmap entry failed`

**注入**：`ulimit -l 0`，然后触发大对象 mmap。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_RUNTIME_ERROR(5)` |
| 日志 | `Get mmap entry failed` |
| 节点 | `/proc/<pid>/limits` `Max locked memory` 为 0 |

#### 3.5.8 磁盘满 / OOM → `K_NO_SPACE(13)` / `K_OUT_OF_MEMORY(6)`

**注入**：`dd if=/dev/zero of=<spill_path>/fill bs=1M` 把 spill 盘写满；或 cgroup `memory.limit_in_bytes` 设极低值。

| 观测面 | 期望 |
|--------|------|
| 错误码 | `K_NO_SPACE(13)` / `K_OUT_OF_MEMORY(6)` |
| 日志 | `dmesg \| grep OOM` 可能有 OOM killer 记录 |
| 统计 | `resource.log` `SPILL_HARD_DISK.SPACE_USAGE` 接近 TOTAL_LIMIT；`SHARED_MEMORY.MEMORY_USAGE` 飙升 |
| 节点 | `df -h`；`free -h`；`dmesg` |

#### 3.5.9 1002 桶码「≥ 3 种前缀」验收

同一次 1002 场景集的日志里，需能 grep 到**至少 3 种不同的 `[...]` 前缀**（TCP / UDS / ZMQ / RPC / URMA / SOCK / REMOTE / SHM_FD 家族之一），证明**不同根因触发的 1002 有不同的前缀区分能力**。

```bash
grep -oE '\[[A-Z_]+\]' $LOG/datasystem_worker.INFO.log \
  | sort -u | wc -l    # 期望 ≥ 3
```

---

## 4. 客户场景级用例蓝图

把 [`10-customer-fault-scenarios.md`](10-customer-fault-scenarios.md) 的 7 个场景反向变成端到端测试套件。每个场景组合 §3 里若干注入点，**四类证据全部产出**。

| 场景（对应 §10） | 必选注入点（§3） | 验收关注 |
|-----------------|-----------------|---------|
| Put/Create/Publish 失败 | §3.1.1（参数） + §3.5.3（ZMQ 硬失败） + §3.3.1（etcd 不可用） + §3.4.1（URMA 重连） | 错误码分布 × 边界判定一致 |
| Get 失败或「查不到」 | §3.1.2（NOT_FOUND 陷阱） + §3.2.1（对端慢） | access log 陷阱判据 |
| Put/Get 延迟异常 | §3.2.1（对端慢） + §3.4.5（UB 降级） + §3.5.2（tc netem） | Histogram max 对比、降级字节对比 |
| Client Init / 连接失败 | §3.5.1（TCP 建连失败） + §3.5.5（UDS / SHM_FD） | Init 阶段日志与错误码 |
| SHM 容量 / 泄漏 | §3.2.7（SHM 钉住） + §3.5.8（OOM） | `OBJECT_COUNT` 反向变化、`ref_table_bytes` 曲线 |
| 扩缩容 / 升级中断 | §3.2.6（优雅退出） + §3.2.5（心跳） | 新旧 pid 切换、SDK 自愈 |
| 机器 / 节点级故障 | §3.2.3（kill -9） + 节点级 dmesg / 编排日志 | 进程消失、节点 NotReady 两情况 |

**每场景 ST 骨架**（通用语言、非特定框架）：

```
# 场景 X · <现象>
# 覆盖边界: <用户 / DS / 三方 / URMA / OS 中的一个或多个>

1. 部署与基线：启动数据系统、业务客户端，正常流量跑 30s 拿基线
2. 注入（§3.x.y）：记录注入时间点
3. 触发业务：压测脚本 or SDK 单次调用，持续 ≥ 20s
4. 采集四类证据：
   - 错误码：access log awk 汇总
   - 日志：目标 [PREFIX] 的 grep -c
   - 统计：Metrics Summary delta + resource.log diff
   - 节点：iptables / ss / dmesg / ifconfig / etcdctl 快照
5. 四维断言：逐条比对"期望值"
6. 恢复：撤销注入
7. 验证自恢复：业务成功率回 100%、关键 counter 停止增长
```

---

## 5. 注入手段速查

| 注入方式 | 适用边界 | 优点 | 注意事项 |
|---------|---------|------|---------|
| **iptables** | OS（TCP 建连 / ZMQ 硬失败 / etcd 路径阻断） | 主机级 drop/reject | `-I/-A` 后必须 `-D`；跨机别忘两端；`trap EXIT` 兜底 |
| **tc qdisc netem** | OS（延迟 / 丢包 / 乱序） | 模拟真实网络抖动 | `tc qdisc del dev <nic> root` 清理；容器中需 `NET_ADMIN` |
| **kill -9** | DS（对端 crash） | 最真实 | 需要编排拉起；验收要测自动恢复 |
| **kill -STOP / -CONT** | DS（心跳） | 不杀进程、完整恢复 | 注意 Lease 超时窗口 |
| **`ifconfig ub0 down/up`** | URMA（UB 降级） | 直接触发降级 | root；验收后 `up` |
| **`ulimit -n / -l`** | OS（fd / mmap） | 进程内生效 | 父子 shell 继承；建议 `exec` 内 ulimit |
| **`systemctl stop etcd`** | 三方 etcd | 真实 | 多节点 etcd 可能要一并 stop |
| **`dd` 写盘 / cgroup memory** | OS（磁盘 / OOM） | 真实 | 清理后恢复 |
| **构造无效 UDS path / 错 tenant_id** | OS（UDS / SHM_FD） | 触发 `[SHM_FD_TRANSFER_FAILED]` | 部署侧配合 |
| **代码级注入钩子**（由华为 DS 提供 debug 构建） | 用户 / DS / URMA 里黑盒难触发的场景 | 精确 | 仅 debug 构建；release 不可用 |

> **代码级注入点**的启用方式、可用编译开关与钩子名，请向华为 DS 支持索取，不在本文档范围。

---

## 6. 脚本模板

### 6.1 通用故障注入脚本

新故障场景先套模板，再按 §3 细化。**注入与恢复成对**写在 `trap EXIT`，断言失败也会清理。

```bash
#!/bin/bash
# inject-<scenario>.sh — 五步通用模板
set -uo pipefail
LOG=${log_dir:-/var/log/datasystem}
PORT=${PORT:-<worker_port>}
DURATION=${DURATION:-30}

# ─── ⑤ 兜底清理：任何退出路径都会跑 ───
cleanup() {
  iptables -D OUTPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null
  tc qdisc del dev eth0 root 2>/dev/null
  # ifconfig ub0 up 2>/dev/null
  # kill -CONT <worker_pid> 2>/dev/null
}
trap cleanup EXIT

echo "[1/5] baseline"
grep 'Compare with' "$LOG"/datasystem_worker.INFO.log | tail -3 > /tmp/base.txt
cp "$LOG"/ds_client_access_*.log /tmp/access-baseline.log 2>/dev/null || true

echo "[2/5] inject"
iptables -I OUTPUT -p tcp --dport "$PORT" -j DROP     # 按场景替换

echo "[3/5] drive business & observe"
# 启动业务压测（测试团队自己的压测脚本）
# ...
sleep "$DURATION"

echo "[4/5] evidence"
echo "=== 错误码分布 ==="
grep DS_KV_CLIENT_ "$LOG"/ds_client_access_*.log \
  | awk -F'|' '{print $1}' | sort | uniq -c
echo "=== 结构化日志 ==="
grep -oE '\[[A-Z_]+\]' "$LOG"/datasystem_worker.INFO.log \
  | sort | uniq -c | sort -nr | head -20
echo "=== metrics delta ==="
grep 'Compare with' "$LOG"/datasystem_worker.INFO.log | tail -3
echo "=== 节点视图 ==="
iptables -L -n | grep -E 'DROP|REJECT'
pgrep -af datasystem_worker

echo "[5/5] recovery (via trap EXIT) & verify"
cleanup; trap - EXIT
sleep 20
grep 'Compare with' "$LOG"/datasystem_worker.INFO.log | tail -3
# 期望目标 counter 停止增长；错误码分布回到基线
```

### 6.2 通用证据验收脚本

```bash
#!/bin/bash
# verify-observability.sh <log-dir>
LOG=${1:-/var/log/datasystem}
FAIL=0

check() {
  if [[ "$1" -ge 1 ]]; then echo "  ✓ $2 ($1)"
  else echo "  ✗ $2 ($1)"; FAIL=1
  fi
}

echo "== 观测能力验收 =="

# ① Metrics Summary 周期触发
n=$(grep -c 'Metrics Summary, version=v0' "$LOG"/datasystem_worker.INFO.log)
check "$n" "Metrics Summary 周期（期望 ≥ 2）"

# ② 结构化标签家族覆盖
FAMS=0
for fam in TCP UDS ZMQ RPC URMA HealthCheck SOCK REMOTE SHM_FD; do
  c=$(grep -c "\[$fam" "$LOG"/datasystem_worker.INFO.log 2>/dev/null || true)
  [[ "$c" -ge 1 ]] && FAMS=$((FAMS+1))
done
check "$FAMS" "结构化日志家族覆盖（期望 ≥ 3）"

# ③ ZMQ fault 指标有输出
n=$(grep -c 'zmq_\(send\|receive\)_\(failure\|try_again\)_total' \
    "$LOG"/datasystem_worker.INFO.log)
check "$n" "ZMQ fault metrics 输出"

# ④ URMA/TCP 字节
n=$(grep -cE 'urma_(write|read)_total_bytes|tcp_(write|read)_total_bytes' \
    "$LOG"/datasystem_worker.INFO.log)
check "$n" "URMA/TCP 字节 metrics"

# ⑤ resource.log 核心字段
n=$(grep -cE 'SHARED_MEMORY|ETCD_QUEUE|OC_HIT_NUM' "$LOG"/resource.log)
check "$n" "resource.log 关键字段"

# ⑥ access log 错误码分布
n=$(grep -c DS_KV_CLIENT_ "$LOG"/ds_client_access_*.log)
check "$n" "Client access log"

[[ "$FAIL" -eq 0 ]] && { echo "PASS"; exit 0; } || { echo "FAIL"; exit 1; }
```

### 6.3 验收判据落地建议

1. 每个注入场景**独立一个 `inject-<scenario>.sh`**，结构对齐 §6.1。
2. 所有场景跑完后统一跑 `verify-observability.sh`，输出 PASS / FAIL。
3. 具体 `[PREFIX]` 命中数 / metric delta 阈值 / 错误码分布预期**写进各自 inject 脚本的断言段**（§3 里的"期望"列就是落地依据）。
4. CI 集成：每场景脚本退出码非 0 即失败；可与现有用例框架（pytest / bats / gtest / k8s job 等）对接。

---

## 7. 验收 Checklist（按五边界 × 四观测面）

### 7.1 基础观测（版本发布前必过）

- [ ] Worker 与 Client 日志都出现 `Metrics Summary, version=v0, cycle=...`（≥ 2 次）
- [ ] `Total:` 段含 **≥ 54 行 metric**（若更少，`InitKvMetrics` 未正确启用）
- [ ] `client_put_request_total` / `client_get_request_total` `+delta > 0`（有业务流量）
- [ ] 正常场景 `client_put_error_total` / `client_get_error_total` `+delta = 0`
- [ ] access log 文件存在且字段六列齐全
- [ ] `resource.log` 存在且 22 项字段齐全（含 `SHARED_MEMORY` / `ETCD_QUEUE` / 线程池 / `OC_HIT_NUM`）

### 7.2 五边界故障注入（每边界至少通过一条）

| 边界 | 最少通过项 | 判据 |
|------|-----------|------|
| **用户** | 参数非法 → `K_INVALID` + `client_put_error_total +1` | 错误码 + 统计 |
| **用户** | NOT_FOUND → `K_NOT_FOUND` + access log `code=0 + respMsg=NOT_FOUND`（陷阱验收） | 错误码 + 日志 |
| **DS 进程内** | 对端慢 → `[RPC_RECV_TIMEOUT]` + `zmq_*_failure_total = 0` + 对端进程仍活 | 日志 + 统计 + 节点 |
| **DS 进程内** | Worker 优雅退出 → `[HealthCheck]` + `K_SCALE_DOWN(31)` + `worker_object_count` 降 | 日志 + 错误码 + 统计 |
| **DS 进程内** | SHM 钉住 → `worker_shm_ref_table_bytes` 持续涨 + `worker_object_count` 持平 + **`OBJECT_COUNT` 与 `OBJECT_SIZE` 反向变化** | 统计（Gauge + resource.log） |
| **三方 etcd** | `systemctl stop etcd` → `etcd is (timeout\|unavailable)` + `K_MASTER_TIMEOUT(25)` + `ETCD_REQUEST_SUCCESS_RATE` 下跌 + `etcdctl endpoint health` 不健康 | 四类齐 |
| **URMA** | URMA 日志至少出现 NEED_CONNECT / RECREATE_JFS / POLL_ERROR 之一 | 日志 |
| **URMA** | UB 降级 → `fallback to TCP/IP payload` + `client_*_urma_*_bytes` delta=0 + `*_tcp_*_bytes` delta>0 + 业务 `K_OK` | 四类齐 |
| **OS** | iptables DROP → `[ZMQ_SEND_FAILURE_TOTAL]` + `zmq_network_error_total` delta>0 + `zmq_last_error_number` ∈ 网络 errno 集 + `iptables -L -n` 有规则 | 四类齐 |
| **OS** | 1002 桶码 ≥ 3 种前缀 | 日志家族数 |
| **OS** | `ulimit -n` 太小 → `K_FILE_LIMIT_REACHED(18)` + `/proc/<pid>/fd` 接近上限 | 错误码 + 节点 |

### 7.3 四观测面完整性（每条用例内部）

- [ ] **错误码**：access log `awk` 断言 或 SDK 返回值等值匹配
- [ ] **错误日志**：`grep -c '[PREFIX]'` ≥ 1（或前缀家族数判据）
- [ ] **统计信息**：至少一条 Counter delta 断言 + 一条 Histogram 或 Gauge 断言
- [ ] **节点 / 容器**：至少一条节点命令断言（进程 / 端口 / iptables / ulimit / UB / etcd 健康）

### 7.4 自恢复行为验收（必做）

每条有"恢复操作"的用例必须断言**故障解除后自动恢复**，否则只是"看见了"，没证明系统韧性。

| 故障 | 注入 | 恢复动作 | 自恢复判据 |
|------|------|---------|-----------|
| Worker 崩溃（§3.2.3） | `kill -9 <pid>` | 编排 / k8s 拉起新 pid | SDK **自动切流到新 pid**；业务成功率回 100%；`worker_object_count` 回升 |
| Worker 心跳挂死（§3.2.5） | `kill -STOP <pid>` | `kill -CONT <pid>` | `Cannot receive heartbeat` 停止增长；下一次调用 `K_OK` |
| 扩缩容（§3.2.6） | 启动扩缩容 | 维护窗口结束 | SDK 自重试切到新 Worker；窗口内 `K_SCALING` 比例可控 |
| UB 降级（§3.4.5） | `ifconfig ub0 down` | `ifconfig ub0 up` | `client_*_urma_*_bytes` delta 恢复 > 0；`fallback` 停止新增 |
| iptables 屏蔽（§3.5.1 / 3） | `iptables -I ...` | `iptables -D ...` | `zmq_send_failure_total` delta 归零；业务 100% |
| tc netem（§3.5.2） | `tc qdisc add ... netem` | `tc qdisc del ...` | latency histogram max 回基线 |
| etcd 不可用（§3.3.1） | `systemctl stop etcd` | `systemctl start etcd` | `etcd is ...` 停止新增；`ETCD_REQUEST_SUCCESS_RATE` 回基线 |

### 7.5 性能场景自证清白

- [ ] 四个 histogram（`zmq_send_io_latency` / `zmq_receive_io_latency` / `zmq_rpc_serialize_latency` / `zmq_rpc_deserialize_latency`）`count > 0`
- [ ] **RPC 框架占比** `(serialize+deserialize) / (send_io+recv_io+serialize+deserialize)` 稳态 < 5%，否则在用例报告给解释
- [ ] Trace ID（或 Metrics Summary `cycle=N`）能跨进程对齐

---

## 8. 端到端示例：ZMQ 发送失败（iptables 注入）

纯 shell / 运维命令视角，适合黑盒测试。

```bash
#!/bin/bash
# 覆盖边界: OS；验证: [ZMQ_SEND_FAILURE_TOTAL] + zmq_send_failure_total↑
set -uo pipefail
LOG=${log_dir:-/var/log/datasystem}
PORT=${PORT:-9876}
cleanup() { iptables -D OUTPUT -p tcp --dport "$PORT" -j DROP 2>/dev/null; }
trap cleanup EXIT

# ① 基线：正常跑 30s 业务
start_business_workload &   # 测试团队自己的压测脚本
BIZ_PID=$!
sleep 30
grep 'Compare with' "$LOG"/datasystem_worker.INFO.log | tail -3 > /tmp/baseline.txt

# ② 注入
iptables -I OUTPUT -p tcp --dport "$PORT" -j DROP

# ③ 故障中：持续压测 30s（≥ 2 个 Metrics 周期）
sleep 30

# ④ 四类证据
echo "=== 错误码 ==="
grep DS_KV_CLIENT_ "$LOG"/ds_client_access_*.log \
  | awk -F'|' '{print $1}' | sort | uniq -c
# 期望：出现 1002，且数量与压测 QPS × 时间 大致吻合

echo "=== 错误日志 ==="
grep -c '\[ZMQ_SEND_FAILURE_TOTAL\]' "$LOG"/datasystem_worker.INFO.log
# 期望：≥ 1

echo "=== 统计信息 ==="
grep 'zmq_send_failure_total' "$LOG"/datasystem_worker.INFO.log | tail -5
grep 'zmq_network_error_total' "$LOG"/datasystem_worker.INFO.log | tail -5
grep 'zmq_last_error_number' "$LOG"/datasystem_worker.INFO.log | tail -5
# 期望：delta > 0；zmq_last_error_number ∈ {113, 101, 111}

echo "=== 节点视图 ==="
iptables -L -n | grep -E 'DROP|REJECT' || echo "no rule"
# 期望：有 DROP 行命中我们注入的端口

# ⑤ 恢复
cleanup; trap - EXIT
kill $BIZ_PID 2>/dev/null || true
sleep 30
grep 'Compare with' "$LOG"/datasystem_worker.INFO.log | tail -3
# 期望：zmq_send_failure_total delta 归零
```

**反例排查（跑不过时）**：

1. **`zmq_send_failure_total` 没涨** → 确认 iptables 方向 / 端口；确认业务真的走了**对该端口的新 send**（不是本地缓存）；看 `zmq_gateway_recreate_total` 是否反而在涨（重连了）。
2. **`zmq_network_error_total` = 0 但 `zmq_send_failure_total` 涨** → errno 不属于网络类（如 `EINVAL`）；该 metric 不涨**正确**。
3. **Metrics Summary grep 为空** → 业务时长 < `log_monitor_interval_ms × 2`；或 `log_monitor_exporter` 不是 `harddisk`。
4. **`[PREFIX]` 没命中** → 日志 rotate 把旧段轮出去了，用 `*.INFO.log*` 通配；或 grep 错了前缀大小写。

---

## 9. 维护约定

1. **新增故障场景**：必须同时补齐 §3 对应边界小节的**四类证据**（错误码 / 日志 / 统计 / 节点），缺一不收入本文。
2. **`[PREFIX]` 标签**新增时，同步更新 [`08` 附录 B](08-fault-triage-consolidated.md) 与 [`10` 场景章节](10-customer-fault-scenarios.md) 对应位置。
3. **Metrics 增删**时，`08` 附录 D 全量清单与本文 §7.1"≥ 54 行"门槛同步更新。
4. **每条用例必须同时包含**"基线正常"+"故障注入"+"恢复验证"三段；缺任一段视作半成品。
5. **五边界归属**变化时（如某信号重归边界），08 §2.2、10 §二、本文 §3 三处同步。例：`[SHM_FD_TRANSFER_FAILED]` 已明确归 **OS 边界**，不归 DS 进程内。
6. **代码级注入钩子**（需 debug 构建）的启用方式请联系华为 DS 支持；**本文只描述故障现象与期望证据，不假设代码级注入可用**。
