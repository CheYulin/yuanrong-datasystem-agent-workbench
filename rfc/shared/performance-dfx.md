# 性能 DFX 设计

> 基于 repo_context 代码分析 + 现网 metrics 数据 + Jetty 代码深读

---

## 一、已有基础设施 (可复用)

### 1.1 PLOG 慢请求检测 (已有)

```
PLOG 宏: Get 路径自动检测慢请求，绕开 log_rate_limit 采样
阈值: Get本地处理>1000us, QueryMeta>1000us, URMA>1000us
规划中: 16段 LatencyTick 数组记录 Get 各阶段延迟, Client侧>2ms 触发打印
```

### 1.2 URMA 耗时诊断 (已有)

```
[URMA_ELAPSED_TOTAL]       JFC 等待 > 1ms
[URMA_ELAPSED_THREAD_SHED] nanosleep 唤醒 > 100us  → OS 调度
[URMA_ELAPSED_POLL_JFC]     urma_poll_jfc > 100us → URMA 层
[URMA_ELAPSED_NOTIFY]       notify 唤醒 > 1ms     → OS 调度
```

### 1.3 文件式可观测性 (已有)

| 信号 | 路径 | 采集频率 |
|------|------|:--:|
| Readiness | `{ready_check_path}` | 启动后一次 |
| Liveness | `{liveness_check_path}` | WorkerLivenessCheck 周期 |
| Access Log | `access.log`, `request_out.log` | 每次 RPC |
| Resource Monitor | `resource*.log` | 10s 周期 |
| Crash Log | `container.log` | 崩溃时 |

### 1.4 信号 → 症状路由表 (已有)

```
8 类症状 → 对应信号 → 关键配置 → 代码入口
startup failure → readiness/liveness/ordinary logs → FLAGS_ready_check_path
latency regression → access logs/resource monitor → PLOG threshold
blank metric → missing handler → FLAGS_log_monitor
```

---

## 二、已知差距 (需补齐)

| 差距 | 影响 | 优先级 |
|------|------|:--:|
| 无 HTTP /metrics 端点 | 无法对接 Prometheus/Grafana | P1 |
| 无请求级 P50/P99 时延分桶 | 无法看到操作维度时延分布 | P0 |
| 无连接池 metrics | 无法观察复用率/命中率 | P1 |
| 无副本健康度 metrics | 无法判断副本同步状态 | P0 |
| 无 RPO/RTO 指标 | 无法量化可靠性 | P1 |
| 无 Circuit Breaker | 故障连锁传播 | P2 |

---

## 三、新增 Metrics 设计

### 3.1 连接池 (RFC4)

```cpp
// 复用率是核心 KPI — 直接决定池化是否有价值
ZMQ_CONN_POOL_SIZE        GAUGE    // 当前池大小
ZMQ_CONN_POOL_HIT_RATE    GAUGE    // 复用命中率 (0-1), 告警阈值 < 0.5
ZMQ_CONN_POOL_ACQUIRE_US  HISTOGRAM // Acquire() 耗时 P50/P99
ZMQ_CONN_HEALTH_FAILURE   COUNTER  // 健康检查失败次数

URMA_QP_POOL_SIZE         GAUGE    // 当前 QP 池大小
URMA_QP_POOL_HIT_RATE     GAUGE    // 复用命中率
URMA_QP_CREATE_US         HISTOGRAM // QP 创建耗时
URMA_QP_DELETE_TOTAL      COUNTER  // QP 删除次数

JETTY_COUNT               GAUGE    // Jetty 总数
JETTY_CTP_ALLOCATED       GAUGE    // 已分配 CTP 数
JETTY_LOAD_MAX            GAUGE    // 最忙 Jetty 负载 (0-1)
JETTY_LOAD_AVG            GAUGE    // 平均负载
JETTY_RECREATE_TOTAL      COUNTER  // Jetty 重建次数
```

### 3.2 多副本 (RFC2+3)

```cpp
REPLICA_SYNC_TOTAL        COUNTER  // SyncReplicate 总次数
REPLICA_SYNC_FAIL_TOTAL   COUNTER  // 同步失败次数
REPLICA_SYNC_LATENCY_US   HISTOGRAM // 同步延迟 (per Backup)
REPLICA_QUORUM_FAIL_TOTAL COUNTER  // Quorum 未达成次数
REPLICA_STALE_TOTAL       COUNTER  // 副本落后次数
REPLICA_RECOVER_TOTAL     COUNTER  // 副本恢复次数
REPLICA_PROMOTE_TOTAL     COUNTER  // Promote 次数
REPLICA_PROMOTE_LATENCY_US HISTOGRAM // Promote 耗时
REPLICA_HEALTH_SCORE      GAUGE    // 副本健康度 (0-100)
```

### 3.3 Snapshot (RFC1)

```cpp
SNAPSHOT_CREATE_US        HISTOGRAM // Snapshot 创建耗时 P50/P99
SNAPSHOT_RESTORE_US       HISTOGRAM // 恢复耗时
SNAPSHOT_SIZE_BYTES       GAUGE    // 文件大小
SNAPSHOT_CRC_FAIL_TOTAL   COUNTER  // CRC 校验失败
SNAPSHOT_SKIP_TOTAL       COUNTER  // 因磁盘满/写失败跳过
```

---

## 四、性能基线与时延预算

### 4.1 写入路径

```
                         P50       P99       P99.9    Budget
Client→Primary(RPC)      100us     300us     500us    25%
Primary→Backup(URMA×2)   21us      50us      100us    5%
Primary→MetaOwner(RPC)   async     async     async    (不阻塞)
───────────────────────────────────────────────────────────────
总计                     121us     350us     600us    30% of 2ms
```

### 4.2 读取路径

```
                         P50       P99       P99.9    Budget
Client→MetaOwner         (cached)  (cached)  300us    15%
MetaOwner lookup         1us       10us      50us     2%
Client→Primary(URMA)     50us      200us     400us    20%
Primary READ SHM         5us       20us      40us     2%
───────────────────────────────────────────────────────────────
总计                     56us      230us     790us    40% of 2ms
```

### 4.3 故障切换

```
故障检测 (Lease 软超时)    3s              (可接受, <0.01%概率)
Promote + Client Refresh  1ms             (P99.99 < 5ms)
Meta Backup接管           0 (无额外延迟)   (已有完整副本)
```

### 4.4 Jetty 连接池影响

基于 Jetty 代码分析:
- Jetty 创建: 一次 `ds_urma_create_jetty` 调用, ~50ms (含 JFR 分配 + 注册)
- Jetty 复用命中: 池命中时 = 0 创建开销
- Jetty 重建: `ReCreateJetty` 异步, 不阻塞关键路径
- Jetty 数量: 从 O(N²)=1M 降到 **O(N×K)=128** (pool_size=8)

---

## 五、慢请求诊断增强

### 5.1 当前 PLOG 阈值 (可复用)

```
Get 本地处理          > 1000us
QueryMeta RPC total   > 1000us
Master QueryMeta      > 2000us
Remote worker RPC     > 2000us
URMA total           > 1000us
```

### 5.2 新增 PLOG 阈值

```
SyncReplicate total   > 100us    // Backup 同步慢
Replica Promote       > 1000us   // 故障切换慢
Snapshot Create       > 1000us   // Checkpoint 慢
Pool Acquire          > 1000us   // 连接池耗尽
Jetty ReCreate        > 100ms    // Jetty 重建慢
```

### 5.3 Get 时延细分 (规划)

```
Client → MetaOwner:     recorded as Tick[0]
MetaOwner lookup:       recorded as Tick[1]
Client → Data Primary:  recorded as Tick[2]
Primary RLock + Read:   recorded as Tick[3]
URMA pull (if remote):  recorded as Tick[4]
Response to Client:     sum of Ticks
```

---

## 六、告警规则

| 告警 | 条件 | 严重度 | 动作 |
|------|------|:--:|------|
| 副本同步失败率高 | `REPLICA_SYNC_FAIL / TOTAL > 1%` | P1 | 检查网络/URMA |
| 连接池命中率低 | `POOL_HIT_RATE < 0.5` | P2 | 增加 pool_size |
| Quorum 失败 | `QUORUM_FAIL_TOTAL > 0` (5min) | P0 | 检查集群健康 |
| Jetty 重建频繁 | `JETTY_RECREATE_TOTAL > 10/min` | P1 | 检查 RDMA 链路 |
| Snapshot 连续失败 | `SNAPSHOT_SKIP_TOTAL > 3` | P1 | 检查磁盘 |
