# 压缩上下文：Object Client 故障切换、负载热点与 UB 负载口径

- **Status**: Draft（随主 RFC 演进）
- **作用**：把讨论中已对齐的**问题、代码行为、缓解方向、负载指标**压成一份，供设计与评审引用；不替代 `README.md` 中 UB 指标定义，与之**互补**。

---

## 1. 问题（现象与风险）

- Worker 故障后，大量 Client 需在短时间内切到其它 Standby。
- **若多 Client 同时压向少数 Standby**，易出现 **URMA/UB 数据面在短时间内的并发建链与传输峰值**，与 **控制面/资源上限** 叠加，放大为节点不稳定或**级联故障**风险（需在客户/内部评审中如实陈述概率与条件）。
- **`USE_URMA` 路径**：切流/析构时除逻辑连接外，还须保证 **URMA 握手/重试停干净**、**Disconnect 对 Worker 对称释放** 与 **同一进程内 `UrmaManager` 无二次 Init/Destroy 错配**；**编译期** `USE_URMA` 与**运行期** `FLAGS_enable_urma`（握手可回退 TCP）不同。详见 [notes-use-urma-ub-lifecycle.md](./notes-use-urma-ub-lifecycle.md)。

---

## 2. 当前 Client 故障切换在代码里大致怎么工作

| 点 | 说明（datasystem 代码向） |
|----|---------------------------|
| **主路径** | `ObjectClientImpl::GetStandbyWorkersForSwitch`：在 **`serviceDiscovery_` 非空**且 **`GetAllWorkers` 成功**时，用 `ServiceDiscovery::GetAllWorkers`（内部 `ObtainWorkers` 扫 etcd `ETCD_CLUSTER_TABLE`）拉 **READY** worker 列表，按 `ServiceAffinityPolicy` 划分 **同机 / 异机** 等。 |
| **去当前** | 用 `currentApi->hostPort_` 与候选地址比，**去掉当前已连的 Worker**；从**同一台故障源 W** 上迁出的多 Client 去掉的同一个 W，**剩下的顺序对所有人一致**（同一次 etcd 视图下的遍历顺序 + 同策略下的分区）。 |
| **尝试策略** | `SwitchToStandbyWorkerImpl` **先** `TrySwitchToCandidateList(sameHost, ...)`，**再** `TrySwitchToCandidateList(others, ...)`；`TrySwitchToCandidateList` 对 `candidates` **按向量顺序**尝试，**第一个能完成切换的即停**（无「按负载比」、无 per-client 随机）。 |
| **RANDOM 策略** | `GetAllWorkers` 在 `RANDOM` 时把同机合并进 `otherAddrs` 等，**仍不 shuffle**；顺序由 etcd 遍历与 merge 方式决定。 |
| **与 HashRing** | Worker 侧对 **单点 `standby_worker`** 可用 **HashRing + UUID 的 MurmurHash** 算环上下一跳；**本切换主路径的候选序**不由此决定，**勿混为一谈**。 |
| **回退** | SD 失败时 `GetStandbyWorkers()`：心跳里攒的 `standby_worker` + `available_workers` 经 set 后 **`std::shuffle`**，与 SD 主路径行为不同。 |
| **Client→Worker 心跳** | **不**写 etcd cluster 表；**Worker→etcd 租约/节点值** 才是 `ETCD_CLUSTER_TABLE` 的写入源。把「负载」写进表需 **扩 `KeepAliveValue` 或并列表项 + 节流写 etcd**，不是「心跳自带改表」。 |

**结论（行为级）**：在「多 Client 自同一 W 迁出」的典型场景，**易优先挤在候选列表**（尤其同机段）**的头部几台**；**不是** `SelectWorker` 首连的随机能自动修补这一点（首连本文不展开）。

---

## 3. 与 URMA/UB 的关系（为何数据面是瓶颈）

- 切换路径上 `TrySwitchToStandbyWorker` → `ClientWorkerRemoteCommonApi::Init` → `TryFastTransportAfterHeartbeat`；`USE_URMA` 下会走 **URMA 首次握手**（如 `TryUrmaHandshake`）等，**对单台 Standby 的并发**敏感。
- 在 **鲲鹏超节点、京东类场景** 的讨论结论：**UB 数据面带宽** 往往是 Worker **存取的关键约束**；因此「负载」若用于**调度/均衡/Po2**，**近窗 UB 传输量** 比纯连接数、纯 RPC 次数更贴近（见同目录 `README.md` 正式定义：含 **C↔W 跨节点** 与 **W↔W** 的 UB 字节）。

---

## 4. 分阶段路线（与 README 里程碑对齐）

| 里程碑 | 交付 | Po2 比较输入（负载信号） |
|--------|------|---------------------------|
| **A（先验证）** | **Power-of-Two**：候选中 **二选一**，**连接数少者优先** 尝试。 | **各 Worker 上当前 Client 连接数**（实现与观测简单；用于三节点轮换故障，看连接是否更均衡）。 |
| **B（生产/京东口径）** | 同上 Po2 框架，**仅替换比较量**。 | **近窗 UB 传输 bytes**（C↔W + W↔W，见 `README.md` §1）。 |
| **后续** | **Jitter**、**Worker 熔断/限流**、**Client 换候选重试**。 | 与 Po2 正交。 |

**验证 Case（用户）**：`worker1/2/3` 正常 → **分别**让 w1、w2、w3 故障 → 观察存活节点 **Client 连接数** 是否相对均衡；详见 [validation-po2-client-count.md](./validation-po2-client-count.md)。

---

## 5. 缓解方向明细（与代码落点，索引级）

| 方向 | 阶段 | 要事 |
|------|------|------|
| **Power of two** | **A→B** | **A**：比较 **Client 连接数**。**B**：比较换为 **近窗 UB bytes**。均在候选中 **抽两地址**（随机无放回），**负载较小者优先**；候选不足 2 则顺序尝试。需 **每 Worker 可见信号** + `SwitchToStandbyWorkerImpl` 内调整首轮顺序；**缺数据** 回退。 |
| **Jitter** | **后** | `SwitchToStandbyWorkerImpl` / `TrySwitchToCandidateList` 前或候选间 **有界**随机延迟；注意与 `switchInProgress_` 的交互。 |
| **Worker 限流 + Client CONTINUE** | **后** | `RegisterClient`、**W↔W / URMA 握手**（如 `WorkerWorkerTransportServiceImpl::WorkerWorkerExchangeUrmaConnectInfo`）拒新；Client 将可换目标错误映射为 **`TrySwitchToCandidateList` 的 CONTINUE**。 |
| **负载写入 etcd** | **A/B**（若选 etcd） | 连接数或 UB 快照写入 `KeepAliveValue` 等：**向后兼容**、**节流** Put。 |

**Po2 与负载信号的衔接**：**里程碑 A** 在 `ClientManager` 类路径维护 **连接数** 并暴露给 Client。**里程碑 B** **计量** 在 URMA/UB 完成路径 + W↔W（`README.md` §2）；**消费**均在 Po2 比较前读 **每地址负载**。

---

## 6. 本目录文件关系

| 文件 | 内容 |
|------|------|
| `README.md` | 核心问题（切流 + 连接均衡）、主文档表、UB 指标、里程碑、**§3.1 验收清单**。 |
| [issue-rfc.md](./issue-rfc.md) | 与 get-metrics **同构**：背景/根因/方案/变更/测试/遗留。 |
| [design.md](./design.md) | Po2 伪代码、文件落点、通道选型、附录 Mermaid。 |
| [design-etcd-keepalive-value.md](./design-etcd-keepalive-value.md) | **集群行 value** 增第 4 段连接数；**非**改表名；与续租关系。 |
| `validation-po2-client-count.md` | **Po2+连接数** 三节点验证。 |
| **本文件** | 故障切换行为、风险、分阶段与代码速查。 |

---

## 7. 相关 rfc 链接

- [Get 时延分段（正交）](../2026-04-worker-get-metrics-breakdown/README.md)
- [URMA/TCP 可观测](../2026-04-kvclient-urma-tcp-observability/README.md)

---

## 8. 代码路径速查（便于跳仓库）

- `yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp`：`SwitchToStandbyWorkerImpl`、`GetStandbyWorkersForSwitch`、`TrySwitchToCandidateList`、`TrySwitchToStandbyWorker`
- `yuanrong-datasystem/src/datasystem/client/service_discovery.cpp`：`ObtainWorkers`、`GetAllWorkers`
- `yuanrong-datasystem/src/datasystem/common/kvstore/etcd/etcd_store.{h,cpp}`：`KeepAliveValue`
- `yuanrong-datasystem/src/datasystem/worker/worker_service_impl.cpp`：`RegisterClient`、`Heartbeat`（`standby_worker` 来源等）
- `yuanrong-datasystem/src/datasystem/worker/hash_ring/hash_ring.cpp`：`GetStandbyWorkerByUuid`（**Worker 侧**环上下一跳，非 Client 切换主序）
- `yuanrong-datasystem/src/datasystem/client/client_worker_common_api.cpp`：`TryFastTransportAfterHeartbeat`、`TryUrmaHandshake`
- 指标与 UB：`common/metrics/kv_metrics`、同目录 `README.md` §2
