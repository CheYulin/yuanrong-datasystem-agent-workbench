# 设计：etcd 集群节点值扩展（随租约曝光 Client 连接数）

- **Status**: Draft
- **归属 RFC**: [2026-04-worker-ub-throughput-load-metric/](./README.md)
- **范围**：在**不改「逻辑表名、行键」**的前提下，扩展 **`ETCD_CLUSTER_TABLE`** 每行 **value 字符串** 的语义，使各 Worker 上的 **active client 数** 可被 `ServiceDiscovery::ObtainWorkers` 解析，供 **Power-of-Two 切流** 使用。  

---

## 1. 概念澄清：没有独立的「表结构迁移」

datasystem 对 etcd 的抽象是 **逻辑表名 → key 前缀**；**`ETCD_CLUSTER_TABLE`** 对应前缀下一组 **KV 行**。

| 项 | 当前约定（不变） |
|----|------------------|
| 逻辑表名 | `ETCD_CLUSTER_TABLE` 字面值 `datasystem/cluster`（与 `FLAGS_cluster_name` 等拼成**物理前缀**） |
| 行键 `key` | **单 Worker 地址** 字符串（如 `host:port`），与 `InitKeepAlive(..., workerAddress_.ToString(), ...)` 一致 |
| 租约 | 同一行可 **`Put` + `lease_id`** 绑定；**LeaseKeepAlive 流**只续 **TTL**，**不** 自动改写 value（见同目录 [diagrams/seq-worker-etcd-lease.puml](./diagrams/seq-worker-etcd-lease.puml)） |

因此：**不需要「新建一张 etcd 表」或改表名**；要改的是**这一行 value 的字符串格式**（`KeepAliveValue` 序列化），以及 Worker 在**何时**以**同一 `leaseId`** 再 **Put** 一次（见 §4「与续租的关系」）。

---

## 2. 当前 value 形态（基线）

代码侧结构体为 `KeepAliveValue`（`timestamp` / `state` / `hostId`），**序列化**为分号分隔**文本**（`etcd_store`）：

| 段序 | 字段 | 说明 |
|------|------|------|
| 1 | `timestamp` | 最后一次写入的墙钟/单调钟字符串（`AutoCreate` / 更新时刷新） |
| 2 | `state` | 如 `ready` / `exiting` 等与 `ETCD_NODE_*` 对齐 |
| 3 | 可选 | `hostId`（`FLAGS_host_id_env_name` 等；可为空，则**不出现在串里**） |

**行示例（概念）**：

```text
1714123456789012345;ready;host-abc-001
```

解析规则：`FromString` 至少两域；有第三域则视为 `hostId`（见 `etcd_store.cpp` 现有实现）。

---

## 3. 目标：在 value 中携带「Client 连接数」

### 3.1 第 4 段：`active_client_count`（推荐行格式）

在**不破坏旧 2/3 段**的前提下，**新写** 建议统一为 **4 段**；第 3 段在无 host 时用 **`-`** 占位。计数与 `ClientManager` 等一致，业务语义在 MR 中定稿。

| 段序 | 字段 | 说明 |
|------|------|------|
| 1 | `timestamp` | 同 §2 |
| 2 | `state` | 同 §2 |
| 3 | `hostId` 或 **`-`** | 无 host 时写 **`-`**（新写推荐）；**老** 行可能仍只有 2 段，或 3 段为真实 `hostId` |
| 4 | `active_client_count` | 非负整数；**无** 第 4 段则 **Po2 回退** |

**行示例**：

```text
1714123456789012345;ready;host-abc-001;42
1714123456789012345;ready;-;17
```

**`FromString` 读规则（示意）**：

- **2 段**：同现网 → 无连接数。  
- **3 段**：同现网 → 无连接数。  
- **4 段**：第 3 为 host 或 `-`，第 4 为连接数 → 供 Po2。  

须配 **2/3/4 段** 单测；若不接受第三段 **`-`**，在 MR 中改本节并**避免**第三段多义。

### 3.2 与「UB 字节」等后续指标

里程碑 B 若要在同一行再带 **近窗 UB 字节**，可继续 **第 5 段** 或**单独 key**；本设计章节**仅**锁定「连接数」**一种** 标量，避免无界膨胀——多指标建议 **子版本** 或 **JSON value** 另开文档（属于**破坏性** 更大、需与运维对齐）。

---

## 4.「随续租约上报」在实现上指什么

etcd 的 **KeepAlive 流** 只保证 **lease 不过期**；**不在流里携带业务字段**。因此「**随**续租**」的准确落地是：

1. 进程内仍跑 **同一 `leaseId`** 与 **EtcdKeepAlive** 流（续期）。  
2. 在**节流条件**满足时，Worker 调用 **`PutWithLeaseId(table, rowKey, newValue, leaseId)`**，其中 **`newValue` 在旧值基础上刷新 `timestamp` 与第 4 段计数**。  
3. 这样从**效果**上，监控侧看到的是：节点键仍在、租约续存，**value 周期刷新**；与「**续租**」在时间上**相邻**，但**不是** gRPC 流**自动**带上连接数。  

若产品坚持「**每次** 连接数变化都写 etcd」，需单独立项评估 **etcd QPS** 与 **watch 放大**；本 RFC 默认 **节流**（定时 **或** 变化超阈，见 [issue-rfc.md](./issue-rfc.md)）。

---

## 5. 解析侧（Client / Service Discovery）

- **`KeepAliveValue::FromString`**：在解析完 `hostId` 规则后，若仍有**尾部** 或 **第 4 个分号段**，则填入结构体**新成员** `active_client_count`（`optional<uint32_t>` 或 特殊哨兵表示未设置）。  
- **`ServiceDiscovery::ObtainWorkers`**：将解析出的计数挂到**内部** `HostPort` 旁（或并行的 `map`），供 `GetStandbyWorkersForSwitch` 的 Po2 使用；**无字段** 则**不调**重排。  

---

## 6. 验收要点

- 老二进制读新 4 段 value：**忽略未知段** 或**按扩展规则** 读连接数，**不** `K_INVALID`。  
- 新二进制读老 2–3 段 value：**与现网**一致。  
- 压测：限流后 etcd Put QPS 在可接受范围；watch 客户端无异常风暴。  

---

## 7. 与现有文件的关系

| 文件 | 作用 |
|------|------|
| [design.md](./design.md) | Po2 与代码落点总览。 |
| [diagrams/seq-worker-etcd-lease.puml](./diagrams/seq-worker-etcd-lease.puml) | 租约 + Put 与**续租流** 分工。 |
| 本文件 | **仅** 集群行 **value** 字段扩展与兼容策略。 |

---

## 8. 小结表（**非** SQL 表，而是「同一行 value 的字段扩展」）

| 层次 | 变更 |
|------|------|
| 逻辑表 / 行键 | **无变更**；仍为 `ETCD_CLUSTER_TABLE` + `workerAddr`。 |
| value 文本 | **可选** 第 4 段 `active_client_count`；**需** 与 `FromString` / `ToString` 及**无 hostId** 情况统一规则。 |
| 租约 | **同一** `leaseId`；更新计数 = 新的 **带租约 Put**，**不** 依赖 **LeaseKeepAlive**  body 带业务数据。 |
| Client | 解析后供 **Po2**；缺段则回退。 |
