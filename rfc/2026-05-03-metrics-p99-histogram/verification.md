# P99 Histogram 实现验证文档

**日期**: 2026-05-03  
**代码路径**: `yuanrong-datasystem`  
**状态**: `metrics_test` 与 ST 下 **`histogram_p99_perf_test`（`ds_cc_test`，轻量依赖、无 `st_common`）** …

---

## 1. 修改的文件

| 文件 | 改动 |
|------|------|
| `src/datasystem/common/metrics/metrics.h` | `HIST_BUCKET_UPPER[20]`、`HistBuckets`、`BucketIndex`、`PercentileFromBuckets` |
| `src/datasystem/common/metrics/metrics.cpp` | `PercentileFromBuckets`、`DeltaBuckets`、`BuildSummary` 输出 `p99`；`Histogram::Observe` 桶自增；`ClearAll` 清桶 |
| `tests/ut/common/metrics/metrics_test.cpp` | `HistogramMetricJson` 含 `p99`；`percentile_from_buckets_*` gtest；并发/均匀分布/跨窗口用例 |
| `tests/st/common/metrics/histogram_p99_perf_test.cpp` | 性能与并发冒烟（**`ds_cc_test`**，**`TEST_F`**） |
| `tests/st/common/metrics/BUILD.bazel` | **`ds_cc_test`**: `histogram_p99_perf_test`，`timeout = "long"` |

---

## 2. Patch 与说明文件

RFC 目录 `rfc/2026-05-03-metrics-p99-histogram/reference/`：

- **`yuanrong-datasystem-p99-only.patch`** — 推荐：metrics 实现 + UT + ST perf 二进制。
- **`yuanrong-datasystem-p99-only.md`** — 设计与命令说明。

---

## 3. Bucket 与 P99 要点（及测试覆盖）

设计要点与 **`metrics_test` / ST perf** 的对应关系如下（实现见 `metrics.cpp` 中 `PercentileFromBuckets`）：

| 要点 | 覆盖方式 |
|------|----------|
| 桶上界与 `std::upper_bound`（严格大于 sample） | `percentile_from_buckets_*` 手动铺桶；`histogram_concurrent_observe_test`（全 10 → P99 20）；ST perf Test 1/2/3 |
| **count ≤ 10** 走 **overflowMax**（小样本不插值） | `percentile_from_buckets_sparse_two_values_returns_overflow_max`；`histogram_observe_test`（JSON）；`percentile_from_buckets_single_ten` / `single_thirty` |
| **末桶** 且 **overflowMax > 0** 返回真实 max | `percentile_from_buckets_overflow_bucket_uses_overflow_max` |
| **count = 0**（`PercentileFromBuckets`） | `percentile_from_buckets_empty` |
| 未 Observe 的 histogram 不出现在 JSON 汇总 | `histogram_empty_test`（`summary` 中无 `test_histogram`） |
| **count > 10** 桶内线性插值 | `percentile_from_buckets_even_split_interpolation`；`histogram_p99_total_even_split_test`；`histogram_concurrent_observe_test`；`histogram_p99_total_and_delta_across_windows_test` |
| **JSON** 中 total/delta **p99** 与 **DeltaBuckets** | `writer_histogram_delta_test`；`histogram_p99_total_and_delta_across_windows_test`；各 KV histogram 用例中带 **p99** 的 `HistogramMetricJson` |

未单独用“纯单测”钉死、但通过集成路径覆盖的：例如 **exactly count = 10** 与 **count = 11** 在 `count<=10` 分支边界上的行为——当前由 **count=2**、**count=1** 与 **count=100** 等用例间接围住；若需防回归可再加命名用例。

---

## 4. 验证命令（远端示例）

工作目录以远端克隆为准（常见为 `~/workspace/git-repos/yuanrong-datasystem`）。

```bash
bazel test //tests/ut/common/metrics/... //tests/st/common/metrics:histogram_p99_perf_test --define=enable_urma=false --test_output=all
# ST 吞吐等 std::cout 仅在通过时也会打印；若只要失败详情可用 --test_output=errors
# 或直接跑可执行文件:
# ./bazel-bin/tests/st/common/metrics/histogram_p99_perf_test
```

RFC 脚本 **`bazel_run_tests.sh`** 分两次 **`bazel test`**（先 UT 包、再 ST 单目标），避免单次命令加 **`--keep_going`** 时 ST **分析失败**被静默跳过。默认 **`BAZEL_TEST_OUTPUT=all`**。若需安静日志可 **`BAZEL_TEST_OUTPUT=errors bash scripts/bazel_run_tests.sh`**。脚本结束会把远端 **`bazel_test_*_<STAMP>.log`**（workbench **`results/`** 或 **`/tmp/`** 兜底）**`rsync`** 到本 RFC **`results/`**（**`LOCAL_RESULTS`** 可覆盖）。

---

## 5. 期望摘要（表格 + ST perf）

### 5.1 `metrics_test`：`PercentileFromBuckets` 直接调用

| 用例 | 预期 |
|------|------|
| `percentile_from_buckets_sparse_two_values_returns_overflow_max` | P99 = **30** |
| `percentile_from_buckets_single_ten` | **10** |
| `percentile_from_buckets_single_thirty` | **30** |
| `percentile_from_buckets_overflow_bucket_uses_overflow_max` | **100000000** |
| `percentile_from_buckets_empty` | **0** |
| `percentile_from_buckets_even_split_interpolation` | **50** |

### 5.2 `metrics_test`：Histogram JSON / 集成

| 用例 | 预期（节选） |
|------|----------------|
| `histogram_observe_test` | 两样本 10+30 → total/delta **p99 = 30** |
| `histogram_empty_test` | 未 Observe → 汇总中无 `test_histogram` |
| `histogram_concurrent_observe_test` | 全 10、大样本 → **p99 = 20** |
| `writer_histogram_delta_test` | delta 路径含 **p99** |
| `histogram_p99_total_even_split_test` | 50×10 + 50×30 → total **p99 = 50** |
| `histogram_p99_total_and_delta_across_windows_test` | 跨 window total/delta **p99 = 20**，max 可达 **100** |
| KV 系列 histogram 测试（`kv_metrics_*` 等） | 各 `HistogramMetricJson(..., p99, ...)` 与 **p99=10** 等 |

### 5.3 ST `histogram_p99_perf_test`（`TEST_F`）

| `TEST_F` | 预期（节选） |
|----------|----------------|
| `FixedValue100usLockContention` | count 与 **P99 = 199** |
| `RandomValuesFourBuckets` | **P99 ∈ [10000, 20000]** |
| `MultiThreaded99x10Plus1x10000` | count **100**，**P99 = 20** |
| `ConcurrentObserveAndDump` | dump 后 count **≤** 已 Observe 次数 |

任一 **`TEST_F`** 中 **`EXPECT_*` / `ASSERT_*`** 失败则该用例失败，`bazel test` 报告失败。

---

## 6. 注意

- ST **`histogram_p99_perf_test`**：**`ds_cc_test`**，**`TEST_F`**；依赖 **flags / common_log / common_metrics / file_util**（**无** **`//tests/st:st_common`**）。
- P99 桶自检已合并进 **`metrics_test`**，直接调用 `metrics::PercentileFromBuckets`，不再单独维护 `p99_verify_test.cpp`。
