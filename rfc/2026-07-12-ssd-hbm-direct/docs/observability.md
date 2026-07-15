# Observability Map: PerfKey · Metrics · Trace（NDS / HBM）

**Date**: 2026-07-13  
**Status**: Draft inventory（实现前先钉观测面；落地随 Task 8 / M-观测）  
**Related**: [verification-observability.md](./verification-observability.md)、[implementation-plan.md](./implementation-plan.md)、仓内 `perf_point.def` / `access_point.def` / metrics

## 1. 三层观测（已有能力，NDS 必须挂上）

| 层 | 机制 | 看什么 | 怎么捞 |
|----|------|--------|--------|
| **Perf** | `PerfPoint` / `PerfKey`（`src/datasystem/common/perf/perf_point.def`） | 热点分段时延（ns 级计数） | worker/client INFO 中 `[Perf Log]:`；workbench `gen_kv_perf_report.py`、`zmq_rpc_perf_nightly` 类脚本 |
| **Metrics** | `metrics_summary` JSON + `ResMetricCollector` + **AccessRecorder** | 路径计数、资源、API 访问延迟/结果 | glog `metrics_summary`；`grep_get_latency_breakdown.sh`；access 日志 |
| **Trace** | `Trace` / `TraceGuard` / RPC `trace_attachment` | 单次请求跨 client↔worker 关联排障 | 日志里 `trace_id` / `TraceID`；按 ID grep 全链路 |

原则：

1. **路径判定用 Metrics/Access（次数+原因）**；**时延拆解用 PerfKey**；**单次失败用 Trace+结构化 LOG**。  
2. 直通 vs DRAM reload **必须在三条线上都能区分**（至少 Perf 分段 + Access/metrics 计数 + 同 TraceID 日志）。  
3. 新 key **先复用现有 spill/H2D 树**，只在缺口处加 `NDS_*` / `HBM_IPC_*`，并同步进 `GetPerfKeyDefines` / access def。

---

## 2. 现有可复用锚点（对照源码）

### 2.1 PerfKey（已有，A/B 对照要用）

**Spill 读（DRAM reload 基线）**

| Key | 含义 |
|-----|------|
| `WORKER_SPILL_GET` | spill Get 总览 |
| `WORKER_SPILL_READ_FILE` | 读文件 |
| `WORKER_SPILL_GET_TO_MESSAGE` | 填 RPC message |
| `WORKER_SPILL_*` 写侧 | 写路径；Phase-1 直通不改，仍可对照压力 |

**Client H2D（今日 DRAM→HBM；NDS 成功后这段应变短或消失）**

| Key | 含义 |
|-----|------|
| `CLIENT_MGET_H2D_ALL` / `CLIENT_MGET_H2D_GET` / `CLIENT_MGET_H2D_COPY` | MGetH2D 总/Get/拷 |
| `CLIENT_H2D_MEMCPY` / `CLIENT_H2D_LOCAL_COPY` / `CLIENT_H2D_REMOTE_COPY` | 本地拷 vs RH2D |
| `HETERO_CLIENT_MGET_H2D` / `HETERO_CLIENT_MSET_D2H` | Hetero API 外包 |

**RH2D / Pipeline（对照，非 NDS）**

| Key | 含义 |
|-----|------|
| `WORKER_REMOTE_GET_PREPARE_RH2D_HOST_INFO` | RH2D 准备 |
| `PIPLN_RH2D_*` | Pipeline RH2D 分段 |

Perf A/B（决策 D17）：同 object  
`WORKER_SPILL_READ_FILE + CLIENT_H2D_*`（旧） vs `WORKER_NDS_*`（新，见下）+ 更短的 client copy（零拷贝时接近 0）。

### 2.2 AccessRecorder / Metrics（已有）

- Hetero：`DS_HETERO_CLIENT_MGETH2D` / `MSETD2H` / async 变体（`access_point.def`，经 `AccessRecorder::Object`）。  
- 资源：`ResMetricName::SPILL_HARD_DISK`（盘占用；直通不替代，仍要看）。  
- KV Get 分段：既有 worker get metrics breakdown（workbench RFC `2026-04-27-worker-get-metrics-breakdown`）——直通旁路应在 **同一 Get 树** 上挂子段，避免另起一套看不懂的报表。

### 2.3 Trace（已有）

- Client 发 RPC 带 `Trace::GetTraceID()`（`trace_attachment.h`）。  
- Worker 侧 `SetTraceContext` / `TraceGuard` 延续。  
- NDS 路径日志 **必须带同一 TraceID**，字段建议：`path=nds_direct|dram_reload|fallback_*`、`mapping_id`、`align`、`nds_rc`。

---

## 3. NDS / HBM 拟新增（实现时落 def，名称可微调但语义锁定）

### 3.1 PerfKey（时延树）

```text
HBM_IPC_REGISTER_ALL          # Register RPC e2e
HBM_IPC_EXPORT / IMPORT / CLOSE
HBM_IPC_MAPPING_LOOKUP

WORKER_NDS_ELIGIBILITY        # 门禁判定（含失败早退）
WORKER_NDS_ALIGN_GATE
WORKER_NDS_READ_ISSUE         # read_file / Fake 读
WORKER_NDS_DRAIN              # drain_read（Fake 可极短）
WORKER_NDS_E2E                # issue+drain 或 Fake 整段
WORKER_NDS_FALLBACK_TO_DRAM   # 转入 LoadSpilledObjectToMemory 前

CLIENT_HBM_COMM_COPY_OUT      # 未 Register：comm→user
CLIENT_NDS_GET_ALL            # 统一 Get 上 NDS 相关外包（可选）
```

binmock ST：Fake 路径也打同一套 key（count 可测；绝对值不作 SLO）。

### 3.2 AccessRecorder / 计数 metrics

```text
# Access（API / 路径）
DS_CLIENT_REGISTER_HBM_BUFFER
DS_CLIENT_UNREGISTER_HBM_BUFFER
DS_WORKER_NDS_DIRECT          # 成功直通（可挂 object access）
DS_WORKER_NDS_FALLBACK        # 带 reason 字段：align|buffer|nds|ipc|no_mapping

# 或 metrics_summary 计数器（二选一或双写，优先跟现有 Get breakdown 风格）
local_spill_hbm_direct
local_spill_dram_reload
direct_fallback_alignment
direct_fallback_still_in_buffer
direct_fallback_nds_error
direct_fallback_ipc
hbm_ipc_register_total / _fail
hbm_ipc_mapping_count
nds_bytes_total
```

### 3.3 Trace / 日志关键字（排障）

| 事件 | 建议 LOG 关键字（稳定可 grep） |
|------|-------------------------------|
| Register OK/Fail | `nds_hbm_register` |
| Import | `nds_hbm_import` |
| Eligibility 结果 | `nds_eligible` / `nds_skip reason=` |
| NDS issue/drain | `nds_io issue=` / `nds_io drain=` |
| Fallback | `nds_fallback reason=` |
| Deliver | `nds_deliver mode=zero_copy\|copy` |

统一：`trace_id=... objectKey=... mapping_id=...`

---

## 4. 场景 → 观测手段（怎么用）

| 场景 | Perf | Metrics/Access | Trace |
|------|------|----------------|-------|
| binmock 串通（①） | `WORKER_NDS_E2E` count>0；无真实 NDS latency | fallback 计数；direct count | 单测日志 grep `nds_` |
| Stage A IPC（②） | `HBM_IPC_IMPORT`；可选 client H2D pattern 对照 | register success/fail | 跨进程同一业务 trace（若 RPC Register） |
| Stage B 直通（③） | `WORKER_NDS_*` vs `WORKER_SPILL_READ_FILE` | direct/dram/fallback 比率 | 一次 Get 全链路 |
| 对齐拒绝 | `WORKER_NDS_ALIGN_GATE` 短；**无** `NDS_DRAIN` | `direct_fallback_alignment` | `nds_skip reason=align` |
| SpillBuffer | 无 NDS_E2E | `direct_fallback_still_in_buffer` | `nds_skip reason=buffer` |
| Perf A/B | 导出两路径 Perf 表对比 | 同 QPS 下 direct 占比 | 抽样 1% 慢请求 |

---

## 5. 采集与报表（脚本，跟事项①一起备）

| 动作 | 命令 / 脚本 |
|------|-------------|
| Perf + metrics 树 | `python3 .../scripts/metrics/gen_kv_perf_report.py --ascii-tree <worker.log>` |
| 指定 NDS keys | 同上 `--perf-keys 'WORKER_NDS_,HBM_IPC_,WORKER_SPILL_READ,CLIENT_MGET_H2D,CLIENT_H2D_'` |
| Get 分段 | `scripts/metrics/grep_get_latency_breakdown.sh <logdir>` |
| 本 RFC | `scripts/run_obs_smoke.sh`（见 `scripts/`）：跑完 binmock/Stage 后自动抽 `[Perf Log]` + `nds_` 行 |
| 人工 Stage A/B | 脚本已 tee 日志；checklist 要求贴回 **Perf 片段 + nds_fallback 统计** |

---

## 6. 与三事项 / 计划的挂钩

| 事项 | 观测最低交付 |
|------|----------------|
| **① binmock** | PerfKey def 落地 + Fake 路径埋点；Access/计数可区分 direct/fallback；ST grep `nds_` |
| **② Stage A** | `HBM_IPC_*` 有 count；Register fail 原因可见 |
| **③ Stage B** | `WORKER_NDS_ISSUE/DRAIN/E2E`；A/B Perf 表；fallback 饼图数据够用 |
| **M-观测** | implementation-plan Task 8 + 本文件评审；报表脚本 smoke |

实现顺序建议：先 **def + 埋点宏/助手**（避免各处字符串不一致）→ Fake 路径验证 count → 真路径复用同一助手。

---

## 7. 明确不做（Phase-1）

- 不为 NDS 新建独立 APM 系统；不接外部 tracing 后端（除非仓库已有）。  
- 不把 xds 内核 ftrace 当作产品必选项（可选运维增强）。  
- 不在未 Register 的 comm 上对用户暴露额外 metrics 标签泄漏内部 VA。
