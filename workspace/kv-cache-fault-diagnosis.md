# KVCache 故障定界傻瓜手册

## 责任方速认

| 责任方 | 典型特征 |
|--------|----------|
| **用户/业务** | 错误码 0/2/3/8 |
| **OS/网络** | ZMQ fault 增量 > 0、ping 不通、TCP 连接失败 |
| **URMA** | 错误码 1004/1006/1008/1009/1010、UB 端口 down |
| **数据系统** | ZMQ fault 增量 = 0 + 对端在运行、Worker 崩溃、主动拒绝 |
| **etcd** | 错误码 25、etcd timeout/unavailable |

---

## 第一步：抓错误码

```bash
grep "DS_KV_CLIENT_PUT" $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
grep "DS_KV_CLIENT_GET" $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
```

---

## 第二步：按错误码定界

### 错误码 0/2/3/8 → 用户业务

| 错误码 | 含义 | 常见原因 |
|--------|------|----------|
| 0 | K_OK | Get 查不到时看 respMsg |
| 2 | K_INVALID | 参数为空、大小为 0 |
| 3 | K_NOT_FOUND | key 不存在 |
| 8 | K_NOT_READY | 未 Init |

```bash
grep "K_INVALID\|K_NOT_FOUND\|K_NOT_READY" $LOG/ds_client_access_*.log
```

---

### 错误码 5 → OS / etcd / URMA

```bash
grep -E 'K_RUNTIME_ERROR|Get mmap entry failed|etcd is|urma' $LOG/datasystem_worker.INFO.log | tail -50
```

| 错误信息 | 责任方 | 排查 |
|----------|--------|------|
| `Get mmap entry failed` | OS | `ulimit -l unlimited`；检查内存锁定限制 |
| `etcd is timeout/unavailable` | etcd | `etcdctl endpoint status` |
| `urma ... payload ...` | URMA | 查 URMA 日志 |

---

### 错误码 6/7/13/18 → OS

| 错误码 | 含义 | 排查 |
|--------|------|------|
| 6 | K_OUT_OF_MEMORY | `dmesg \| grep -i oom`；`free -h` |
| 7 | K_IO_ERROR | `dmesg`；检查磁盘 |
| 13 | K_NO_SPACE | `df -h` |
| 18 | K_FILE_LIMIT_REACHED | `ulimit -n` |

---

### 错误码 1004/1006/1008/1009/1010 → URMA

| 错误码 | 含义 | 责任方 | 排查 |
|--------|------|--------|------|
| 1004 | K_URMA_ERROR | 海思 | 驱动/硬件；`dmesg` |
| 1006 | K_URMA_NEED_CONNECT | 看日志细分 | 对端重启？UB不稳？ |
| 1008 | K_URMA_TRY_AGAIN | 看日志细分 | JFS 自愈成功？失败？ |
| 1009 | K_URMA_CONNECT_FAILED | 海思 | `ifconfig ub0 DOWN` |
| 1010 | K_URMA_WAIT_TIMEOUT | 分布式并行实验室 | SDK 重试白名单自愈 |

```bash
grep -E '\[URMA_' $LOG/*.INFO.log | tail -20
```

---

## 第三步：核心四个错误码定界（19 / 23 / 1001 / 1002）

### ZMQ fault 判断：定界的核心

**看这两个指标在故障时段的 delta（差值）**：

```bash
grep 'zmq_send_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
grep 'zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
```

| 判断 | 含义 | 倾向 |
|------|------|------|
| delta = 0 | 无 ZMQ 层 I/O 失败 | 对端慢 / 数据系统问题 |
| delta > 0 | 有 ZMQ 层 I/O 失败 | 网络 / OS / 防火墙问题 |

---

### 错误码 19：K_TRY_AGAIN

**含义**：RPC 处理慢，需要重试

| 判断 | 责任方 | 依据 |
|------|--------|------|
| **无 ZMQ 失败增量**（delta=0） | **数据系统** | 对端处理慢 |
| **有 ZMQ 失败增量**（delta>0） | **OS/网络** | 网络丢包/断连 |

**查看**：
```bash
grep 'zmq_send_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
grep 'zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
```

---

### 错误码 23：K_CLIENT_WORKER_DISCONNECT

**含义**：Client 与 Worker 连接断开

| 判断 | 责任方 | 依据 |
|------|--------|------|
| 对端进程不存在 | **数据系统** | Worker 崩溃 |
| 对端进程存在，但 ping 不通 | **OS/网络** | 网络/防火墙 |
| 对端进程存在，ping 通，但心跳超时 | **OS/网络** | 对端负载高/网络抖 |

**查看**：
```bash
ping <peer_ip>
ssh <peer_ip> "pgrep -af datasystem_worker"
grep 'Cannot receive heartbeat' $LOG/datasystem_worker.INFO.log | tail -20
```

---

### 错误码 1001：K_RPC_DEADLINE_EXCEEDED

**含义**：RPC 处理超时

| 判断 | 责任方 | 依据 |
|------|--------|------|
| `[RPC_SERVICE_UNAVAILABLE]` | **数据系统** | 对端主动拒绝 |
| **无 ZMQ 失败增量** + 对端在运行 | **数据系统** | 对端处理慢 |
| **有 ZMQ 失败增量** 或对端不在 | **OS/网络** | 网络问题 |

**查看**：
```bash
grep 'zmq_send_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
pgrep -af datasystem_worker
ss -tnlp | grep <worker_port>
grep 'RPC_SERVICE_UNAVAILABLE' $LOG/datasystem_worker.INFO.log | tail -20
```

---

### 错误码 1002：K_RPC_UNAVAILABLE

**含义**：RPC 服务不可用

| 判断 | 责任方 | 依据 |
|------|--------|------|
| 对端进程不存在 | **数据系统** | Worker 崩溃 |
| `[TCP_CONNECT_FAILED]` + 对端存活 | **OS/网络** | 防火墙/端口 |
| `[TCP_CONNECT_RESET]` | **OS/网络** | 网络闪断 |
| `zmq_event_handshake_failure_total`↑ | **数据系统** | TLS/证书问题 |
| **无 ZMQ 失败增量** + 对端在运行 | **数据系统** | 对端处理慢/拒绝 |
| **有 ZMQ 失败增量** | **OS/网络** | 网络问题 |

**查看**：
```bash
ping <peer_ip>
ssh <peer_ip> "pgrep -af datasystem_worker"
grep 'zmq_last_error_number' $LOG/datasystem_worker.INFO.log | tail -3
grep 'zmq_event_handshake_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
```

**errno 参考**：

| errno | 含义 |
|-------|------|
| 11 | 背压（非错） |
| 101 | 路由不可达 |
| 104 | 对端 reset |
| 110 | TCP 超时 |
| 111 | 端口无监听 |
| 113 | 主机不可达 |

---

## 定界决策树

```
错误码 19/1001/1002
       │
       ▼
  对端是否存活？
       │
   ┌───┴───┐
   ▼       ▼
  存活     不存活
   │       │
   ▼       ▼
查 ZMQ   → 数据系统
失败增量   (对端崩溃)
   │
┌──┴──┐
▼     ▼
delta=0  delta>0
  │       │
  ▼       ▼
数据系统  OS/网络
(对端慢)  (网络问题)
```

```
错误码 23
       │
       ▼
  对端是否存活？
       │
   ┌───┴───┐
   ▼       ▼
  存活     不存活
   │       │
   ▼       ▼
ping通?  → 数据系统
   │     (对端崩溃)
   ▼
 ping不通 → OS/网络
   │
   ▼
 心跳超时 → OS/网络
   (对端负载高)
```

---

## 日志位置

| 类型 | 路径 |
|------|------|
| 接口错误码 | `$LOG/ds_client_access_*.log` |
| ZMQ 指标/错误 | `$LOG/datasystem_worker.INFO.log` |
| URMA 日志 | `$LOG/*.INFO.log` 中的 `[URMA_]` |
| 资源指标 | `$LOG/resource.log` 中的 `WAITING_TASK_NUM` |
