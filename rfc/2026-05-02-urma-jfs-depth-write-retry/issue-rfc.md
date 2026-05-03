# [RFC] URMA send JFS 深度可配置、write post 遇 URMA_EAGAIN 退避重试与 spin 时延 Histogram

## 背景与目标描述

### 动机

1. **Send jetty JFS depth** 长期硬编码为与 recv JFR 同量级常量（历史默认 256），不利于在 **浅队列 / 设备限额** 场景下调参验证；需要将 **send 侧 JFS depth** 暴露为 **gflags**，并把临时默认设为较小值（如 **2**）以便压测与行为对齐。
2. **`urma_post_jetty_send_wr`**（本仓库经 `PostJettyRw` → `ds_urma_post_jetty_send_wr`）在设备侧可能出现 **`URMA_EAGAIN`**（典型：队列满或 WR 缓冲暂不可用，参见 UMDK `bondp_datapath`）。当前实现一旦失败即返回错误，缺少 **基于 RPC 剩余时间的退避重试**，易导致不必要失败或与上层超时语义不协调。
3. **可观测性**：需要度量「从 **首次 `URMA_EAGAIN`** 到 **首次 post 成功**」的耗时（µs），用于评估浅队列下的 **退避 / 重试** 成本；**首次 post 即成功则不采样**；**非 EAGAIN 失败或 deadline 退出也不采样**。

### 目标

| 目标 | 说明 |
|------|------|
| Gflags：depth | **`urma_send_jfs_depth`**（uint32，默认 **2**）：仅 **send jetty** 的 **`urma_jfs_cfg_t.depth`**；recv 仍为 **`RECV_JETTY_JFS_DEPTH`**；recv 专用 JFR 创建仍使用常量 **`JETTY_SIZE`**（256）。 |
| Gflags：退避 | **`urma_write_spin_retry_sleep_us`**（uint32，默认 **50**，单位 **µs**）：两次 **`URMA_EAGAIN`** post 尝试之间的 **`nanosleep`**；**0** 表示不睡眠（校验上限例如 ≤ 1s）。 |
| 重试语义 | **仅对 `URMA_EAGAIN`** 重试；其它 **`urma_status_t`** 立即失败（避免对 **`EINVAL`** 等错误死循环）。 |
| 截止时间 | 每次继续重试前使用 **`reqTimeoutDuration.CalcRealRemainingTime() <= 0`** 作为硬停（与 RPC deadline 对齐的真实剩余毫秒）；耗尽则 **`DeleteEvent`** 并返回 **`K_URMA_ERROR`**（文案标明 deadline）。 |
| 指标 | 新增 **`WORKER_URMA_WRITE_SPIN_LATENCY`** → Prometheus 名 **`worker_urma_write_spin_latency`**，Histogram，单位 **µs**；**仅当** 该分段发生过 **≥1 次 `URMA_EAGAIN`** 且最终 **post 成功** 时 **Observe 一次**（失败 / 超时路径不写入）。 |
| 既有指标 | **`WORKER_URMA_WRITE_LATENCY`**（`METRIC_TIMER`）仍覆盖该分段主要逻辑区间；**不在** write 重试的 `nanosleep` 上打 **`URMA_NANOSLEEP_LATENCY`**（该指标保留 id，**仅** JFC poll 线程原逻辑继续使用，避免与「post 重试」语义混淆）。 |

### 非目标

- 不修改 **read** 路径或其它 **`PostJettyRw`** 调用点的重试策略（可后续复用小块工具函数）。
- 不修改 **`OsXprtPipln::StartPipelineSender`** 路径。
- **不在非 URMA 构建**中编译 **`urma_manager`**（默认 Bazel **`common_rdma`** 不链接 URMA）；完整编译 **`//src/datasystem/common/rdma:urma_manager`** 仍需 **`enable_urma`** 与 URMA SDK 头文件环境。
- **URMA 数据面正确性 / EAGAIN 触发条件** 需在 **URMA 实机** 验收；通用 CI / 无设备远端无法替代。

---

## 建议的方案

### 配置层（gflags）

- **`ValidateUrmaSendJfsDepth`**：`[1, 65535]`（或与设备 cap 对齐的上限）。
- **`ValidateUrmaWriteSpinRetrySleepUs`**：`<= 1_000_000` µs；允许 **0**。

### 资源创建（`UrmaJetty::Create`）

```text
jfsConfig.depth = isSendJetty ? FLAGS_urma_send_jfs_depth : RECV_JETTY_JFS_DEPTH;
```

### 写路径（`UrmaManager::UrmaWriteImpl`）

对每个 **`remainSize` 分段**（对应一次 **`CreateEvent`** + 同一 **`key`**）：

1. **`CreateEvent`** 成功后进入 **复合语句块**（便于阅读 RAII 与 **break → 后继语句** 的先后关系）。
2. 块首 **`METRIC_TIMER(WORKER_URMA_WRITE_LATENCY)`**；**`Timer spinLatencyTimer`** + **`spinPhaseActive`**（初始 **false**）。
3. **`while (true)`**：调用 **`PostJettyRw`**（NUMA / 非 NUMA 分支保持原有逻辑）。
4. **`URMA_SUCCESS`**：若 **`spinPhaseActive`**（曾遇到 **`URMA_EAGAIN`**），则 **`Observe`** **`spinLatencyTimer`**（µs）→ **`WORKER_URMA_WRITE_SPIN_LATENCY`**；然后 **`break`**。
5. 若 **`ret != URMA_EAGAIN`**：**不 Observe spin** → **`DeleteEvent`** → **`RETURN_STATUS_LOG_ERROR`**。
6. 首次进入 **`URMA_EAGAIN` 分支**：置 **`spinPhaseActive=true`** 并对 **`spinLatencyTimer.Reset()`**（计时起点：**收到首次 EAGAIN 之后**，位于 deadline 检查与 **`nanosleep` 之前）。
7. 若 **`CalcRealRemainingTime() <= 0`**：**不 Observe** → **`DeleteEvent`** → deadline 文案 **`RETURN_STATUS_LOG_ERROR`**。
8. 否则按 **`FLAGS_urma_write_spin_retry_sleep_us`** 构造 **`timespec`** 并 **`nanosleep`**（**不打** **`URMA_NANOSLEEP_LATENCY`**）；循环继续。
9. 内层循环 **`break`** 后 **`VLOG` / `PerfPoint::Record` / 更新 `remainSize` / `eventKeys`**（成功路径无第二次 spin Observe）。

### `timespec` 与单位（便于代码评审）

**`sleepUs` 为微秒**；**`nanosleep`** 需要 **`tv_nsec` 为纳秒**：余数部分 **`(sleepUs % 1_000_000) * 1000`**。

---

## 涉及到的变更（文件级）

### `yuanrong-datasystem`

| 文件 | 改动摘要 |
|------|----------|
| `src/datasystem/common/util/gflag/common_gflags.h` | `DS_DECLARE_uint32(urma_send_jfs_depth)`、`urma_write_spin_retry_sleep_us` |
| `src/datasystem/common/util/gflag/common_gflag_define.cpp` | `DS_DEFINE_uint32` 与说明字符串 |
| `src/datasystem/common/util/gflag/common_gflags_validate.cpp` | 校验函数 + `DS_DEFINE_validator` |
| `src/datasystem/common/rdma/urma_resource.cpp` | send 分支 depth；含 **`common_gflags.h`**；常量注释 |
| `src/datasystem/common/rdma/urma_manager.cpp` | `UrmaWriteImpl` 重试、`kv_metrics`、`timespec` 注释 |
| `src/datasystem/common/metrics/kv_metrics.h` | **`WORKER_URMA_WRITE_SPIN_LATENCY`**（**`KV_METRIC_END` 前追加**，不重排既有 id） |
| `src/datasystem/common/metrics/kv_metrics.cpp` | **`MetricDesc`** 一行；**id 与枚举序号一致** |
| `src/datasystem/common/rdma/BUILD.bazel` | **`urma_manager`** → **`//src/datasystem/common/metrics:common_metrics`** |

### `URMA_NANOSLEEP_LATENCY`

- **枚举与 id=64 注册保留**（避免后续 metric id 整体移位）。
- **Write 重试路径不再 Observe**；**JFC poll** 线程 **`cnt == 0`** 分支维持原有 **`METRIC_TIMER(URMA_NANOSLEEP_LATENCY)`**（若仍存在）。

---

## 测试验证

### Bazel（非 URMA：冒烟编译依赖链）

在未开启 **`enable_urma`** 时，构建 **`common_rdma`**（不含 **`urma_manager`**）仍可验证 **gflags / kv_metrics** 等与 URMA 无关目标：

```bash
cd /path/to/yuanrong-datasystem
bazel build //src/datasystem/common/rdma:common_rdma \
  //src/datasystem/common/util/gflag:common_util_gflag_def \
  //src/datasystem/common/metrics:common_metrics
```

### Bazel（URMA：需 SDK 与 `enable_urma`）

```bash
bazel build --define enable_urma=true //src/datasystem/common/rdma:urma_manager
```

（以仓库 **`BUILD.bazel`** / **`select`** 条件为准；命令若变更以团队文档为准。）

### UT

```bash
bazel test //tests/ut/common/metrics:metrics_test --test_output=errors
```

确认 **`KV_METRIC_END`** 与 **`worker_urma_write_spin_latency`** 等断言仍成立。

### 集成 / 实机（必选用于数据面）

- 在 **URMA** 环境跑 worker-worker OC Put / 压力下：**浅 depth + 并发 post** 可能在「遇 EAGAIN 后仍成功」的请求上 **`worker_urma_write_spin_latency`** 出现样本。
- 对照 **`worker_urma_write_latency`**（分段整体）与 **`worker_urma_write_spin_latency`**（仅 **EAGAIN→成功** 子集）：后者 **计数** 通常 ≤ 前者分段次数。

---

## 兼容性 / 运维说明

- **新增 gflags**：部署需注意默认值（尤其 **`urma_send_jfs_depth=2`**）是否与线上容量匹配。
- **新增 Histogram**：监控侧注册 **`worker_urma_write_spin_latency`**（µs）。
- **行为变更**：遇 **`URMA_EAGAIN`** 时由「立即失败」变为「在 RPC 剩余时间内退避重试」，可能 **拉长墙钟** 但提高成功率；需在 URMA 环境观察 **超时** 与 **尾延迟**。

---

## 遗留与后续

1. Read 与其它 **`PostJettyRw`** 路径是否对齐重试策略：单独变更。
2. 若需 **`CalcRemainingTime()`（0.8 缩放）** 与 **`WaitFastTransportEvent`** 完全一致：可后续开关或统一封装。
3. **`URMA_NANOSLEEP_LATENCY`** 已无 write 路径样本：若团队决定废弃该指标名，需 **单独 RFC** 做 id 占位迁移策略（避免盲删枚举）。

---

## 期望的反馈

- Review **gflags 默认值**、**校验上限**、**deadline API**（**`CalcRealRemainingTime`** vs **`CalcRemainingTime`**）。
- Review **`worker_urma_write_spin_latency`** 与 **`worker_urma_write_latency`** 是否需在文档/SOP 中联读。
