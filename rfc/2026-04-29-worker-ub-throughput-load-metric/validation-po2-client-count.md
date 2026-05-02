# 验证方案：Power-of-Two + Client 连接数（PoC）

- **Status**: Draft
- **目的**：在 **UB 字节** 作为京东/鲲鹏侧最终负载口径之前，先用 **Client 连接数** 作为 Po2 的**比较信号** 做一版可跑通的实现与验证；实现简单、易在现网或 ST 环境观察 **各 Worker 上连接是否更均衡**。
- **与主 RFC 关系**：见 [README.md](./README.md) §1.1「里程碑」；本文件只描述 **场景与判据**，不规定 metric 名字与 MR 细节。  
- **用例怎么设计、记什么、怎么比、URMA/重复次数**：见 [validation-test-design-and-observation.md](./validation-test-design-and-observation.md)。  
- **顺序与覆盖**：**验收用例/脚本** **优先** 在 **非 URMA** 下完成 **§2 行为预期** 对应场景与基线，**再** 增加 **URMA** 环境专项；**实现** 上 Po2/切流/负载读 须对 **非 URMA 与 URMA** 两条 C↔W 路径**均可测**，见 [validation-test-design-and-observation.md](./validation-test-design-and-observation.md) **§0**。

---

## 1. 拓扑与前置

- **Worker**：`worker1`、`worker2`、`worker3` **均正常** 注册到集群（etcd READY 等前置满足 Object Client 使用 Service Discovery 切换的前提）。
- **Client**：事先已连到各 Worker（典型压测布局：多 Client 主要连在 **worker1**，或均匀分布；由用例指定）。需能触发 **故障切换**（kill worker1 进程、断网、注入等）。

---

## 2. 行为预期（实现目标）

- 当 **某一 Worker 故障**（例如 `worker1`）时，原连接其上的 Client 走 **`SwitchToStandbyWorkerImpl`** 路径迁出。
- **Po2**：在 Standby **候选集** 中先 **随机选两个** 不同地址，按 **各 Worker 当前 Client 连接数**（**较小者优先**）决定 **先尝试** 哪一台；若失败再按现有逻辑继续试其它候选或下一分区。
- **负载信号（本阶段）**：**每个 Worker 上的活跃 Client 连接数**（与 `ClientManager` 侧计数语义对齐即可），**不是** UB 字节。

---

## 3. 场景 Case（用户口述整理）

| 步骤 | 动作 | 观察 |
|------|------|------|
| 0 | 三节点均正常，Client 按用例连入 | 基线：记录 **worker1/2/3 各自 Client 数**（或 metrics / 管理面查询）。 |
| 1 | 制造 **worker1 故障** | 原在 w1 上的 Client 应切到 w2/w3；期望 **不** 出现「几乎全部挤到**同一** Standby」的极端（相对无 Po2/无负载对比时 **更均衡**）。 |
| 2 | 恢复或替换 worker1，再制造 **worker2 故障** | 重复观察各节点 **Client 连接数** 分布。 |
| 3 | 再制造 **worker3 故障** | 同上。 |
| 4 |（可选）轮换多次 / 多 Client 规模 | 统计 **方差/最大最小比** 或 P95 与均值的差距，作简单均衡判据。 |

**判据（建议，可再量化）**：

- 在 **可比** 的 Client 规模与故障注入方式下，启用 Po2+连接数 后，**故障后**存活节点上的 **Client 数** 的 **峰谷差** 或 **标准差** 小于**仅按 etcd 顺序**切换的基线（或低于某经验阈值）。

---

## 4. 非本验证范围

- 不验证 **UB 字节** 是否平衡（属后续把 Po2 比较量 **替换** 为 UB 后的用例）。
- 不覆盖 **jitter、熔断、重试**（见主 RFC 后续阶段）。

---

## 5. 实现侧备注（与 datasystem 对齐时自查）

- **连接数来源**：Worker 侧 **注册/下线** 路径维护的权威计数；需 **对 Client 可见**（etcd 扩展、Heartbeat 增字段、或调试用只读接口），以便 Po2 在 `GetStandbyWorkersForSwitch` 之后比较。
- **多租户 / 多进程**：若验证脚本只测单租户，在文档中写明，避免与生产多租户行为混淆。
