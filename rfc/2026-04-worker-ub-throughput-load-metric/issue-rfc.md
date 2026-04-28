# [RFC]：Worker 故障切流与 Client–Worker 连接均衡（Power-of-Two + 负载信号）

## 背景与目标描述

Object Client 在 **Worker 发生故障** 时需从当前节点迁出，迁移路径上的**选机策略**若缺乏负载信息，易在短时间把大量 Client 导向**同一台** Standby，与 **URMA/UB 数据面** 并发建链叠加，影响稳定性与**连接分布均衡性**。

**本 RFC 要解决的“核心问题”是两层（一致面向「均衡」）**：

| 层次 | 问题陈述 | 成功判据（验收方向） |
|------|----------|----------------------|
| **A. 故障切流均衡** | 某 **Worker 故障**后，原连在其上的多 Client 在选 Standby 时，**不再固定按 etcd/列表顺序**挤向同一台，而是按**可比较的负载**在候选间做**更分散、更优**的决策。 | 三节点等典型拓扑下，轮换对 **w1/w2/w3** 注入故障，存活节点上 **Client 数或 UB 相关负载** 的**峰谷差**相对「无 Po2/无负载」基线**改善**（见 [validation-po2-client-count.md](./validation-po2-client-count.md)）。 |
| **B. Client–Worker 连接长期均衡** | 除**一次性故障**场景外，希望各 Worker 上 **C↔W 连接数/数据面压力** 在**稳态**下**不过分偏斜**（在现有连接模型下可度量的范围内）。 | 与 A 使用同一套 **Power-of-Two**：先以 **连接数** 为 Po2 比较信号验证；再以 **近窗 UB 字节**（京东/鲲鹏口径，见 [README.md](./README.md) §1）为生产级信号。 |

**与可观测类 RFC 的关系**：[Worker Get 时延 breakdown](../2026-04-worker-get-metrics-breakdown/README.md) 管 **时延定界**；本 RFC 管 **负载与选路均衡**，**正交**。

**非目标（一期不做）**：

- **不** 在本 `issue-rfc` 的「一期实现」中要求同时交付 **Jitter、Worker 熔断、Client 换候选重试**（可单独里程碑，见 [README.md](./README.md) 后续阶段）。
- **不** 承诺 Po2 在**任意**业务分布下达到严格数学上的「连接数方差最小」（仅保证相对基线**改进**与可测）。
- **不** 改变对外业务 API 语义；**不** 要求 Client 与 Worker 的**持久连接**模型本身改为「每 Client 多 Worker 轮询」类架构（本 RFC 仅在**已有切换路径**上增强）。

**关联文档**：

- 指标定义与里程碑摘要：[README.md](./README.md)  
- 设计细节与落点：[design.md](./design.md)  
- 行为背景与代码索引：[CONTEXT.md](./CONTEXT.md)  

---

## 根因与现状（代码行为摘要）

| 现象 | 原因（实现向） | 对「均衡」的影响 |
|------|----------------|-----------------|
| 故障后多 Client **同时** 扑向**少数** Standby | `GetStandbyWorkersForSwitch` 使用 etcd 列表序；`TrySwitchToCandidateList` **顺序尝试、首成即停**；**无** per-client 随机、**无**负载比较。 | **切流**时热点 |
| 与 HashRing 易混淆 | Worker 在 Heartbeat/Register 里下发的 `standby_worker` 可经 **环上下一跳** 计算；**不** 覆盖 Client 侧 SD 主路径的**候选序**。 | 解释预期 ≠ 本方案 |
| Client 与 etcd 的「心跳」 | **Client→Worker gRPC 心跳不** 写 `ETCD_CLUSTER_TABLE`；**Worker→etcd 租约** 写节点。负载若走 etcd 需**扩** `KeepAliveValue` 等并 **节流**。 | 选路若读 etcd，需**明确定义**刷新频率 |

> 更细的调用链与文件指针见 [CONTEXT.md](./CONTEXT.md) §2–§8。

---

## 建议的方案

### 总览：Power-of-Two Choices（Po2）

在 **每个** `TrySwitchToCandidateList` 相关决策点（在**同分区** `sameHost` 或 `others` 的候选集上，细节见 [design.md](./design.md) §2）：

1. 若候选数 **< 2**：**退化为** 当前代码的**顺序尝试**（行为不变）。  
2. 若候选数 **≥ 2**：**无放回** 随机抽取 **两** 个**不同** `HostPort`；向公开源读取二者各自的 **负载标量**（里程碑 A：`active_client_count`；里程碑 B：近窗 **UB 传输字节** 或派生速率）。  
3. **先尝试** 负载**较小**者；若该次 `TrySwitchToStandbyWorker` 链失败并返回 `CONTINUE`，再依产品约定试另一候选或继续列表（见 design **回退**）。

**缺省负载数据**：视为「不可比较」，**回退**为与当前实现一致的**固定顺序**尝试（不放大行为差异于线上不可控）。

### 里程碑划分（与 [README.md](./README.md) §1.1 一致）

| 里程碑 | Po2 比较信号 | 目的 |
|--------|-------------|------|
| **A** | **各 Worker 当前 Client 连接数**（与 `ClientManager` 一致） | **低成本**验证 Po2 与三节点故障轮换；观测量直观（每节点「几条连接」）。 |
| **B** | **近窗 UB 累计字节**（C↔W + W↔W，[README.md](./README.md) §1 结论） | **京东/鲲鹏** 数据面瓶颈下的生产口径；在 A 稳定后替换比较量。 |
| **后续** | — | Jitter、限流/熔断、重试，正交增强。 |

### 切流与「连接长期均衡」的决策关系（逻辑树）

```text
Worker 故障 / 需切 Standby？
  │
  ├─ 候选集 < 2 个有效地址
  │     └─ 顺序尝试（与现网一致），无 Po2
  │
  └─ 候选集 ≥ 2
        ├─ 能读到两台负载？
        │     ├─ 是 → Po2：随机两地址 → 轻负载者先试
        │     └─ 否 → 回退：顺序尝试
        └─ 失败 CONTINUE → 下一候选 / 下分区（现逻辑 + 可扩展，见 design）
```

**稳态**下「连接更均衡」：不强制在本 RFC 一期单独做「全局重平衡」；**Po2 在每次故障切流**上减少偏斜，间接改善长期分布。**里程碑 B** 的 UB 信号更贴数据面热点。

### 与「Client–Worker 连接均衡」的说明

- **切流**（问题 A）直接由 **Po2 + 负载** 在 `SwitchToStandbyWorkerImpl` 路径解决。  
- **稳态**（问题 B）在**不**改「Client 长期只连**一台**主 Worker」的产品前提下，**主要**通过：故障与扩容时的 **Po2 选轻**、以及后续可选的**运维面**调权；若未来需要**主动 rebalance**，需单独 RFC，**不在**本 `issue-rfc` 范围。

---

## 涉及到的变更

### 新增/扩展（示意，以 design 为准）

| 项 | 说明 |
|----|------|
| Worker 侧 **每节点负载** 对 Client 或注册中心**可读** | 里程碑 A：连接数；B：UB 近窗字节的快照或等效字段（etcd `KeepAliveValue` 扩展、Heartbeat 字段、或只读 query，**须节流**见 design）。 |
| Client 侧 **Po2 选择** | `object_client_impl.cpp` 中在调用 `TrySwitchToCandidateList` 前，或对 `candidates` **预排序/重排首轮顺序**；缺数据**回退**。 |
| 指标（B） | `kv_metrics` 或统一层：Worker 维 **UB** 累计与滑窗，C↔W 与 W↔W 路径打点（[README.md](./README.md) §2）。 |
| 测试 | 见下「测试验证」与 [validation-po2-client-count.md](./validation-po2-client-count.md)。 |

### 核心修改文件（里程碑 A 预期）

| 文件 | 说明 |
|------|------|
| `yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp` | `GetStandbyWorkersForSwitch` / `SwitchToStandbyWorkerImpl` / `TrySwitchToCandidateList` 周边 Po2 与回退。 |
| `yuanrong-datasystem/src/datasystem/client/service_discovery.{h,cpp}` | 若负载随 `GetAll` 解析，扩展返回结构。 |
| `yuanrong-datasystem/src/datasystem/common/kvstore/etcd/etcd_store.{h,cpp}` | 可选：`KeepAliveValue` 第 4 段或兼容策略。 |
| `yuanrong-datasystem/src/datasystem/worker/worker_service_impl.cpp` 与 `client_manager` 相关 | 暴露连接数、Heartbeat 等。 |

里程碑 B 额外：`urma_manager.cpp` / `urma_resource.cpp` / W↔W 路径等，**完整表** 见 [design.md](./design.md) §3。

### etcd：集群行「表观」示意（仅 value 扩展，详规另文）

**不是** 新建 DB 表；**`ETCD_CLUSTER_TABLE`** 下行键 **仍为** `worker 地址`；**变** 的是每行 **value 文本**（`KeepAliveValue` 序列化）：

```text
逻辑:  prefix/.../ + rowKey(Worker 地址)  ->  value
现网:  value = timestamp;state[;hostId]
目标:  value = timestamp;state;hostId_or_dash;active_client_count
      （无 host 时第三段为 '-'，见专章）
```

| 段（示意） | 含义 | 老 Client |
|------------|------|-----------|
| 1–2 | 必有：时间戳、状态 | 可读 |
| 3 | `hostId` 或 **`-`** | 可读，老串可无第 3 格 |
| 4 | `active_client_count` | **不解析** 时 Po2 回退，行为同现网顺序 |

**续租与上报**：`LeaseKeepAlive` 流**不**改 value；连接数经 **同 leaseId 的 `PutWithLeaseId`** 刷新（**节流**）。**完整** 字段、兼容与误区别见 [design-etcd-keepalive-value.md](./design-etcd-keepalive-value.md)。

### 不变项

- **不** 为 Po2 改变 **gRPC/RegisterClient** 对外 proto 的**必须字段**（新增字段**可选/后向兼容**）。  
- **不** 在「一期」**强制** 所有集群开启 Po2（建议 **gflag 开关**）。  

---

## 测试验证

### 功能与基线

1. **单元测试**：`RandomData` / 候选为 0/1/2+ 时 Po2 与回退；**可选** 假负载表注入；**非 URMA 与 `USE_URMA` 两条 C↔W 路径** 在合入中**均** 有可测覆盖，见 [validation-test-design-and-observation.md](./validation-test-design-and-observation.md) §0。  
2. **集成/场景**（里程碑 A）：**优先** 在 **非 URMA** 下按 [validation-po2-client-count.md](./validation-po2-client-count.md) 跑通 **w1/w2/w3 轮换** 与基线，**再** 补 **URMA** 专项；**记录** 各 Worker **Client 连接数** 与基线（关 Po2）对比。  
3. **里程碑 B**：在 st 或同环境上确认 **UB counter** 与 **etcd/心跳** 中数值与**手工算** 滑窗一致（容差在 design 约定）。

### 构建与单测（与仓库习惯一致）

- 优先在 **`xqyun-32c32g`** 对 datasystem 跑 **相关** `bazel test` / `ctest` 子集；Workbench 不强制为 Po2 单独新增脚本，**可** 在 `rfc/.../results/` 留 **metrics 或日志** 样例。  
- 行宽、第三方缓存等遵守仓库与 `.cursor` 规则；Workbench **RFC 正文不强制 120 列**。

### 粗验命令（示例）

```bash
# 日志中各 worker 上连接数/ metrics（具体 metric 名以合入后 design 为准）
rg 'metrics_summary|AddClient|active.*client' -n "$LOG_DIR"/*.INFO
```

---

## 遗留事项（待人工/产品确认）

1. **Po2 作用域**：仅在 `others` 内抽二，还是 **同机/异机** 分区内各做一次 Po2，与 `PREFERRED_SAME_NODE` 的交互。  
2. **etcd 写频率**：每连接变化是否都更新负载字段；**节流** 周期与**陈旧读** 上限。  
3. **B 的 UB 窗口**：1s/5s/1min 与 **Grafana** 面板是否用同一套。  
4. 与**全局调度**、**K8s** 的边界：本 RFC 仅 **Client 侧切换** + **可见负载**；不替代集群级调度。  

---

## 期望的反馈时间

- 建议 **5～7 个工作日** 内对 **Po2 作用域、里程碑 A 上线开关默认值、B 的 UB 窗口** 给出产品/业务确认。  

---

## 文档与状态

- 与 [get-metrics-breakdown issue-rfc](../2026-04-worker-get-metrics-breakdown/issue-rfc.md) **同构**（背景 / 方案 / 变更 / 测试 / 遗留）。  
- 实现以 [design.md](./design.md) 为技术主文档；`Status` 与总表在 [README.md](./README.md) 与 [rfc/README.md](../README.md) 维护。  
