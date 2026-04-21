# 08 · KV 故障定位定界手册（通断 × 时延）

> **谁用**：值班 / 运维 / 测试 / 研发现场排障。
> **怎么用**：接口日志看**成功率**与 **P99** → **§2** 30 秒分类 → **§3 / §4** 1–3 分钟定界 → **§5** 出工单。
> **怎么判**：五边界 — **用户 / DS 进程内 / 三方(etcd 等) / URMA / OS**。凭据均与 `yuanrong-datasystem` 主干行为对齐，代码细节请查代码库。

---

## 一、从接口日志切入

### 1.1 Access Log 字段

```
code | handleName | microseconds | dataSize | reqMsg | respMsg
  ↑      ↑            ↑            ↑         ↑         ↑
 错误码  接口名        耗时(μs)     数据大小   请求参数   响应信息
```

- **成功率 / 通断** → 看 `code`：`awk -F'|' '{print $1}' $LOG/ds_client_access_*.log | sort | uniq -c`
- **时延 / P99** → 看 `microseconds`：聚合 P99、max 或与基线对比
- **注意陷阱**：
  - `K_NOT_FOUND` 在 access log 被记成 **code=0**；业务「查不到」要看 `respMsg`
  - `K_SCALING(32)` / `K_SCALE_DOWN(31)` SDK 已自动重试，非用户错

### 1.2 两类故障分流

| 现象 | 走这一章 |
|------|---------|
| 接口大量失败、`code` 非 0 明显增多、进程挂、连接断 | **§3 通断** |
| `code` 多为 0 但 **P99↑**、`microseconds` 整体右移、抖动 | **§4 时延** |

---

## 二、五边界速记（30 秒分类）

### 2.1 错误码 → 边界总表

| 错误码 | 枚举 | 主责边界 | 备注 |
|-------|------|---------|------|
| 0 | K_OK | 用户? | 看 `respMsg`；NOT_FOUND→0 陷阱 |
| 2 / 3 / 8 | K_INVALID / K_NOT_FOUND / K_NOT_READY | **用户** | 业务参数 / Init 顺序 |
| 5 | K_RUNTIME_ERROR | **OS / 三方 / URMA** | mmap→OS；`etcd is ...`→三方；payload→URMA |
| 6 / 7 / 13 / 18 | OOM / IO / NoSpace / FdLimit | **OS** | 内存 / IO / 磁盘 / fd |
| 19 | K_TRY_AGAIN | **DS / OS** | 结合前缀；常瞬时繁忙 |
| 23 | K_CLIENT_WORKER_DISCONNECT | **DS / OS / 机器** | 先确认进程与节点 |
| 25 | K_MASTER_TIMEOUT | **三方(etcd)** | 兼查 Master 与网络 |
| 29 / 31 / 32 | ServerFdClosed / ScaleDown / Scaling | **DS 进程内** | 生命周期 / 扩缩容 |
| 1001 / 1002 | K_RPC_DEADLINE / K_RPC_UNAVAILABLE | **DS / OS / 三方** | 桶码；必看日志前缀（§3.3） |
| 1004 / 1006 / 1008 / 1009 / 1010 | K_URMA_* | **URMA** | UB 硬件 / 驱动 / 链路 |

> ⚠️ **1002 是桶码**：DS crash、OS 网络断、etcd 不可用都会给 1002，必须看 §3.3 前缀。
> ⚠️ **0 ≠ 一切正常**：Get 的 NOT_FOUND 被记成 0，业务语义异常看 `respMsg`。

### 2.2 五边界典型特征

| 边界 | 典型信号 | 核心凭据 |
|------|---------|---------|
| **用户** | code ∈ {2,3,8}，或 code=0 + `respMsg` 异常 | access log 的 `respMsg` |
| **DS 进程内** | code ∈ {23,29,31,32}；`[RPC_RECV_TIMEOUT]` + ZMQ fault=0；`[UDS_*]/[SHM_FD_TRANSFER_FAILED]`；线程池 `WAITING_TASK_NUM` 堆积 | Worker/Client INFO log、`resource.log` 线程池 |
| **三方 etcd** | `etcd is timeout` / `etcd is unavailable`；`ETCD_QUEUE` 堆积、`ETCD_REQUEST_SUCCESS_RATE`↓；code=25；`request_out.log` 异常 | `grep etcd`、`etcdctl endpoint status` |
| **URMA** | code ∈ {1004,1006,1008,1009,1010}；`[URMA_*]`；`fallback to TCP/IP payload` | `[URMA_*]`、`*_urma_*_bytes` |
| **OS** | code ∈ {6,7,13,18}；`K_RUNTIME_ERROR(5) + Get mmap entry failed`；`[TCP_*]` + 对端进程仍活；`[ZMQ_SEND/RECV_FAILURE_TOTAL]`（多为 errno / 网络栈 / 资源） | `ulimit` / `ss` / `df` / `dmesg` / iptables；`zmq_last_error_number` |

---

## 三、通断定界（1 分钟）

### 3.1 定界流程

```
         access / SDK 日志 / 告警
                   │
                   ▼
    ┌──────────────────────────┐
    │ Step1  错误码树（§3.2）   │
    │ 0→respMsg；1004+→URMA    │
    │ 1001/1002→进 Step2       │
    └────────────┬─────────────┘
                 │
    ┌────────────┼────────────┐
    │ 已判边界    │ 桶码需前缀  │
    ▼            ▼            │
 直接出结论  ┌──────────────┐ │
            │ Step2  日志   │ │
            │ 前缀分流(§3.3)│ │
            └──────┬───────┘ │
                   ▼          │
            ┌──────────────┐ │
            │ Step3  交叉   │◄┘
            │ 验证(§3.4)    │
            └──────┬───────┘
                   ▼
         主责边界 → §3.5 定位
```

### 3.2 Step 1：错误码树

```
Status →
├─ 0       → 看 respMsg：NOT_FOUND/invalid → 用户；否则跳 §4
├─ 2/3/8   → 用户
├─ 5       → 按日志串细分：
│             "Get mmap entry failed" → OS
│             "etcd is timeout/unavailable" → 三方(etcd)
│             "urma ... payload ..." → URMA
├─ 6/7/13/18 → OS（内存/IO/磁盘/fd）
├─ 25        → 三方(etcd) 为主
├─ 19/23/29/31/32 → DS 进程内
├─ 1001/1002 → 进 Step 2
└─ 1004+     → URMA
```

### 3.3 Step 2：1001 / 1002 按日志前缀分流

| 最先出现的前缀 / 信号 | 主责 | 原因 |
|---------------------|------|------|
| `[TCP_CONNECT_FAILED]` + 对端 Worker 活 | **OS** | 端口不通 / iptables / 路由 |
| `[TCP_CONNECT_FAILED]` + 对端 Worker 无 | **DS / 机器** | Worker crash / 未拉起 |
| `[TCP_CONNECT_RESET]` / `[TCP_NETWORK_UNREACHABLE]` | **OS** | 网络闪断（除非同窗 Worker 重启） |
| `[UDS_CONNECT_FAILED]` / `[SHM_FD_TRANSFER_FAILED]` | **DS** | 同机 Worker 未就绪 / UDS 配置 |
| `[RPC_RECV_TIMEOUT]` + ZMQ fault=0 | **DS** | 对端处理慢拖超时，非网络 |
| `[RPC_SERVICE_UNAVAILABLE]` | **DS** | 对端主动拒绝（状态 / shutting down） |
| `[ZMQ_SEND/RECV_FAILURE_TOTAL]`（`zmq_msg_send/recv` 硬失败） | **OS** | 按 `zmq_last_error_number` 对照 errno |
| `etcd is timeout` / `etcd is unavailable`（同屏 1002/25） | **三方 etcd** | DS 仅客户端；先修 etcd / 到 etcd 的网络 |
| `[SOCK_CONN_WAIT_TIMEOUT]` / `[REMOTE_SERVICE_WAIT_TIMEOUT]` | **OS 或 DS** | 握手迟；看对端存活再判 |
| `zmq_event_handshake_failure_total` ↑ | **DS** | TLS / 认证配置 |

**errno 速查**：`101=NETUNREACH`、`104=ECONNRESET`、`110=ETIMEDOUT`、`111=ECONNREFUSED`、`113=EHOSTUNREACH`、`11=EAGAIN`(背压非错)。

### 3.4 Step 3：交叉验证

- `ping / ss / iptables`：对端 IP 可达、端口 LISTEN → 排除 OS。
- 对端 Worker INFO log 同时间窗有无受理日志：有 → 对端活；无 → 再看 `[HealthCheck] Worker is exiting now`。
- `worker_object_count` / access log 计数断崖 → Worker 重启。

### 3.5 按边界定位

#### 3.5.1 用户

| respMsg 片段 | 含义 | 处置 |
|--------------|------|------|
| `The objectKey is empty` / `dataSize should be bigger than zero` / `length not match` | 参数非法 | 业务校验 |
| `ConnectOptions was not configured` | 未配置 Init | 检查 Init |
| `Client object is already sealed` | buffer 重复 Publish | 业务逻辑 |
| `OBJECT_KEYS_MAX_SIZE_LIMIT` | 批次超限 | 拆 batch |
| `Can't find object` / `K_NOT_FOUND` | 对象不存在 | 业务自查 key |

#### 3.5.2 DS 进程内

```
             §3.2/3.3 已落到 DS 进程内
                       │
      ┌────────┬───────┼───────┬────────┐
      ▼        ▼       ▼       ▼        ▼
   RPC 超时 / 网关      etcd /   心跳 /    mmap /
   服务不可用 重建 /    ETCD_*  扩缩容    SHM fd
   fault=0   disconn.   /25同屏 23/31/32  传递失败
      │        │        │        │        │
      ▼        ▼        ▼        ▼        ▼
     (a)      (b)      (c)      (d)      (e)
```

| 子场景 | 证据 | 处置 |
|--------|------|------|
| **(a) 对端处理慢 / 拒绝** | `[RPC_RECV_TIMEOUT]` / `[RPC_SERVICE_UNAVAILABLE]`；ZMQ fault=0；`*_OC_SERVICE_THREAD_POOL.WAITING_TASK_NUM` 堆积 | 查 Worker CPU / 锁；扩 `oc_rpc_thread_num` |
| **(b) 网关重建 / 连接抖动** | `zmq_gateway_recreate_total`↑；`zmq_event_disconnect_total`↑；对端 Worker 仍活 | 低频忽略；高频转 OS 查网络 |
| **(c) 三方 etcd** | Master `etcd is timeout`；Worker `etcd is unavailable`；`ETCD_QUEUE`↑ / 成功率↓；code=25 | **主责写三方**；`systemctl status etcd`；`etcdctl endpoint status`；查到 etcd 的网络 |
| **(d) 心跳 / 生命周期 / 扩缩容** | `Cannot receive heartbeat from worker.`(code=23)；`[HealthCheck] Worker is exiting now`；`meta_is_moving`(31/32) | 心跳断 → `kill -CONT <pid>`；退出由编排拉起；扩缩容 SDK 自重试 |
| **(e) SHM fd 传递** | `Get mmap entry failed`；同时 `[SHM_FD_TRANSFER_FAILED]` | 先试 OS：`ulimit -l unlimited`；仍现查 SCM_RIGHTS |

> (c) 实为三方 etcd，此处仅因信号出现在 DS 日志中一并列出；工单**主责写三方**。

#### 3.5.3 URMA

| 证据 | 含义 | 处置 |
|------|------|------|
| `[URMA_NEED_CONNECT]` 首次 + `instanceId` 变 | 对端重启 | 等 SDK 重连 |
| `[URMA_NEED_CONNECT]` 持续 + `instanceId` 不变 | UB 链路不稳 | 查 UB 端口 / 交换机 |
| `[URMA_RECREATE_JFS]` + `cqeStatus=9` | JFS 异常自动重建 | 观察是否成功 |
| `[URMA_RECREATE_JFS_FAILED]` 连续 | 重建失败 | 找 URMA 团队 |
| `[URMA_POLL_ERROR]` | 驱动 / 硬件 | 查 UMDK |
| `[URMA_WAIT_TIMEOUT]`(1010) | 等待 CQE 超时 | SDK 重试白名单 |
| code=1009 | URMA 建连失败 | 查 UB 端口 up/down |

> `fallback to TCP/IP payload` **非通断**，功能成功 → 走 §4.5.3 时延侧。

#### 3.5.4 OS

| 证据 | 判据 | 处置 |
|------|------|------|
| code=6 | `dmesg \| grep OOM`；`free -h` | 扩内存 / 调 cgroup |
| code=13 | `df -h`；`resource.log` `SPILL_HARD_DISK` | 清理 / 扩容 |
| code=18 | `ls /proc/<pid>/fd \| wc -l` vs `ulimit -n` | 调大 `ulimit -n` |
| code=5 + `Get mmap entry failed` | `ulimit -l` 太低 | `ulimit -l unlimited` |
| `[TCP_CONNECT_FAILED]` + 对端活 | `ss -tnlp`；`iptables -L -n` | 开端口 / 删规则 |
| `[ZMQ_*_FAILURE_TOTAL]` + `zmq_last_error_number` | 按 errno 对照 | 对应 OS 排查 |

---

## 四、时延定界（3 分钟）

### 4.1 定界流程

```
      接口日志 P99↑ / microseconds 变差（code 多为 0）
                       │
                       ▼
         ┌──────────────────────────┐
         │ Step1  delta 段确认劣化    │
         │ Histogram max；count=0 停 │
         └────────────┬─────────────┘
                      ▼
         ┌──────────────────────────┐
         │ Step2  client_rpc_*      │
         │   vs worker_process_*    │
         └────────────┬─────────────┘
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
   同幅飙升       Client 更慢     Worker 快/Client 慢
   → §4.5.2 (a-d) → Step3 拆链路   → 用户 / SDK 本地
                      │
                      ▼
         ┌──────────────────────────┐
         │ Step3  URMA/TCP/ZMQ IO   │
         │ +fault / RTT / 丢包      │
         └────────────┬─────────────┘
                      ▼
              主责边界 → §4.5
```

### 4.2 三步要点

**Step 1**：`grep 'Compare with' $LOG/*.INFO.log` 看 delta 段；对应 Histogram `max` 相对 baseline 飙升（如 2–3×）或 P99↑ → 确认真慢。`count=+0` 则无流量，非时延。

**Step 2**：Client vs Worker

| 对比 | 结论 |
|------|------|
| `client_rpc_*_latency` max 显著 > `worker_process_*_latency` max | 中间链路慢 → Step 3 |
| 两者同幅 max 飙升 | **DS 进程内** Worker 业务慢 |
| Worker 快 / Client 慢、`worker_to_client_total_bytes` 正常 | **用户 / SDK 本地**（反序列化、用户线程阻塞） |

**Step 3**：拆中间链路

- `worker_urma_write_latency` max↑ → **URMA**。
- `worker_tcp_write_latency` max↑ + `*_urma_*_bytes` delta=0 + `fallback to TCP/IP payload` → **URMA**（UB 降级）。
- `zmq_send/receive_io_latency` max↑ + `zmq_send/receive_failure_total` 有 delta → **OS**。
- `zmq_*_io_latency` max↑ + fault=0 → 再看**框架占比**：
  - `(serialize + deserialize) / (send_io + recv_io + serialize + deserialize)`
  - **< 5%** → 中间网络或对端处理（看 `worker_process_*` 是否同幅）
  - **≥ 5%** → **DS 进程内**框架（大 payload / protobuf）
- `ping RTT 抖` / `tc qdisc` netem / `nstat` 丢包 → **OS**。

### 4.3 用户时延

- 过大 batch（接近 `OBJECT_KEYS_MAX_SIZE_LIMIT`）→ 拆。
- SDK 调用线程被业务阻塞（metrics 仍滚动但 pstack 卡在 app 代码）。
- 客户端 GC / 同步 IO。

### 4.4 DS 进程内时延

```
            同幅飙升 / Worker 侧为主
                       │
     worker_rpc_get_remote_object_latency 单独突出？
         是 ───────────────────────────► (c) 跨 Worker Get
         否
                       │
     create_meta_latency↑ 且 ETCD_* 差？
         是 ───► **三方 etcd**（对照 §3.5.2(c)）
         否
                       │
     RPC 框架占比 ≥ 5%？
         是 ───────────────────────────► (b) 序列化 / 大 payload
         否
                       │
     zmq_send_try_again↑ 且 fault=0？
         是 ───────────────────────────► (d) HWM 背压
         否
                       ▼
                 (a) 业务路径 / 线程池 / 锁 / CPU
```

| 子场景 | 证据 | 处置 |
|--------|------|------|
| **(a) Worker 业务慢** | `worker_process_*_latency` max↑；`*_OC_SERVICE_THREAD_POOL.WAITING_TASK_NUM` 堆积 | 查 CPU / 锁；扩线程池 |
| **(b) 框架 / 大 payload** | `serialize+deserialize` 占比 ≥5%；`worker_to/from_client_total_bytes` 异常大 | 拆对象 / 启 URMA 零拷贝 / Publish+Get 走 SHM |
| **(c) 跨 Worker Get** | `worker_rpc_get_remote_object_latency` max↑ | 远端也慢 → 远端业务；否则转 §4.5.3/4.5.4 |
| **(d) ZMQ 背压** | `zmq_send_try_again_total`↑ 其它 fault=0 | 加消费线程 / 降峰限流；Status 仍为 0，以 max/P99 观测 |

### 4.5 URMA / OS 时延

| 边界 | 证据 | 处置 |
|------|------|------|
| **URMA** | `worker_urma_write_latency` max↑；`*_urma_*_bytes` delta=0 + TCP 字节正常（降级）；`fallback to TCP/IP payload` 频率>0；`[URMA_NEED_CONNECT]/[URMA_RECREATE_JFS]` 间歇 | 查 UMDK / UB 端口；`ibstat \|\| ubinfo \|\| ifconfig ub0` |
| **OS** | `ping` RTT 抖；`tc qdisc` netem 残留；`nstat/ss -ti` 重传↑；`iostat/vmstat` IO wait/swap；`resource.log` `SHARED_MEMORY` 接近 `TOTAL_LIMIT` | `tc qdisc del`；`dmesg`；扩资源 |

---

## 五、结论模板

```
【故障类型】通断 / 时延
【责任边界】用户 / DS 进程内 / 三方(etcd 等) / URMA / OS（必须给其一；跨界写"主责+协查"）
【错误码】<code> <枚举> + rc.GetMsg()（时延类写 "N/A, Status=K_OK"）
【日志证据】
  - <SDK 一行，含 TraceID / 时间 / objectKey>
  - <Worker 一行，同时间窗，带 [PREFIX]>
【Metrics delta】cycle=<N>, interval=<ms>ms
  <Counter>=+<N>；<Histogram> count=+<N>, max=<val>
【根因】一句话
【处置】对应 §3.5 / §4.4 动作
【闭环验证】X 分钟后哪些信号回 baseline
```

---

## 六、实战示例

### 通断 / DS：1002 + 对端 Worker 正常
```
Step1：code=1002 → Step2
Step2：[RPC_RECV_TIMEOUT]，ZMQ fault=0 → DS 对端处理慢
Step3：resource.log 对端 WORKER_OC_SERVICE_THREAD_POOL.WAITING_TASK_NUM=128
【边界】DS 进程内（线程池打满）；扩 oc_rpc_thread_num
```

### 通断 / OS：1002 + iptables 注入
```
Step2：[ZMQ_SEND_FAILURE_TOTAL] + zmq_last_error_number=113(EHOSTUNREACH)
Step3：iptables -L -n 有 DROP
【边界】OS；iptables -D ...；zmq_send_failure_total 归零即恢复
```

### 通断 / 三方：1002 + etcd 不可用
```
Step2：Master/Worker 同时打 "etcd is timeout" / "etcd is unavailable"
       resource.log ETCD_REQUEST_SUCCESS_RATE 断崖
【边界】三方(etcd)；修 etcd 集群或到 etcd 的网络
```

### 通断 / URMA：Put 返回 1009
```
Step1：code=1009 → URMA
Step3：[URMA_NEED_CONNECT] 高频 + [URMA_RECREATE_JFS_FAILED] 连续
       ifconfig ub0 DOWN
【边界】URMA；ifconfig ub0 up
```

### 时延 / URMA：Put avg 500us→3000us，code=0
```
Step1：client_rpc_publish_latency max 飙升
Step2：worker_process_publish_latency 同幅不成立
       client_put_urma_write_total_bytes delta=0，TCP 字节 +50MB
Step3：fallback to TCP/IP payload 200/min
【边界】URMA（UB 降级）；修 UMDK / ub0
```

### 时延 / OS：框架占比低、中间链路抖
```
Step1：client_rpc_get_latency max↑，worker_process_get_latency 未变
Step2：zmq_send_io_latency max↑；serialize+deserialize 占比 ~3%
Step3：ping RTT 抖动；tc qdisc 有 netem
【边界】OS；tc qdisc del ...
```

### 通断 / DS：SHM 钉住泄漏
```
无错误码；SHARED_MEMORY 3.58GB→37.5GB
worker_shm_ref_table_bytes↑，worker_object_count 持平
worker_allocator_alloc_bytes_total delta > free delta
【边界】DS 进程内（SHM ref 未释放）；查 client DecRef
```

---

## 七、速查卡

### 7.1 一纸禅 grep

```bash
LOG=${log_dir:-/var/log/datasystem}

# 结构化日志前缀（通断主抓手）
grep -E '\[(TCP|UDS|ZMQ|RPC|SOCK|REMOTE|SHM_FD|URMA)_' \
  $LOG/datasystem_worker.INFO.log $LOG/ds_client_*.INFO.log

# Metrics delta 段（时延必看）
grep 'Compare with' $LOG/datasystem_worker.INFO.log | tail -3

# URMA 降级 / Worker 退出 / 心跳 / etcd
grep -E 'fallback to TCP/IP payload' $LOG/*.INFO.log
grep -E '\[HealthCheck\] Worker is exiting now|Cannot receive heartbeat from worker' $LOG/*.INFO.log
grep -E 'etcd is (timeout|unavailable)' $LOG/*.INFO.log

# SHM 钉住
grep -E 'worker_shm_(ref_table|unit|ref_(add|remove))|worker_allocator_(alloc|free)_bytes_total' \
  $LOG/datasystem_worker.INFO.log

# resource.log 核心
grep -E 'SHARED_MEMORY|ETCD_QUEUE|ETCD_REQUEST_SUCCESS_RATE|OC_HIT_NUM|WAITING_TASK_NUM' $LOG/resource.log

# 错误码分布
grep 'DS_KV_CLIENT_GET' $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
```

### 7.2 现场命令

```bash
# OS
ulimit -a; free -h; df -h; dmesg | tail -200
ls /proc/<pid>/fd | wc -l; ss -tnlp | grep <port>
iptables -L -n; tc qdisc show dev eth0

# URMA
ibstat 2>/dev/null || ubinfo 2>/dev/null; ifconfig ub0; ls /dev/ub*

# DS 进程
pgrep -af datasystem_worker; pidstat -p <pid> 1; gstack <pid>

# etcd
systemctl status etcd; etcdctl endpoint status -w table
```

### 7.3 处置速查

| 故障 | 恢复 | 验证 |
|------|------|------|
| ZMQ 发送失败（iptables） | `iptables -D ...` | `zmq_send_failure_total` 归零 |
| RPC 超时（tc） | `tc qdisc del dev eth0 root netem` | latency 回 baseline |
| TCP 建连失败 | 开端口 / 删 iptables / 拉起 Worker | `[TCP_CONNECT_FAILED]` 消失 |
| UB 降级 | `ifconfig ub0 up`；修 UMDK | `client_*_urma_*_bytes` > 0 |
| 心跳超时（手工 STOP） | `kill -CONT <pid>` | 心跳恢复 |
| etcd 超时 | 修 etcd 集群 / 网络 | `etcd is ...` 消失 |
| mmap 失败 | `ulimit -l unlimited` | `Get mmap entry failed` 消失 |
| fd 耗尽 | `ulimit -n` 调大 | code=18 消失 |
| SHM 钉住 | 查 DecRef；必要时重启 Worker | `worker_shm_ref_table_bytes` 回稳 |

---

## 八、KV Metrics 全量（54 条）

与 `yuanrong-datasystem` 主干一致时 `KvMetricId = 0~53`（`KV_METRIC_END = 54`），定义在 `common/metrics/kv_metrics.{h,cpp}`。

| ID | 名 | 类型 | 单位 | | ID | 名 | 类型 | 单位 |
|---|----|------|------|-|---|----|------|------|
| 0 | `client_put_request_total` | Counter | count | | 27 | `zmq_network_error_total` | Counter | count |
| 1 | `client_put_error_total` | Counter | count | | 28 | `zmq_last_error_number` | Gauge | — |
| 2 | `client_get_request_total` | Counter | count | | 29 | `zmq_gateway_recreate_total` | Counter | count |
| 3 | `client_get_error_total` | Counter | count | | 30 | `zmq_event_disconnect_total` | Counter | count |
| 4 | `client_rpc_create_latency` | Histogram | us | | 31 | `zmq_event_handshake_failure_total` | Counter | count |
| 5 | `client_rpc_publish_latency` | Histogram | us | | 32 | `zmq_send_io_latency` | Histogram | us |
| 6 | `client_rpc_get_latency` | Histogram | us | | 33 | `zmq_receive_io_latency` | Histogram | us |
| 7 | `client_put_urma_write_total_bytes` | Counter | bytes | | 34 | `zmq_rpc_serialize_latency` | Histogram | us |
| 8 | `client_put_tcp_write_total_bytes` | Counter | bytes | | 35 | `zmq_rpc_deserialize_latency` | Histogram | us |
| 9 | `client_get_urma_read_total_bytes` | Counter | bytes | | 36 | `worker_allocator_alloc_bytes_total` | Counter | bytes |
| 10 | `client_get_tcp_read_total_bytes` | Counter | bytes | | 37 | `worker_allocator_free_bytes_total` | Counter | bytes |
| 11 | `worker_rpc_create_meta_latency` | Histogram | us | | 38 | `worker_shm_unit_created_total` | Counter | count |
| 12 | `worker_rpc_query_meta_latency` | Histogram | us | | 39 | `worker_shm_unit_destroyed_total` | Counter | count |
| 13 | `worker_rpc_get_remote_object_latency` | Histogram | us | | 40 | `worker_shm_ref_add_total` | Counter | count |
| 14 | `worker_process_create_latency` | Histogram | us | | 41 | `worker_shm_ref_remove_total` | Counter | count |
| 15 | `worker_process_publish_latency` | Histogram | us | | 42 | `worker_shm_ref_table_size` | Gauge | count |
| 16 | `worker_process_get_latency` | Histogram | us | | 43 | `worker_shm_ref_table_bytes` | Gauge | bytes |
| 17 | `worker_urma_write_latency` | Histogram | us | | 44 | `worker_remove_client_refs_total` | Counter | count |
| 18 | `worker_tcp_write_latency` | Histogram | us | | 45 | `worker_object_erase_total` | Counter | count |
| 19 | `worker_to_client_total_bytes` | Counter | bytes | | 46 | `master_object_meta_table_size` | Gauge | count |
| 20 | `worker_from_client_total_bytes` | Counter | bytes | | 47 | `master_ttl_pending_size` | Gauge | count |
| 21 | `worker_object_count` | Gauge | count | | 48 | `master_ttl_fire_total` | Counter | count |
| 22 | `worker_allocated_memory_size` | Gauge | bytes | | 49 | `master_ttl_delete_success_total` | Counter | count |
| 23 | `zmq_send_failure_total` | Counter | count | | 50 | `master_ttl_delete_failed_total` | Counter | count |
| 24 | `zmq_receive_failure_total` | Counter | count | | 51 | `master_ttl_retry_total` | Counter | count |
| 25 | `zmq_send_try_again_total` | Counter | count | | 52 | `client_async_release_queue_size` | Gauge | count |
| 26 | `zmq_receive_try_again_total` | Counter | count | | 53 | `client_dec_ref_skipped_total` | Counter | count |

---

## 附录 · 日志与开关

| 项 | 说明 |
|----|------|
| `$log_dir` | 由 `--log_dir` / 环境决定 |
| Worker 运行日志 | `datasystem_worker.INFO.log` |
| Client 运行日志 | `ds_client_<pid>.INFO.log` |
| Client access log | `ds_client_access_<pid>.log` |
| `resource.log` | 资源、线程池、`ETCD_*` 聚合 |
| `request_out.log` | Worker 访问 etcd / OBS 轨迹 |
| `log_monitor` / `log_monitor_interval_ms` | 默认 `true` / `10000` |
| `log_monitor_exporter` | 现场用 `harddisk` 落盘 |
| Metrics Summary | `Total:` + `Compare with ...ms before:`；时延看 Histogram max / P99 |
