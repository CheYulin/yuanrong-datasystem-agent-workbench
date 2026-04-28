# RFC：以 UB 数据面吞吐刻画 Worker 负载（鲲鹏超节点 / 京东场景）

- **Status**: **Draft**
- **Started**: 2026-04-27

**核心问题（本 RFC 要解决）**

1. **Worker 故障时 Client 切流的均衡性**：避免大量 Client 在故障窗口**同时**按固定 etcd 顺序涌向**同一** Standby，放大 URMA/UB 数据面峰值。  
2. **Client–Worker 间连接的均衡性**：在可度量范围内，使各 Worker 上 **C↔W 连接**（及后续里程碑 B 的 **UB 数据面负载**）**不过分偏斜**；**一期**以 **Power-of-Two + 连接数** 验证，**二期**将 Po2 比较量换为 **近窗 UB 字节**。

**主文档**

| 文档 | 作用 |
|------|------|
| [issue-rfc.md](./issue-rfc.md) | 背景与目标、根因表、**Po2 方案**、里程碑、变更清单、测试与**遗留事项**（与 [get-metrics-breakdown issue-rfc](../2026-04-worker-get-metrics-breakdown/issue-rfc.md) 同构）。 |
| [design.md](./design.md) | **算法**、分区策略、**文件级落点**、负载通道选型、陈旧与回退、验收、Mermaid。 |
| [CONTEXT.md](./CONTEXT.md) | 故障切换**代码行为**压缩说明与路径速查。 |
| [validation-po2-client-count.md](./validation-po2-client-count.md) | 三节点 **Po2+连接数** 验证 Case。 |
| [validation-test-design-and-observation.md](./validation-test-design-and-observation.md) | **用例构造**、**先非 URMA 后 URMA**（代码**双路径**须覆盖，§0）、**均衡度指标**、**观测**、etcd 陈旧度。 |
| [diagrams/](./diagrams/README.md) | **PlantUML 时序图**：Client 与 SD/切流；Worker 与 **etcd 租约**；**连接数** 与 `PutWithLeaseId` 关系说明。 |
| [design-etcd-keepalive-value.md](./design-etcd-keepalive-value.md) | **etcd 侧**：不改表名/行键，**扩展 `KeepAliveValue` 行 value** 携带 `active_client_count`；与「续租流」**非**同一路径的说明。 |
| [notes-use-urma-ub-lifecycle.md](./notes-use-urma-ub-lifecycle.md) | **USE_URMA / UB**：编译期与运行期开关、**建链/重试/回退** 与 **析构/切流** 时须保证的语义（与 Po2 正交）。 |

---

## 1. 问题与结论（讨论压缩）

- **环境**：在 **鲲鹏超节点** 上，**UB（Unified Buffer / 统一缓冲相关数据面，经 URMA 等路径）的带宽** 通常是制约 Worker **存储与读取** 的最关键资源，而不是单纯的 CPU 或单段时延。
- **目标**：为调度、容量与热点分析，需要一个 **能代表「Worker 有多忙（数据面压力多大）」** 的标量/曲线指标。
- **结论（指标定义草案）**：
  - 使用 **最近一段时间窗口内，该 Worker 经 UB 传输的数据量**（**字节**，可再换算为 B/s 或近实时速率）。
  - **口径必须同时包含**：
    1. **跨节点 Client ↔ 该 Worker** 的 UB 数据面传输量（远端 client 经 UB 与 entry/data worker 的大块读写等）；
    2. **Worker ↔ Worker** 的 UB 数据面传输量（例如拉数、迁移、旁路 data path 等经 URMA/UB 的部分）。
- **场景陈述**：在 **京东类** 部署与 access pattern 下，上述并集被视为 **对 Worker 压力最贴近的单一负载指标**（优于仅用 RPC 次数、或仅用某一类时延分桶）。

### 1.1 分阶段实施（整体逻辑，已对齐）

| 阶段 | 范围 | 说明 |
|------|------|------|
| **里程碑 A（先验证）** | **Power-of-Two + Client 连接数** | 故障切换选 Standby 时做 **二选一**；**比较输入** 为各 Worker 上 **当前 Client 连接数**（**较小优先**），实现与观测成本低，用于 **三节点轮换故障** 下看连接是否更均衡。详见 [validation-po2-client-count.md](./validation-po2-client-count.md)。 |
| **里程碑 B（生产/京东口径）** | **Po2 + UB 传输字节** | 在 A 跑通后，将 Po2 的比较量 **换为** 上表 §1 的 **近窗 UB 字节**（C↔W + W↔W）。仍 **不** 要求在本里程碑同时上 jitter/熔断/重试。 |
| **后续阶段** | **Jitter + 熔断 + 重试** | 有界随机延迟；Worker 限流/拒新；Client 换候选重试。在 Po2 与负载可见性稳定后再上。 |

详细行为与代码索引见 [CONTEXT.md](./CONTEXT.md) §4「分阶段路线」（若与上表 **里程碑** 有出入，**以本 README 里程碑为准**）。

### 1.2 三节点验证场景（用户 Case，摘要）

- 初始：`worker1`、`worker2`、`worker3` 正常；Client 按用例连入。  
- **每次让一个 Worker 故障**，观察剩余节点上 **Client 连接数** 是否相对均衡（相对无 Po2+连接数 的基线）。  
- 轮换对 **w1 / w2 / w3** 分别故障，重复多轮。  
- 完整步骤与判据见 [validation-po2-client-count.md](./validation-po2-client-count.md)。

---

## 2. 与「当前代码 / 可观测」的关系（便于后续落地）

| 方向 | 现状 / 落点（datasystem） | 与 UB 负载指标的关系 |
|------|--------------------------|----------------------|
| **Client 侧已有个别字节累加** | `kv_metrics`：`client_put_urma_write_total_bytes`、`client_get_urma_read_total_bytes`（COUNTER, bytes） | 反映 **client 进程** 上 URMA 读/写量，**不等于** 单台 worker 的 UB 总负载；调度侧若要看 **per worker**，需 **worker 维度的 UB 出口/入口字节** 或从日志/聚合拉齐。 |
| **UB/URMA 实现** | `urma_manager.cpp` / `urma_resource.cpp` 等；Client↔Worker 数据面如 `client_worker_base_api` 中 UB chunk/pipeline 路径 | 实际 **传了多少字节** 宜在 **完成一次 URMA 写/读** 的公共路径上 **统一累加**（并区分 C↔W / W↔W 若产品需要可拆成两条 counter + 一条 sum）。 |
| **Worker↔Worker** | `worker_worker_oc_service_impl.cpp` 等 | 与 **跨节点 C↔W** 并列纳入「Worker UB 总传输量」口径时需在此类路径上 **显式计量** 或复用与 URMA/UB 共用的 **peer 维 counter**（若已有可观测基座则扩展标签/名字）。 |
| **时延可观测（正交）** | [2026-04-worker-get-metrics-breakdown/](../2026-04-worker-get-metrics-breakdown/README.md) 侧重 **Get 分段时延**、定界用 histogram | 与 **本 RFC 的「负载=字节/窗口」** 互补：时延看路径是否变慢；**UB 字节** 看数据面是否饱和。 |

**缺口（实施时需写清）**：现网 metric 中 **缺乏「按 Worker 聚合、且明确覆盖 C↔W + W↔W 的 UB 字节」** 的单一或组合指标时，本 RFC 的「负载」需 **新增/拼接** 并在文档中固定 **时间窗口、进程边界、双工是否各算各的** 等。

---

## 3. 非目标（本文件范围）

- 不规定具体 **Prometheus 名字 / label**（留给 `design.md` 与 MR）。
- 不替代 **SLO 类时延** 指标；**UB 负载** 用于 **容量、调度、对比节点压力**，不单独作为「好/坏业务」判据。

---

## 3.1 验收清单（可勾选）

1. **里程碑 A（Po2 + 连接数）**  
   - **UT**：候选数 0/1/2+、负载缺失回退、gflag 关闭时与**现网顺序**一致（见 [design.md §6](./design.md#6-验收与回归)）；**非 URMA 与 URMA 两条 C↔W 路径** 均覆盖（[validation-test-design-and-observation.md](./validation-test-design-and-observation.md) §0）。  
   - **场景**：**先** 非 URMA 主体验证、**再** URMA 专项（顺序约定见 [validation-test-design-and-observation.md](./validation-test-design-and-observation.md) §0）；按 [validation-po2-client-count.md](./validation-po2-client-count.md) 做 **w1/w2/w3 轮换故障**，对比**开/关 Po2** 下各存活 Worker **Client 连接数**分布（峰谷差或简单方差优于基线）。  
2. **里程碑 B（Po2 + UB bytes）**  
   - Worker 级 **UB** 近窗与 **C↔W + W↔W** 口径与 `design.md` / §2 一致；Po2 **仅替换** `L`，**不** 改失败重试框架。  
3. **实现合入后**：将本 RFC **Status** 置为 **Done** 并更新 [rfc/README.md](../README.md) 表。

---

## 4. 相关 RFC 与 Workbench

| 条目 | 说明 |
|------|------|
| [2026-04-worker-get-metrics-breakdown/](../2026-04-worker-get-metrics-breakdown/README.md) | Get 路径 **时延** 分段、grep 定界。 |
| [2026-04-kvclient-urma-tcp-observability/](../2026-04-kvclient-urma-tcp-observability/README.md) | URMA/连接类可观测与错误码。 |
| [2026-04-zmq-rpc-metrics/](../2026-04-zmq-rpc-metrics/README.md) 等 | 控制面/ ZMQ 与数据面正交。 |

---

## 5. 下一步（与里程碑对齐）

**里程碑 A：Po2 + Client 连接数（验证用）**

1. Worker：暴露**各节点当前 Client 连接数** 给 Client 或注册中心（`ClientManager` 计数 + etcd/Heartbeat 等，需节流时按设计）。
2. Client：`GetStandbyWorkersForSwitch` / `SwitchToStandbyWorkerImpl`：**随机两候选** → **连少者优先** 尝试；缺数据时 **回退** 为当前顺序尝试。
3. 按 [validation-po2-client-count.md](./validation-po2-client-count.md) 做 **w1/w2/w3 轮换故障**，对比连接数**是否更均衡**。

**里程碑 B：Po2 比较量换为 UB bytes**

4. 在 `kv_metrics` 或统一采集层完成 **Worker 级** UB 字节（§2、§1 结论），C↔W + W↔W 打点与对 Client/etcd 的暴露；将 Po2 的**比较**从连接数 **切换** 为 UB（或速率）。
5. 与产品确认 **滑窗**、Po2 作用域及 **面向 UB 的** 验收用例。

**后续阶段（jitter + 熔断 + 重试）**

6. 见 [CONTEXT.md](./CONTEXT.md)；Workbench metrics **基线** 贯穿各阶段。
