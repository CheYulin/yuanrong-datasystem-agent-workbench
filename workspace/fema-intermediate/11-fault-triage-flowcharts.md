# 故障定位定界流程图 v2.0

> 三阶段故障排查流程：快速定界 → 识别关键问题 → 处理措施建议

---

## 阶段一：快速定界（30秒）

### 1.1 错误码 → 故障域速判定

```mermaid
flowchart TD
    START["收到业务失败报告"] --> CHECK_CODE{"返回StatusCode?"}

    CHECK_CODE -->|code=0 但业务失败| RESPCASE["respMsg是否NOT_FOUND陷阱"]
    RESPCASE -->|是| USER_A["用户层-A类"]
    RESPCASE -->|否| CHECK_AGAIN["复盘请求流程"]

    CHECK_CODE -->|code=2/3/8| USER_B["用户层-A类\n参数/NotFound/未Init"]

    CHECK_CODE -->|code=1001/1002/19| OS_B["OS层-B类\nRPC超时/不可达/TryAgain"]

    CHECK_CODE -->|code=1004/1006/1008/1010| URMA_C["URMA层-C类\nUB设备/连接/CQ/NeedConnect"]

    CHECK_CODE -->|code=23/31/32| COMP_D["组件层-D类\n心跳断/Worker退出/扩缩容"]

    CHECK_CODE -->|code=5/6/7/13/20/25| OS_E["OS层-E类\n内存/磁盘/IO/etcd"]

    CHECK_CODE -->|code=2000/2003/2004| USER_F["用户层-F类\n业务冲突/队列满"]
```

### 1.2 Access Log + 错误码 快速分流

```mermaid
flowchart LR
    subgraph ACCESS["Access Log分析"]
        A1["code | handleName | microseconds | dataSize | reqMsg | respMsg"]
    end

    subgraph DECISION["快速分流"]
        D1{"code == 0?"}
        D2{"respMsg Contains 'NOT_FOUND'?"}
        D3{"code ∈ 1001/1002?"}
        D4{"code ∈ 1004/1006?"}
        D5{"code ∈ 23/31/32?"}
        D6{"code ∈ 5/6/7/13/20/25?"}
    end

    subgraph RESULT["故障域归属"]
        R1["用户层 - A类"]
        R2["OS层 - B类 网络/RPC"]
        R3["URMA层 - C类"]
        R4["组件层 - D类"]
        R5["OS层 - E类 资源"]
    end

    A1 --> D1
    D1 -->|Yes| D2
    D1 -->|No| D3
    D2 -->|Yes| R1
    D3 -->|Yes| R2
    D4 -->|Yes| R3
    D5 -->|Yes| R4
    D6 -->|Yes| R5
```

---

## 阶段二：识别关键问题（5分钟）

### 2.1 OS层-B类 诊断流程

```mermaid
flowchart TD
    START_B["OS层网络/RPC故障"] --> ZMQ_CHECK{"ZMQ标签?"}

    ZMQ_CHECK -->|ZMQ_SEND_FAILURE_TOTAL| ZMQ_SEND["ZMQ发送失败\niptables drop / 网络闪断"]
    ZMQ_CHECK -->|ZMQ_RECV_FAILURE_TOTAL| ZMQ_RECV["ZMQ接收失败\n对端不可达"]

    ZMQ_CHECK -->|TCP_CONNECT_FAILED| TCP_CONN["TCP建连失败\n端口不可达/防火墙"]
    ZMQ_CHECK -->|TCP_CONNECT_RESET| TCP_RST["连接被重置\n对端Crash"]

    ZMQ_CHECK -->|RPC_RECV_TIMEOUT| RPC_TIMEOUT["RPC超时\ntc delay + timeout配置"]
    ZMQ_CHECK -->|RPC_SERVICE_UNAVAILABLE| RPC_UNAVAIL["服务不可达\nWorker进程退出"]

    START_B --> METRICS_B["检查Metrics delta"]
    METRICS_B --> M1{"zmq_send_failure_total ↑?"}
    METRICS_B --> M2{"zmq_gateway_recreate_total ↑?"}
    METRICS_B --> M3{"client_rpc_get_latency max ↑?"}

    M1 -->|Yes| ZMQ_SEND
    M2 -->|Yes| RPC_UNAVAIL
    M3 -->|Yes| RPC_TIMEOUT

    ZMQ_SEND --> RECOV_B1["恢复: iptables -D ..."]
    TCP_CONN --> RECOV_B2["恢复: 检查端口/防火墙"]
    RPC_TIMEOUT --> RECOV_B3["恢复: tc qdisc del"]
```

### 2.2 URMA层-C类 诊断流程

```mermaid
flowchart TD
    START_C["URMA层故障"] --> URMA_CHECK{"URMA标签?"}

    URMA_CHECK -->|URMA_NEED_CONNECT| URMA_CONN["URMA连接需重建\nkill远端Worker/实例变更"]
    URMA_CHECK -->|URMA_RECREATE_JFS| URMA_JFS["JFS重建\ncqeStatus=9"]
    URMA_CHECK -->|URMA_POLL_ERROR| URMA_POLL["CQ poll失败"]
    URMA_CHECK -->|fallback to TCP| UB_DOWN["UB降级TCP\nifconfig ub0 down"]

    START_C --> METRICS_C["检查UB/TCP bytes"]
    METRICS_C --> M1{"tcp_read_total_bytes ↑?"}
    METRICS_C --> M2{"urma_read_total_bytes = 0?"}
    METRICS_C --> M3{"worker_urma_write_latency max ↑?"}

    M1 -->|Yes| UB_DOWN
    M2 -->|Yes| UB_DOWN
    M3 -->|Yes| URMA_JFS

    URMA_CONN --> RECOV_C1["恢复: SDK自动重连"]
    UB_DOWN --> RECOV_C2["恢复: ifconfig ub0 up"]
    URMA_JFS --> RECOV_C3["恢复: 自动重建JFS"]
```

### 2.3 组件层-D类 诊断流程

```mermaid
flowchart TD
    START_D["组件层故障"] --> HEALTH_CHECK{"HealthCheck标签?"}

    HEALTH_CHECK -->|Worker is exiting| WORKER_EXIT["Worker退出\nkill -9 Worker"]
    HEALTH_CHECK -->|Cannot receive heartbeat| HEARTBEAT_TMO["心跳超时\nkill -STOP Worker"]

    HEALTH_CHECK -->|etcd is timeout| ETCD_TMO["etcd超时\nsystemctl stop etcd"]
    HEALTH_CHECK -->|Disconnected from remote node| ETCD_DISC["节点断开\nMaster超时"]

    HEALTH_CHECK -->|meta_is_moving = true| SCALING["扩缩容中\nK_SCALING 正常处理"]

    START_D --> METRICS_D["检查Worker Metrics"]
    METRICS_D --> M1{"worker_object_count ↓?"}
    METRICS_D --> M2{"worker_heartbeat_timeout_total ↑?"}
    METRICS_D --> M3{"ETCD_REQUEST_SUCCESS_RATE ↓?"}

    M1 -->|Yes| WORKER_EXIT
    M2 -->|Yes| HEARTBEAT_TMO
    M3 -->|Yes| ETCD_TMO

    WORKER_EXIT --> RECOV_D1["恢复: k8s自动拉起"]
    HEARTBEAT_TMO --> RECOV_D2["恢复: kill -CONT"]
    ETCD_TMO --> RECOV_D3["恢复: systemctl start etcd"]
```

### 2.4 SHM内存泄漏-PR#652 诊断流程

```mermaid
flowchart TD
    START_SHM["疑似SHM内存泄漏"] --> SHM_CHECK{"症状?"}

    SHM_CHECK -->|shm.memUsage暴涨| SHM_MEM["3.58GB → 37.5GB\n100s内增长10x"]
    SHM_CHECK -->|OBJECT_COUNT反常| SHM_COUNT["OBJECT_COUNT: 438→37\n反向于OBJECT_SIZE"]

    SHM_CHECK -->|Metrics验证| SHM_METRICS{"worker_shm_alloc_total\n> worker_shm_free_total?"}

    SHM_CHECK -->|ref_table钉住| SHM_REF{"worker_shm_ref_table_bytes\n持续涨"}

    SHM_MEM --> DIAG_SHM1["1. 检查resource.log\nSHARED_MEMORY使用率"]
    SHM_COUNT --> DIAG_SHM2["2. 检查OBJECT_COUNT vs SIZE\n变化方向是否相反"]
    SHM_REF --> DIAG_SHM3["3. 检查master_ttl_pending\n是否堆积"]

    SHM_METRICS -->|是| CONFIRM_LEAK["确认内存泄漏"]
    SHM_METRICS -->|否| CHECK_OTHER["排查其他原因"]

    CONFIRM_LEAK --> CAUSE["元数据已删但物理shm\n仍被memoryRefTable_钉住"]

    CAUSE --> RECOV_SHM["恢复:\n等待TTL回收/异步释放"]
```

---

## 阶段三：处理措施建议

### 3.1 按故障域推荐处理

```mermaid
flowchart TD
    subgraph USER["用户层-A类"]
        U1["检查业务参数"]
        U2["检查Init顺序"]
        U3["检查对象Key合法性"]
    end

    subgraph OS_NET["OS层-B类 网络/RPC"]
        O1["grep ZMQ/TCP/RPC标签"]
        O2["检查zmq_send_failure_total"]
        O3["检查iptables/tc配置"]
    end

    subgraph URMA["URMA层-C类"]
        R1["grep URMA_标签"]
        R2["检查UB/TCP bytes"]
        R3["检查ifconfig ub0状态"]
    end

    subgraph COMP["组件层-D类"]
        C1["grep HealthCheck标签"]
        C2["检查worker心跳"]
        C3["检查etcd状态"]
    end

    subgraph SHM["SHM泄漏-PR652"]
        S1["检查worker_shm_metrics"]
        S2["检查ref_table钉住"]
        S3["观察TTL pending堆积"]
    end
```

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
| SHM泄漏 | 等待TTL回收/异步释放 | Metrics恢复到baseline |

### 3.3 自证清白验证流程

```mermaid
flowchart LR
    subgraph FORMULA["自证清白公式"]
        F1["RPC框架占比 = (ser + deser) / (send + recv + ser + deser)"]
    end

    subgraph ANALYSIS["瓶颈分析"]
        A1{"框架占比 > 20%?"}
        A2{"I/O占比 > 80%?"}
        A3{"全部都低?"}
    end

    subgraph CONCLUSION["结论"]
        C1["瓶颈在序列化"]
        C2["瓶颈在网络"]
        C3["瓶颈不在RPC栈"]
    end

    F1 --> A1
    A1 -->|Yes| C1
    A1 -->|No| A2
    A2 -->|Yes| C2
    A2 -->|No| A3
    A3 --> C3
```

---

## 附录：常用grep命令速挂

```bash
# 第一步：快速归类
grep -E "\[(TCP|ZMQ|RPC|SOCK|URMA)_[A-Z_]+\]" worker.log | head -20

# 第二步：查Metrics delta
grep "Compare with" worker.log | tail -3

# 第三步：按域查
grep "\[URMA_NEED_CONNECT\]" worker.log   # URMA连接问题
grep "\[ZMQ_SEND_FAILURE_TOTAL\]" worker.log  # ZMQ发送失败
grep "fallback to TCP" worker.log           # UB降级
grep "HealthCheck.*exiting" worker.log     # Worker退出
grep "etcd is timeout" worker.log          # etcd超时
grep "worker_shm_ref_table" worker.log     # SHM泄漏
```
