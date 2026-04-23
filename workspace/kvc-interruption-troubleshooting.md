# 12.2 KVCache中断异常

## 故障现象

- 客户侧SDK成功率 < 90%
- 客户KVC Worker成功率 < 90%

---

## 快速定界：错误码 → 责任方

### 第一步：抓错误码

```bash
grep "DS_KV_CLIENT_PUT" $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
grep "DS_KV_CLIENT_GET" $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
```

### 第二步：按错误码定界

| 错误码 | 含义 | 责任方 |
|--------|------|--------|
| 19 | `K_TRY_AGAIN` | 见下方 19 定界 |
| 23 | `K_CLIENT_WORKER_DISCONNECT` | 见下方 23 定界 |
| 1001 | `K_RPC_DEADLINE_EXCEEDED` | 见下方 1001 定界 |
| 1002 | `K_RPC_UNAVAILABLE` | 见下方 1002 定界 |

---

## 错误码 19：K_TRY_AGAIN

**含义**：RPC 处理慢，需要重试

### 定界

| 判断 | 责任方 | 依据 |
|------|--------|------|
| **无 ZMQ 失败增量**（delta=0） | **数据系统** | 对端处理慢 |
| **有 ZMQ 失败增量**（delta>0） | **OS/网络** | 网络丢包/断连 |

### 查看

```bash
# ZMQ失败增量
grep 'zmq_send_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
grep 'zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
```

---

## 错误码 23：K_CLIENT_WORKER_DISCONNECT

**含义**：Client 与 Worker 连接断开

### 定界

| 判断 | 责任方 | 依据 |
|------|--------|------|
| 对端进程不存在 | **数据系统** | Worker 崩溃 |
| 对端进程存在，但 ping 不通 | **OS/网络** | 网络/防火墙 |
| 对端进程存在，ping 通，但心跳超时 | **OS/网络** | 对端负载高/网络抖 |

### 查看

```bash
ping <peer_ip>
ssh <peer_ip> "pgrep -af datasystem_worker"
grep 'Cannot receive heartbeat' $LOG/datasystem_worker.INFO.log | tail -20
```

---

## 错误码 1001：K_RPC_DEADLINE_EXCEEDED

**含义**：RPC 处理超时

### 定界

| 判断 | 责任方 | 依据 |
|------|--------|------|
| `[RPC_SERVICE_UNAVAILABLE]` | **数据系统** | 对端主动拒绝 |
| **无 ZMQ 失败增量** + 对端在运行 | **数据系统** | 对端处理慢 |
| **有 ZMQ 失败增量** 或对端不在 | **OS/网络** | 网络问题 |

### 查看

```bash
# ZMQ失败增量
grep 'zmq_send_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
# 对端状态
pgrep -af datasystem_worker
ss -tnlp | grep <worker_port>
# 是否主动拒绝
grep 'RPC_SERVICE_UNAVAILABLE' $LOG/datasystem_worker.INFO.log | tail -20
```

---

## 错误码 1002：K_RPC_UNAVAILABLE

**含义**：RPC 服务不可用

### 定界

| 判断 | 责任方 | 依据 |
|------|--------|------|
| 对端进程不存在 | **数据系统** | Worker 崩溃 |
| `[TCP_CONNECT_FAILED]` + 对端存活 | **OS/网络** | 防火墙/端口 |
| `[TCP_CONNECT_RESET]` | **OS/网络** | 网络闪断 |
| `zmq_event_handshake_failure_total`↑ | **数据系统** | TLS/证书问题 |
| **无 ZMQ 失败增量** + 对端在运行 | **数据系统** | 对端处理慢/拒绝 |
| **有 ZMQ 失败增量** | **OS/网络** | 网络问题 |

### 查看

```bash
ping <peer_ip>
ssh <peer_ip> "pgrep -af datasystem_worker"
# ZMQ错误码
grep 'zmq_last_error_number' $LOG/datasystem_worker.INFO.log | tail -3
# TLS问题
grep 'zmq_event_handshake_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
```

### errno 参考

| errno | 含义 |
|-------|------|
| 11 | 背压（非错） |
| 101 | 路由不可达 |
| 104 | 对端 reset |
| 110 | TCP 超时 |
| 111 | 端口无监听 |
| 113 | 主机不可达 |

---

## ZMQ 失败增量判断

这是定界的核心。

### 看哪些指标

```bash
grep 'zmq_send_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
grep 'zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
```

### 怎么判断

- **delta = 0**：无 ZMQ 层 I/O 失败 → 倾向对端慢/系统问题
- **delta > 0**：有 ZMQ 层 I/O 失败 → 倾向网络/防火墙/断连

---

## 归责总览

| 责任方 | 判断依据 |
|--------|----------|
| **数据系统** | 无 ZMQ 失败增量 + 对端在运行 / 主动拒绝 / Worker 崩溃 |
| **OS/网络** | 有 ZMQ 失败增量 / ping 不通 / 防火墙 / 网络闪断 |
| **用户** | 业务校验失败 / 对象不存在 / 未初始化 |
| **URMA** | 错误码 1004/1006/1008/1009/1010 |

---

## 日志位置

| 类型 | 路径 |
|------|------|
| 接口错误码 | `$LOG/ds_client_access_*.log` |
| ZMQ 指标/错误 | `$LOG/datasystem_worker.INFO.log` |
