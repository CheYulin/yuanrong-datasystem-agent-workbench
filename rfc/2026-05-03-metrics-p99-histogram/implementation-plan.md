# 实施计划：Histogram P99（metrics）落入 `yuanrong-datasystem`

本文面向 **`common/metrics`**：在 Histogram 的 JSON 汇总里增加 **`p99`**。参考 `design.md`、`verification.md`、`reference/yuanrong-datasystem-p99-only.patch` 与 `reference/yuanrong-datasystem-p99-only.md`。

---

## 0. 路径：`tests/` 不是 `src/tests/`

单测在仓库根下 **`tests/`**（例如 `tests/ut/common/metrics/metrics_test.cpp`）。

---

## 1. 文件清单

| 文件 | 作用 |
|------|------|
| `src/datasystem/common/metrics/metrics.h` | `HIST_BUCKET_UPPER`、`HistBuckets`、`BucketIndex`、`PercentileFromBuckets` 声明 |
| `src/datasystem/common/metrics/metrics.cpp` | 桶、`BuildSummary` 输出 `p99`、**`Observe` 写桶**、**`ClearAll` 清桶** |
| `tests/ut/common/metrics/metrics_test.cpp` | `HistogramMetricJson`、`p99` 断言、`PercentileFromBuckets` gtest |
| `tests/st/common/metrics/histogram_p99_perf_test.cpp` | 吞吐与并发读写冒烟（原生 **`cc_test`**，较重，`size = "large"`） |
| `tests/st/common/metrics/BUILD.bazel` | 注册 `histogram_p99_perf_test` |
| `tests/ut/common/metrics/BUILD.bazel` | `metrics_test` 等 UT |

---

## 2. 应用 patch

在 `yuanrong-datasystem` 根目录：

```bash
git apply --check /path/to/rfc/2026-05-03-metrics-p99-histogram/reference/yuanrong-datasystem-p99-only.patch
git apply /path/to/rfc/2026-05-03-metrics-p99-histogram/reference/yuanrong-datasystem-p99-only.patch
```

再根据 §3 补上 patch 未包含的 **`Observe`/`ClearAll`** 逻辑（参考 patch 只加了 `BuildSummary` 侧读桶）。

---

## 3. 为何要补 `Observe` / `ClearAll`

P99 依赖 **`histBuckets`** 上的分布；必须在每次 **`Histogram::Observe(us)`** 中执行 **`histBuckets[BucketIndex(us)]++`**（与现有 `histMutex` 同锁），并在 **`ClearAll`**（及槽位重置）中把 **`histBuckets` 清零**。否则桶全零，分位数计算会错误退化为 max/兜底；简单 UT 可能仍绿，混合样本会错。详见 §3 数据流（原 §3 详细说明保留在下方）。

### 3.1 现有 Histogram 未保存分布

当前主干 `Observe` 只维护 count、sum、max、periodMax，没有「落在哪个延迟区间」的计数。

### 3.2 P99 需要什么

固定桶 + `Observe` 时 **`histBuckets[i]++`**；`BuildSummary` 在 **`histMutex`** 下读桶并调用 `PercentileFromBuckets` / `DeltaBuckets` 得到 `total.p99` / `delta.p99`。

### 3.3 参考 patch 的缺口

部分历史 patch 为 slot 增加 `histBuckets` 并在 `BuildSummary` 中算 P99，但 **不含** 对 `histBuckets` 的写入与清理；合并前必须在 **`Observe`** 与 **`ClearAll`** 中补上。当前 **`yuanrong-datasystem-p99-only.patch`** 已含实现；若使用较旧子集 patch，仍须人工核对。

### 3.4 线程安全

`BuildSummary` 对 Histogram 已持 **`slot.histMutex`**；`Observe` 亦在同锁内更新即可。

---

## 4. 质量闸门与远端全量

```bash
bash yuanrong-datasystem-agent-workbench/scripts/lint/check_cpp_line_width.sh
bazel test \
  //tests/ut/common/metrics/... \
  //tests/st/common/metrics:histogram_p99_perf_test \
  --define=enable_urma=false --test_output=errors
# 或本机执行 RFC：bash rfc/.../scripts/bazel_run_tests.sh
```

远端（参考 `2026-04-30-zmq-rpc-queue-latency` 拆段）：`scripts/rsync_datasystem.sh`、`scripts/bazel_build.sh`、`scripts/bazel_run_tests.sh`；一键顺序执行用 `scripts/run_full_tests_remote.sh`。默认 **`REMOTE=root@xqyun-32c32g`**，可用环境变量覆盖。
