# KV Client FEMA：关键读写路径与可靠性设计

> **系统可靠性方案**（通信/组件/数据/etcd）与 triage 口径对齐：[`FAULT_HANDLING_AND_DATA_RELIABILITY.md`](../../plans/kv_client_triage/FAULT_HANDLING_AND_DATA_RELIABILITY.md)（**第五节**与 triage、Case 对齐）。入口：[00-kv-client-fema-index.md](00-kv-client-fema-index.md)。

**配图（PlantUML）**：

- **读写时序 / 拓扑**：[`../flows/sequences/kv-client/`](../flows/sequences/kv-client/)（正常 / 切流时序，与下表步骤一致）
- **故障处理方案**：[`diagrams/kv-client/`](diagrams/kv-client/)（UB/TCP、etcd、SDK 切流等）

**客户导读**：[`KV_CLIENT_CUSTOMER_ALLINONE.md`](../../plans/kv_client_triage/KV_CLIENT_CUSTOMER_ALLINONE.md) **§1.3 关键读写路径**。

---

## 关键读写路径

**PlantUML 时序图**（与下表步骤一致）：

- 正常：[`kv_client_read_path_normal_sequence.puml`](../flows/sequences/kv-client/kv_client_read_path_normal_sequence.puml)
- 切流：[`kv_client_read_path_switch_worker_sequence.puml`](../flows/sequences/kv-client/kv_client_read_path_switch_worker_sequence.puml)

### 正常读写 Case

| 步骤  | 路径说明                                                               |
| --- | ------------------------------------------------------------------ |
| 1   | client → worker1（本机 TCP），不涉及 TCP 网卡                                |
| 2   | worker1 → worker2（跨机 TCP，元数据访问）                                    |
| 3   | worker1 → worker3（跨机 TCP，触发数据拉取）                                   |
| 4   | worker3 → worker1（跨机 URMA write）                                   |
| 5   | worker3 → worker1（跨机 TCP，get resp）— timeout 时可携带 worker3 的 IP、Port |
| 6   | worker1 → client（本地共享内存，返回偏移）                                      |

### Client 故障 / SDK 切流异常读写 Case

| 步骤  | 路径说明                                                       |
| --- | ---------------------------------------------------------- |
| 1   | client → worker2（跨机 TCP）                                   |
| 2   | worker2 → worker3（跨机 TCP，元数据访问）                            |
| 3   | worker2 → worker4（跨机 TCP，触发数据拉取）                           |
| 4   | worker4 → worker2（跨机 URMA write）                           |
| 5   | worker4 → worker2（跨机 TCP，get resp）— timeout 时可携带对端 IP、Port |
| 6   | worker2 → client（本地共享内存，返回偏移）                              |

> 说明：切流后 client 所连 worker 变化，跨机跳数与超时定界需结合当前连接的 Worker 与内部链路的监控。

---

## 可靠性设计

### 通信故障处理方案

| 故障大类    | 链路/子类     | 场景简述                  | 故障处理机制                                                                                            | 业务影响（示例）                                                       |
| ------- | --------- | --------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| 通信链路故障  | TCP 链路    | TCP 单口故障（端口切换约 100ms） | 请求超时内 RPC 重试；客户 timeout=20ms 时约有 1～2 次超时重试机会；TCP 重试间隔 1、5、50、200ms…5s；最小 RPC 时间预算 2ms，剩余时间不足则读写退出 | 切换窗口内读写报错，含约 1/n 元数据失败等；成功率、P99 劣化；切换完成后恢复                     |
| 通信链路故障  | TCP 链路    | TCP 网络抖动              | 自动失败重试后成功；时延增加 RPC 重试时间（约 0.2ms）+ 等待重试（约 1～6ms）                                                   | 时延劣化                                                           |
| 通信链路故障  | TCP 链路    | TCP 两口故障、交换机等         | TCP 超时失败；端口恢复后业务恢复                                                                                | 故障期失败；恢复后正常                                                    |
| UB 链路故障 | UB        | 单 UB 平面故障             | 故障检测后未恢复前可走 TCP 回退；未检测到时随用户超时（如 20ms）报错，硬件侧感知约 128ms                                              | 非 Jetty 类：约 133ms 内检测与平面切换；切换瞬间请求可能失败；切换后带宽可能减半；平面恢复后 URMA 可自愈 |
| UB 链路故障 | Jetty 不可用 | 拥塞、闪断、双 UB 平面故障等      | 过程中异常可 20ms 超时报错；Jetty 不可用则重连；无可用 Jetty 时可切 TCP（例：大 payload 时延与 TCP 相当）；平面恢复后回切                   | 短时超时、建链；TCP 降级时延上升                                             |

### 整体可靠性方案

| 故障大类       | 细化分类                | 故障处理机制                                            | 业务影响（示例）                                                            |
| ---------- | ------------------- | ------------------------------------------------- | ------------------------------------------------------------------- |
| 系统亚健康（高负载） | CPU、UB、HCCS、内存带宽占用高 | （依部署与调优）                                          | （依业务）                                                               |
| 系统资源故障     | 内存故障                | 进程 Crash，组件故障隔离                                   | SDK 同组件切流；KVC Worker Crash 隔离，影响同 Worker 上组件                        |
| 组件、节点故障    | KVC SDK 故障          | 自动清理 SDK 引用共享内存                                   | 不影响其他业务实例；SHM 资源释放                                                  |
| 组件、节点故障    | KVC Worker 故障       | etcd 心跳超时（约 2s）→ 隔离与元数据重分布；SDK 心跳检测切流（约 2s），恢复后回切 | 检测隔离窗口内读写失败；切流后恢复；时延可能增加（如 UB 约 1ms）；二级存储足够时可恢复数据，否则丢弃；大规模恢复耗时与带宽相关 |
| 组件、节点故障    | 异步持久化与恢复            | 分片迁移后预加载                                          | 恢复过程中访问可能失败；恢复完成后数据可访问性依容量与策略                                       |
| 第三方组件故障    | etcd 集群故障           | KVC 降级；数据面读写删除通常不受影响；与 etcd 重连直至恢复                | 服务发现、扩缩容、故障隔离等能力依赖 etcd 恢复                                          |
| 二级存储故障     | 持久化/加载失败            | 当前策略有限                                            | 可靠性风险；可用性可能仍维持；恢复后持久化功能可恢复                                          |
