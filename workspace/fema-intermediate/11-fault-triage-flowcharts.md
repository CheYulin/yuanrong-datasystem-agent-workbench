# 故障定位定界流程图 v2.0

> 三阶段故障排查流程：快速定界 → 识别关键问题 → 处理措施建议

---

## 阶段一：快速定界（30秒）

### 1.1 错误码 → 故障域速判定

```
收到业务失败报告
 │
 ▼
┌─────────────────────────────────────┐
│  返回StatusCode检查                  │
└─────────────────────────────────────┘
 │
 ├─ code=0 但业务失败 ──────────────→ respMsg是否NOT_FOUND陷阱?
 │                                         │
 │                                         ├─ 是 ─→ 用户层-A类
 │                                         └─ 否 ─→ 复盘请求流程
 │
 ├─ code=2/3/8 ────────────────────→ 用户层-A类
 │
 ├─ code=1001/1002/19 ─────────────→ OS层-B类
 │
 ├─ code=1004/1006/1008/1010 ─────→ URMA层-C类
 │
 ├─ code=23/31/32 ─────────────────→ 组件层-D类
 │
 └─ code=5/6/7/13/20/25 ───────────→ OS层-资源面
```

---

## 阶段二：识别关键问题（5分钟）

### 2.1 OS层-B类(控制面) 诊断流程

```
OS层-控制面故障
 │
 ▼
 ┌──────────────────────────────────────────────────┐
 │  检查结构化日志标签                                │
 │  [TCP_CONNECT_FAILED] / [TCP_CONNECT_RESET]      │
 │  [RPC_RECV_TIMEOUT] / [RPC_SERVICE_UNAVAILABLE]  │
 │  [ZMQ_SEND_FAILURE_TOTAL]                        │
 └──────────────────────────────────────────────────┘
 │
 ├─ [TCP_CONNECT_FAILED] ─────────────────→ 端口不可达/防火墙
 ├─ [TCP_CONNECT_RESET] ──────────────────→ 对端Crash/网络闪断
 ├─ [RPC_RECV_TIMEOUT] ───────────────────→ 网络拥塞/对端处理慢
 ├─ [ZMQ_SEND_FAILURE_TOTAL] ────────────→ ZMQ发送失败
 └─ [RPC_SERVICE_UNAVAILABLE] ────────────→ Worker进程退出

Metrics检查:
 ├─ zmq_send_failure_total ↑ ─────────────→ ZMQ发送失败
 ├─ client_rpc_get_latency max ↑ ────────→ RPC超时
 └─ ETCD_QUEUE ↑ ──────────────────────────→ etcd写入慢

恢复方法:
 ├─ 网络闪断 ────→ iptables规则撤销
 ├─ TCP建连失败 ─→ 检查端口/防火墙
 └─ RPC超时 ─────→ tc qdisc del dev eth0 root netem
```

### 2.2 URMA层-C类 诊断流程

```
URMA层故障
 │
 ▼
 ┌──────────────────────────────────────────────────┐
 │  检查URMA结构化日志                               │
 │  [URMA_NEED_CONNECT] / [URMA_RECREATE_JFS]        │
 │  [URMA_POLL_ERROR] / fallback to TCP             │
 └──────────────────────────────────────────────────┘
 │
 ├─ [URMA_NEED_CONNECT] ─────────────────→ 连接需重建
 │    原因: 连接不存在/实例不匹配/不稳定
 │    恢复: SDK自动重连 → K_TRY_AGAIN
 │
 ├─ [URMA_RECREATE_JFS] ────────────────→ JFS重建
 │    原因: cqeStatus=9触发重建
 │    恢复: 自动重建JFS
 │
 ├─ fallback to TCP ────────────────────→ UB降级TCP
 │    原因: UB payload超限/设备故障/Jetty不足
 │    恢复: ifconfig ub0 up
 │
 └─ [URMA_POLL_ERROR] ───────────────────→ CQ poll失败
      恢复: 重建CQ

Metrics检查:
 ├─ tcp_read_total_bytes ↑ ─────────────→ UB降级TCP
 ├─ urma_read_total_bytes = 0 ─────────→ UB降级TCP
 └─ worker_urma_write_latency max ↑ ─────→ JFS异常
```

### 2.3 组件层-D类 诊断流程

```
组件层故障
 │
 ▼
 ┌──────────────────────────────────────────────────┐
 │  检查HealthCheck标签                               │
 │  [HealthCheck] Worker is exiting now              │
 │  Cannot receive heartbeat from worker            │
 │  etcd is timeout / Disconnected from remote node  │
 │  meta_is_moving = true                           │
 └──────────────────────────────────────────────────┘
 │
 ├─ [HealthCheck] Worker is exiting ──────→ Worker退出
 │    恢复: k8s自动拉起
 │
 ├─ Cannot receive heartbeat ────────────→ 心跳超时
 │    原因: kill -STOP Worker / 进程挂死
 │    恢复: kill -CONT <worker_pid>
 │
 ├─ etcd is timeout ──────────────────────→ etcd超时
 │    恢复: systemctl start etcd
 │
 ├─ Disconnected from remote node ────────→ 节点断开
 │    原因: Master超时
 │    恢复: 检查etcd和网络
 │
 └─ meta_is_moving = true ───────────────→ 扩缩容中
      处理: K_SCALING正常，SDK自动重试

Metrics检查:
 ├─ worker_object_count ↓ ──────────────→ Worker退出
 ├─ SHARED_MEMORY ↑ ───────────────────→ 内存异常/SHM泄漏
 └─ ETCD_REQUEST_SUCCESS_RATE ↓ ────────→ etcd问题
```

### 2.4 OS层-资源面 诊断流程

```
OS层-资源面故障
 │
 ▼
 ┌──────────────────────────────────────────────────┐
 │  检查错误码和日志                                 │
 │  K_OUT_OF_MEMORY(6) / K_RUNTIME_ERROR(5/7)       │
 │  K_NO_SPACE(13) / K_FILE_LIMIT_REACHED(20)      │
 │  K_MASTER_TIMEOUT(25)                            │
 └──────────────────────────────────────────────────┘
 │
 ├─ Get mmap entry failed ───────────────→ mmap申请失败
 │    原因: ulimit -l 0 / fd超限
 │    恢复: ulimit -l unlimited
 │
 ├─ K_OUT_OF_MEMORY ────────────────────→ 内存/shm池不足
 │    恢复: 等待内存释放/触发缓存淘汰
 │
 ├─ K_NO_SPACE ──────────────────────────→ 磁盘空间满
 │    恢复: 清理磁盘/扩容
 │
 ├─ K_FILE_LIMIT_REACHED ────────────────→ fd资源耗尽
 │    恢复: 检查fd使用情况
 │
 └─ K_MASTER_TIMEOUT ────────────────────→ etcd不可用
      恢复: 检查etcd集群状态
```

---

## 阶段三：处理措施建议

### 3.1 按故障域推荐处理

| 故障域 | 日志检查 | Metrics检查 | 处理建议 |
|-------|---------|------------|---------|
| **用户层-A** | respMsg关键字 | 无特殊 | 检查业务参数/Init顺序 |
| **OS层-B** | ZMQ/TCP/RPC标签 | zmq_send_failure_total | 检查网络状态 |
| **URMA层-C** | URMA标签 | UB/TCP bytes | 检查UB设备 |
| **组件层-D** | HealthCheck标签 | worker_object_count | 检查Worker状态 |
| **OS层-资源** | etcd/mmap标签 | ETCD_QUEUE | 检查系统资源 |

### 3.2 故障恢复措施速查表

| 故障类型 | 恢复命令 | 验证方法 |
|---------|---------|---------|
| ZMQ发送失败 | `iptables -D OUTPUT -p tcp --dport X -j DROP` | `zmq_send_failure_total` delta=0 |
| RPC超时 | `tc qdisc del dev eth0 root netem` | latency恢复到baseline |
| TCP建连失败 | `iptables -D INPUT -p tcp --dport X -j REJECT` | `[TCP_CONNECT_FAILED]`消失 |
| URMA需重连 | SDK自动重连(等待`K_TRY_AGAIN`) | `[URMA_NEED_CONNECT]`消失 |
| UB降级TCP | `ifconfig ub0 up` | `urma_read_total_bytes`恢复>0 |
| Worker退出 | k8s自动拉起/手动重启 | 新Worker PID获取 |
| 心跳超时 | `kill -CONT <worker_pid>` | 心跳恢复 |
| etcd超时 | `systemctl start etcd` | `etcd is timeout`消失 |
| mmap失败 | `ulimit -l unlimited` | `Get mmap entry failed`消失 |

### 3.3 自证清白验证流程

```
┌─────────────────────────────────────────┐
│ 自证清白公式                             │
│ RPC框架占比 = (ser + deser) /            │
│              (send + recv + ser + deser) │
└─────────────────────────────────────────┘
 │
 ▼
┌─────────────────┐
│ 框架占比 > 20%? │──→ Yes ─→ 瓶颈在序列化
└─────────────────┘
 │No
 ▼
┌─────────────────┐
│ I/O占比 > 80%?  │──→ Yes ─→ 瓶颈在网络
└─────────────────┘
 │No
 ▼
┌─────────────────┐
│ 全部都低?       │──→ Yes ─→ 瓶颈不在RPC栈
└─────────────────┘
```

---

## 附录：常用grep命令速挂

### Worker日志 grep
```bash
# 查URMA标签
grep -E "\[URMA_" /logs/datasystem_worker.INFO.log

# 查TCP/ZMQ/RPC标签
grep -E "\[(TCP|ZMQ|RPC|SOCK)_" /logs/datasystem_worker.INFO.log

# 查Metrics delta
grep "Compare with" /logs/datasystem_worker.INFO.log | tail -3

# 查降级
grep "fallback to TCP" /logs/datasystem_worker.INFO.log

# 查Worker退出
grep "HealthCheck.*exiting" /logs/datasystem_worker.INFO.log

# 查etcd问题
grep "etcd is timeout" /logs/datasystem_worker.INFO.log

# 查SHM泄漏
grep "worker_shm_ref_table" /logs/datasystem_worker.INFO.log
```

### Client日志 grep
```bash
# 查错误码分布
grep "DS_KV_CLIENT_GET" /path/client/ds_client_access_{pid}.log | awk -F'|' '{print $1}' | sort | uniq -c

# 查INVALID类错误
grep "^2 |" /path/client/ds_client_access_{pid}.log

# 查NOT_FOUND
grep "K_NOT_FOUND" /path/client/ds_client_access_{pid}.log
```

### resource.log grep
```bash
# 查资源指标
grep "SHARED_MEMORY\|ETCD_QUEUE\|OC_HIT_NUM" /logs/resource.log

# 查线程池
grep "SERVICE_THREAD_POOL" /logs/resource.log
```
