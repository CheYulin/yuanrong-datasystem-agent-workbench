# [RFC]：Histogram 汇总增加 P99（固定桶近似分位数）

## 背景与目标描述

`datasystem::metrics` 中 **Histogram** 的周期汇总 JSON 目前仅输出 **`count` / `avg_us` / `max_us`**，无法表达 **尾延迟（P99）**，与大盘/排障对分位指标的需求不对齐。本 RFC 在**不重排既有 metric id** 的前提下，在 **通用 Histogram 框架**（`GetHistogram` → `BuildSummary` → `metrics_summary`）内增加 **近似 P99**：

- 使用 **20 个固定上界桶**（1µs～60s，中间段在 0～5ms 更密），桶上界数组与索引方式见下文「建议的方案」。
- 在 **`total` 与 `delta`** 对象中各增加字段 **`p99`**（单位与现有字段一致：**µs**）。
- **`delta.p99`** 基于周期内 **桶计数差分**（`DeltaBuckets`）与 **`periodMax`**，并处理计数器回绕/重置场景。

**非目标**：

- 不承诺 **数学精确** P99（固定桶 + 桶内插值为工程近似）。
- 不在本 RFC 内修改 **ZMQ/RPC 业务埋点**、**`kv_metrics` 注册表语义** 或 **单条业务 Histogram 的测量定义**（仅增强通用 Histogram **汇总形态**）。
- 不为「客户端从 JSON 重算真实分位」提供原始样本或细粒度桶导出（维持轻量 summary）。

**范围说明**：在「给哪些业务加 Histogram」类需求之外，本 RFC 只扩展 **框架侧汇总字段**；与「新增/拆分某条业务 Histogram」类变更 **正交**，可独立评审与合入。

---

## 建议的方案

### 数据面：每次 Observe 维护桶分布

| 动作 | 说明 |
|------|------|
| `Histogram::Observe(us)` | 在现有 **`histMutex`** 下，除 `count/sum/max/periodMax` 外，执行 **`histBuckets[BucketIndex(us)]++`** |
| `BucketIndex(us)` | `std::upper_bound` 对 `HIST_BUCKET_UPPER`：第一个 **>** `us` 的桶上界对应下标 |
| `ClearAll` / 槽位重置 | 将 **`histBuckets`** 清零，避免测试或进程内复用时脏读 |

### 汇总面：BuildSummary 输出 P99

| 步骤 | 说明 |
|------|------|
| 读快照 | 在现有 **`histMutex`** 保护下读取 **`histBuckets`**（与 `count/sum/max` 同锁，与主干一致） |
| `total.p99` | `PercentileFromBuckets(currBuckets, count, 99, max)`，`max` 为累计最大值 |
| `delta.p99` | `DeltaBuckets(curr, lastSnapshot)` 得周期桶；`PercentileFromBuckets(deltaBuckets, dCount, 99, dMax)`，`dMax` 来自 **`periodMax.exchange(0)`** |
| 小样本 / 末桶 | `count <= 10` 时返回 **overflowMax**；末桶且 **overflowMax > 0** 时返回真实 **max**，避免用桶上界冒充真峰值 |

### 桶上界（20 档，单位 µs）

与实现中 `HIST_BUCKET_UPPER` 一致，覆盖 1µs～60s：

`1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 3000, 4000, 5000, 10000, 20000, 50000, 100000, 1000000, 60000000`。

### JSON 形态（示意）

```json
{
  "name": "worker_rpc_query_meta_latency",
  "total": {"count": 1000, "avg_us": 50, "max_us": 200, "p99": 150},
  "delta": {"count": 100, "avg_us": 48, "max_us": 180, "p99": 140}
}
```

### 参考 patch 与合入注意

- 可使用 **仅含 `common/metrics` 与 `metrics_test` 的 git patch**（例如命名为 `metrics-p99-histogram-only.patch`）作为增量基线。
- **重要**：若 patch 只改了槽位与 `BuildSummary`、**未** 在 **`Histogram::Observe`** 里对桶计数 **+1**、**未** 在 **`ClearAll`**（或等价重置）里 **清零桶**，则 **P99 会错误**；简单 UT（全员同延迟、极少样本）仍可能通过，需在合入前做代码审查，并建议增加 **混合样本** UT（多数低价、少数 outlier，期望 P99 接近主体而非恒等于 max）。

---

## 涉及到的变更

### 修改文件（`yuanrong-datasystem`）

| 文件 | 改动说明 |
|------|----------|
| `src/datasystem/common/metrics/metrics.h` | `HIST_BUCKET_UPPER`、`HistBuckets`、`BucketIndex`、`PercentileFromBuckets` 声明 |
| `src/datasystem/common/metrics/metrics.cpp` | `PercentileFromBuckets`、`DeltaBuckets`；`MetricSlot`/`LastSnapshot` 持桶；`BuildSummary` 追加 **`p99`**；**`Observe` 写桶**；**`ClearAll` 清桶** |
| `tests/ut/common/metrics/metrics_test.cpp` | `HistogramMetricJson` 增加 `totalP99`/`deltaP99`；更新各 Histogram 断言 |

### 不变项

- **不** 修改 `KvMetricId` 枚举顺序或既有 id 的 Prometheus 名字（本变更在 **framework 层**）。
- **不** 修改 Counter/Gauge 的 JSON 形态。
- **不** 要求单次 Observe 的调用方改为传额外参数（仍为 **µs 整数**）。

---

## 测试验证

### UT（Bazel，推荐）

```bash
export DATASYSTEM_ROOT=/path/to/yuanrong-datasystem
cd "$DATASYSTEM_ROOT"
# 可选: export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
bazel test //tests/ut/common/metrics:metrics_test --test_output=errors
# 全输出: --test_output=all
```

### 行宽自检

若团队对 C++ 行宽有约定（例如每行 ≤120 列），在含变更的文件上执行既有 lint 脚本，确保无超长行。

### 构建验证（按需）

```bash
cd "$DATASYSTEM_ROOT"
bazel build //src/datasystem/common/metrics:common_metrics --jobs=8
```

### 远端全量（按需）

在约定构建机上对已同步的 `yuanrong-datasystem` 执行例如 `bazel test //tests/...`（参数与并行度以团队为准），并保留带 **commit SHA** 与时间的日志便于复现。

### 可选回归用例（建议）

- 增加 **混合样本** UT（例如绝大多数样本落在低价桶、少数 outlier）：期望 **P99 接近主体**而非简单等于 **max**，防止「桶未更新」类退化。

---

## 遗留事项（待人工决策）

1. **大盘/采集**：消费方是否需显式注册 **`p99` 字段**；旧解析器忽略未知键则 **无 breaking**。
2. **与 Grafana/Prom 的映射**：若下游将 summary JSON 转为多序列，需约定 **P99 的 series 名**（本 RFC 不强制具体字符串）。
3. **Bazel 版本**：以仓库 `.bazelversion` 与团队约定为准。

## 期望的反馈时间

- 建议反馈周期：**5～7 天**。
- 重点反馈：
  1. **20 桶边界** 是否满足当前 **RPC/IO** 尾延迟观测习惯，是否需在 **ms 以上**再调整粒度（另提小改即可）。
  2. **`count <= 10` 返回 max** 的保守策略是否接受，还是希望小样本也走插值。

---

## 文档与状态

- 本文为 **独立可张贴** 的 Issue/RFC 正文；设计细化、验证表与 patch 文件名可由项目侧附录或 MR 描述补充，不依赖外部文档链接。
