# FMEA 文档索引 v2.0

> 整合日志指南 + Metrics + PR#652 SHM Leak Metrics

---

## 文档结构

```
fema-intermediate/
├── code-evidence/
│   ├── 01-urma-fault-detection.md    # URMA层故障检测代码证据
│   ├── 02-component-lifecycle.md    # 组件生命周期代码证据
│   └── 03-os-layer-faults.md         # OS层故障检测代码证据（已更新）
├── 05-urma-api-faults.md            # URMA API错误传播
├── 06-kvcache-interface-faults.md   # KVCache接口故障
├── 07-core-flow-orthogonal-analysis.md  # 核心流程正交分析
├── 08-fault-triage-guide.md         # 故障定位定界指南（三板斧）
├── 09-fault-test-construction.md   # 故障测试构造指南
├── 10-quick-reference-card.md        # 快速参考卡
└── 11-fault-triage-flowcharts.md    # 故障排查流程图 ← 新增

workspace/
├── fema-final.csv                  # FMEA最终分析（65条目，5大类）
└── fema-analysis-filled.csv         # FMEA详细分析（48条目）
```

---

## 文档用途说明

| 文档 | 用途 | 使用者 |
|------|------|--------|
| `08-fault-triage-guide.md` | 故障定位定界指南（三板斧） | 测试/运维 |
| `09-fault-test-construction.md` | 故障构造与验证 | 测试 |
| `10-quick-reference-card.md` | 快速参考卡（grep命令） | 运维 |
| `11-fault-triage-flowcharts.md` | Mermaid流程图 | 开发/测试 |
| `code-evidence/*.md` | 代码证据归档 | 开发 |

---

## 故障域分类

| 故障域 | 错误码 | 典型日志 | 定位方法 |
|--------|--------|---------|---------|
| **用户层(A)** | 2/3/8 | `K_INVALID` / `NOT_FOUND` | 参数校验 |
| **OS层(B)** | 1001/1002/19 | `ZMQ_SEND_FAILURE_TOTAL` / `RPC_RECV_TIMEOUT` | 网络故障注入 |
| **URMA层(C)** | 1004/1006/1008 | `[URMA_NEED_CONNECT]` / `[URMA_RECREATE_JFS]` | UB故障注入 |
| **组件层(D)** | 23/31/32 | `[HealthCheck]` / `Cannot receive heartbeat` | 进程故障注入 |
| **SHM泄漏(PR652)** | 无 | `worker_shm_ref_table_bytes` | 内存泄漏场景 |

---

## 核心流程 × 故障模式 覆盖

| 核心流程 | 故障数 | 覆盖状态 |
|---------|--------|---------|
| SDK Init | 6 | ✅ |
| MCreate | 8 | ✅ |
| MPublish | 9 | ✅ |
| MGet | 12 | ✅ |
| Exist | 6 | ✅ |
| 远端读取(Worker↔Worker) | 8 | ✅ |
| 组件生命周期 | 7 | ✅ |

---

## FMEA分析差异说明

| 维度 | fema-analysis-filled | fema-final |
|------|---------------------|------------|
| 分析维度 | 单一大类(datasystem) | 五级分类(OS/URMA/业务/特殊/组件) |
| 条目数 | 48 | 65 |
| 特色 | 详细的故障传播链路 | 按故障域分类，便于定界 |
| 用途 | 开发参考 | 测试验收/运维定界 |

两者可互补使用：
- **定界阶段**：使用 `fema-final.csv` 按故障域快速定位
- **根因分析**：使用 `fema-analysis-filled.csv` 查看详细传播链路

---

## 更新日志

| 版本 | 日期 | 更新内容 |
|------|------|---------|
| v2.0 | 2026-04-21 | 新增 `11-fault-triage-flowcharts.md` Mermaid流程图 |
| v2.0 | 2026-04-21 | 更新 `03-os-layer-faults.md` 增加代码验证汇总表 |
| v1.0 | 2026-04-20 | 初始版本 |
