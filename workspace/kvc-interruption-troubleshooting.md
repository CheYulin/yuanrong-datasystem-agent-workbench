# 12.2 KVCache中断异常

## 故障现象

- 客户侧SDK成功率 < 90%
- 客户KVC Worker成功率 < 90%

---

## 快速定界

### 第一步：抓错误码分布

```bash
grep "DS_KV_CLIENT_PUT" $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
grep "DS_KV_CLIENT_GET" $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
```

### 第二步：直接出结论

| 错误码 | 枚举 | 错误消息 | 责任方 | 结论 | 证据/日志 |
|--------|------|----------|--------|------|-----------|
| 0 | K_OK | OK | 用户 | key不存在 | respMsg含"NOT_FOUND" |
| 2 | K_INVALID | Invalid parameter | 用户 | 参数非法 | 空key/大小为0 |
| 3 | K_NOT_FOUND | Key not found | 用户 | key不存在 | Get时 |
| 6 | K_OUT_OF_MEMORY | Out of memory | OS | 内存不足 | `dmesg\|grep -i oom` |
| 7 | K_IO_ERROR | IO error | OS | IO错误 | `dmesg`/磁盘smart |
| 8 | K_NOT_READY | Not ready | 用户 | 未Init | |
| 13 | K_NO_SPACE | No space available | OS | 磁盘满 | `df -h` |
| 18 | K_FILE_LIMIT_REACHED | Limit on the number of open file descriptors reached | OS | fd耗尽 | `ulimit -n` |
| 25 | K_MASTER_TIMEOUT | The master may timeout/dead | etcd | Master超时 | `etcdctl endpoint status` |
| 29 | K_SERVER_FD_CLOSED | The server fd has been closed | 数据系统 | Worker退出 | `[HealthCheck] Worker is exiting` |
| 31 | K_SCALE_DOWN | The worker is exiting | 数据系统 | 缩容中 | SDK自重试 |
| 32 | K_SCALING | The cluster is scaling | 数据系统 | 扩容中 | SDK自重试 |
| 1004 | K_URMA_ERROR | Urma operation failed | URMA | 驱动/硬件问题 | `dmesg\|grep ub`/`ibstat` |
| 1009 | K_URMA_CONNECT_FAILED | Urma connect failed | URMA | UB端口down | `ifconfig ub0` |
| 1010 | K_URMA_WAIT_TIMEOUT | Urma wait for completion timed out | 数据系统 | SDK自愈 | 无需处置 |

### 第三步：需进一步分析

| 错误码 | 枚举 | 错误消息 | 可能原因 | 进一步查看 |
|--------|------|----------|----------|------------|
| 5 | K_RUNTIME_ERROR | Runtime error | OS/etcd/URMA | `grep -E 'mmap\|etcd\|urma' $LOG/...` |
| 19 | K_TRY_AGAIN | Try again | 数据系统/OS | ZMQ failure delta |
| 23 | K_CLIENT_WORKER_DISCONNECT | Client and Worker disconnect | 数据系统/OS | 对端进程是否存在 |
| 1001 | K_RPC_DEADLINE_EXCEEDED | RPC deadline exceeded | 数据系统/OS | ZMQ failure delta + 对端状态 |
| 1002 | K_RPC_UNAVAILABLE | RPC unavailable | 数据系统/OS/etcd | 对端状态 + 日志前缀 |
| 1006 | K_URMA_NEED_CONNECT | Urma needs to reconnet | 数据系统/URMA | remoteInstanceId是否变化 |
| 1008 | K_URMA_TRY_AGAIN | Urma operation failed, try again | URMA | 是否有RECREATE_JFS_FAILED |

---

## 故障树

### 一、用户问题

```
【用户】
├─ code=0 + respMsg含"NOT_FOUND" ── K_NOT_FOUND ── key不存在
├─ code=2 ── K_INVALID ── 参数非法
├─ code=3 ── K_NOT_FOUND ── key不存在
└─ code=8 ── K_NOT_READY ── 未Init
```

---

### 二、OS问题

```
【OS】
├─ code=5 + "Get mmap entry failed"
│   └─ mlock限制 ── 处置: ulimit -l unlimited
├─ code=6 ── 内存不足 ── 处置: dmesg|grep -i oom
├─ code=7 ── IO错误 ── 处置: dmesg/磁盘smart
├─ code=13 ── 磁盘满 ── 处置: df -h
├─ code=18 ── fd耗尽 ── 处置: ulimit -n
├─ code=1002 + "[UDS_CONNECT_FAILED]" ── UDS路径/权限
├─ code=1002 + "[SHM_FD_TRANSFER_FAILED]" ── fd耗尽/权限 ── 处置: ulimit -n
├─ ping不通 ── 防火墙/路由 ── 处置: iptables -L
├─ code=1002 + "[TCP_CONNECT_FAILED]" + 对端存活 ── 端口/防火墙 ── 处置: ss -tnlp
├─ code=1002 + "[TCP_CONNECT_RESET]" ── 网络闪断 ── 处置: dmesg
└─ ZMQ failure delta>0 ── 网络丢包/断连
    证据: zmq_send/receive_failure_total delta>0
    日志: [ZMQ_SEND_FAILURE_TOTAL] errno=...
```

---

### 三、etcd问题

```
【etcd】
├─ code=5 + "etcd is timeout/unavailable"
│   └─ 处置: etcdctl endpoint status
├─ code=25 ── K_MASTER_TIMEOUT ── Master超时 ── 处置: etcdctl endpoint status
└─ code=1002 + "etcd is ..."
    └─ 处置: etcdctl endpoint status
```

---

### 四、URMA问题

```
【URMA】
├─ code=5 + "urma ... payload ..." ── UB传输失败
├─ code=1004 ── K_URMA_ERROR ── 驱动/硬件 ── 处置: dmesg|grep ub / ibstat
├─ code=1006 ── K_URMA_NEED_CONNECT
│   ├─ remoteInstanceId变化 ── 对端Worker重启（正常，SDK自重连）
│   └─ instanceId不变 ── UB链路不稳 ── 查: [URMA_POLL_ERROR]
├─ code=1008 ── K_URMA_TRY_AGAIN
│   ├─ [URMA_RECREATE_JFS_FAILED]连续 ── JFS重建失败 ── 查UMDK/驱动
│   └─ 无FAILED ── 自愈，无需处置
├─ code=1009 ── K_URMA_CONNECT_FAILED ── UB端口down ── 处置: ifconfig ub0 / ubinfo
├─ code=1010 ── K_URMA_WAIT_TIMEOUT ── SDK重试白名单自愈
└─ "fallback to TCP/IP payload" ── UB降级到TCP（性能劣化，非通断）
```

---

### 五、数据系统问题

```
【数据系统】
├─ code=19 ── K_TRY_AGAIN
│   └─ ZMQ failure delta=0 ── 对端处理慢 ── 查: WAITING_TASK_NUM/CPU/锁
│       代码: zmq_msg_queue.h:884（recv返回EAGAIN背压）
│
├─ code=23 ── K_CLIENT_WORKER_DISCONNECT
│   ├─ 对端进程不存在 ── Worker崩溃 ── 查: grep "Worker is exiting"
│   └─ ping通 + 心跳超时 ── 对端负载高 ── 查: WAITING_TASK_NUM
│       代码: listen_worker.cpp:114（心跳超时）
│
├─ code=29 ── Worker退出 ── 查: [HealthCheck] Worker is exiting
├─ code=31 ── 缩容中 ── SDK自重试
├─ code=32 ── 扩容中 ── SDK自重试
│
├─ code=1001 ── K_RPC_DEADLINE_EXCEEDED
│   ├─ [RPC_SERVICE_UNAVAILABLE] ── 对端拒绝服务
│   │   代码: zmq_stub_conn.cpp:224
│   └─ ZMQ failure delta=0 + 对端存活 ── 对端处理慢
│       代码: zmq_service.cpp:724（remainingTime<=0）
│
└─ code=1002 ── K_RPC_UNAVAILABLE
    ├─ 对端进程不存在 ── Worker崩溃
    ├─ [RPC_SERVICE_UNAVAILABLE] ── 对端主动拒绝
    ├─ zmq_event_handshake_failure_total↑ ── TLS握手失败
    │   代码: zmq_monitor.cpp:149,155,162
    └─ ZMQ failure delta=0 + 对端存活 ── 对端处理慢/拒绝
        代码: zmq_socket_ref.cpp:175,211（ZMQ真失败）
```

---

## ZMQ failure 判断（核心定界）

**查看**：
```bash
grep 'zmq_send_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
grep 'zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
```

**代码逻辑**（`zmq_socket_ref.cpp`）：
```
ZMQ send/recv 返回-1时：
  errno == EAGAIN  → K_TRY_AGAIN（背压，非故障）
                     → zmq_send/receive_try_again_total++
  errno == EINTR   → K_INTERRUPTED
  其他 errno       → K_RPC_UNAVAILABLE
                     → zmq_send/receive_failure_total++
                     → zmq_last_error_number = errno
                     → 网络errno → zmq_network_error_total++
```

**网络类errno**（`IsZmqSocketNetworkErrno`）：
ECONNREFUSED / ECONNRESET / ECONNABORTED / EHOSTUNREACH / ENETUNREACH / ENETDOWN / ETIMEDOUT / EPIPE / ENOTCONN

| 判断 | 结论 |
|------|------|
| delta=0 | 无ZMQ层I/O失败 → 对端慢/数据系统 |
| delta>0 | 有ZMQ层I/O失败 → 网络/OS |

---

## errno 参考

| errno | 枚举 | 含义 | 典型原因 |
|-------|------|------|----------|
| 11 | EAGAIN/EWOULDBLOCK | 背压 | 对方缓冲区满（非错） |
| 101 | ENETUNREACH | 路由不可达 | 路由配置 |
| 104 | ECONNRESET | 对端reset | 对端崩溃/网络闪断 |
| 110 | ETIMEDOUT | TCP超时 | 网络慢/防火墙丢包 |
| 111 | ECONNREFUSED | 端口无监听 | Worker未启动/端口未开 |
| 113 | EHOSTUNREACH | 主机不可达 | 主机层面网络不通 |

---

## 归责总览

| 责任方 | 判断依据 |
|--------|----------|
| **数据系统** | fault delta=0+对端在运行 / 主动拒绝 / Worker崩溃退出 / TLS握手失败 |
| **OS/网络** | fault delta>0 / ping不通 / 防火墙 / TCP reset / UDS失败 |
| **用户** | code=0(respMsg异常)/2/3/8 |
| **URMA** | code=1004/1006/1008/1009/1010 / fallback to TCP |
| **etcd** | code=25 / 日志含"etcd is ..." |

---

## 日志位置

| 类型 | 路径 |
|------|------|
| 接口错误码 | `$LOG/ds_client_access_*.log` |
| ZMQ指标/错误 | `$LOG/datasystem_worker.INFO.log` |
| URMA日志 | `$LOG/*.INFO.log` 含`[URMA_]` |
