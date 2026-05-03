# P99 Histogram Metrics 实现说明

## 1. 变更范围

仅包含 P99 histogram 相关修改，不含 ZMQ metrics 修正。

## 2. 核心设计

### 2.1 Bucket 定义

```cpp
constexpr std::array<uint64_t, 20> HIST_BUCKET_UPPER = {
    1, 2, 5, 10, 20, 50, 100, 200, 500, 1000,       // 1us ~ 1ms
    2000, 3000, 4000, 5000,                            // 1ms ~ 5ms
    10000, 20000, 50000, 100000, 1000000, 60000000    // 5ms ~ 60s
};
```

### 2.2 BucketIndex — upper_bound 语义

```cpp
inline size_t BucketIndex(uint64_t value) {
    auto it = std::upper_bound(HIST_BUCKET_UPPER.begin(), HIST_BUCKET_UPPER.end(), value);
    // 返回第一个 > value 的桶上界
}
```

- `Observe(10)` → bucket index 3 → upper=20 → 在 count 足够大且走插值时 P99 可落到 20（不是 10）
- `std::upper_bound` 确保严格大于，找到最小上界

### 2.3 P99 计算

```cpp
uint64_t PercentileFromBuckets(const HistBuckets& buckets, uint64_t count,
                                uint32_t percentile, uint64_t overflowMax)
```

1. `target = (count * percentile + 99) / 100` — 目标排名
2. 遍历累加找第一个 `seen >= target` 的 bucket
3. 线性插值：`lower + (position * bucketWidth / bucketCount)`（整数形式见实现）
4. 小样本（<=10）：返回 `overflowMax`（histogram 近似对小样本不可靠）
5. 落在最后一桶且 `overflowMax > 0`：返回 `overflowMax`（真实最大值已知）

### 2.4 Delta Histogram

```cpp
inline HistBuckets DeltaBuckets(const HistBuckets& curr, const HistBuckets& last)
```

- 周期内增量：`delta[i] = curr[i] >= last[i] ? curr[i] - last[i] : curr[i]`
- 处理 counter reset 回绕场景

## 3. 数据结构变更

### MetricSlot（per-metric 存储槽）

```cpp
struct alignas(64) MetricSlot {
    // ... 原有字段 ...
    HistBuckets histBuckets{};  // 新增
    std::mutex histMutex;
};
```

### LastSnapshot（dump 快照）

实现上在 `metrics.cpp` 内联定义：`u64Value`、`i64Value`、`sum`、**`histBuckets`**。  
`max` 仍由 slot 的原子字段维护，**不**镜像到 `LastSnapshot`（与早期草稿不同）。

## 4. JSON 输出格式

```json
{
  "name": "test_histogram",
  "total": {"count": 100, "avg_us": 20, "max_us": 30, "p99": 50},
  "delta": {"count": 50, "avg_us": 20, "max_us": 30, "p99": 50}
}
```

## 5. 测试用例

### 5.1 metrics_test.cpp：`PercentileFromBuckets` 直接调用

与 `metrics.cpp` 同一符号（含 count<=10 与末桶 `overflowMax`），gtest 用例名 `percentile_from_buckets_*`。

| Case | 输入 | 预期 P99 | 说明 |
|------|------|---------|------|
| 1 | 10 + 30 两样本 | 30 | 稀疏：走 overflowMax（非裸插值 50） |
| 2 | 仅 10 | 10 | 单值 |
| 3 | 仅 30 | 30 | 单值 |
| 4 | 100000000 | 100000000 | 末桶 + overflowMax |
| 5 | empty | 0 | 边界 |
| 6 | 50×10 + 50×30，n=100 | 50 | count>10 时 (20,50] 桶插值 |

### 5.2 tests/st/common/metrics/histogram_p99_perf_test.cpp + BUILD

原生 **`cc_test`**（非 `ds_cc_test`），路径 **`//tests/st/common/metrics:histogram_p99_perf_test`**，`size = "large"`；自带 **`main()`**，失败时返回非 0，便于 **`bazel test`**。RFC 脚本 **`bazel_run_tests.sh`** 与 UT 一并调用 **`bazel test`**。

| Test | 场景 | 验证 |
|------|------|------|
| 1 | 16线程 × 1M × Observe(100) | count 与 P99=**199**（(100,200] 桶大样本插值，非 200） |
| 2 | 16 线程轮询 10/100/1000/10000 | P99 约在 **10000..20000**（99p 落入 10ms 档插值） |
| 3 | 99×10 + 1×10000 | P99=**20**（(10,20] 桶插值） |
| 4 | 并发 observe+dump 2秒 | 无 crash，count 合理 |

### 5.3 metrics_test.cpp（集成与其它）

| Test | 说明 |
|------|------|
| histogram_observe_test | 基础 Observe；两样本时 P99=30（稀疏） |
| histogram_empty_test | 空 histogram |
| histogram_concurrent_observe_test | 并发；P99 插值 → **20** |
| writer_histogram_delta_test | Delta 适配 |
| histogram_p99_total_even_split_test | 50×10 + 50×30 → P99 **50** |
| histogram_p99_total_and_delta_across_windows_test | 跨 window total/delta 一致 |

## 6. 性能影响

| 项目 | 影响 |
|------|------|
| 内存 | +192 bytes/slot（1024 slots → +192KB） |
| Observe() | +1× upper_bound + 1×数组写 |
| 锁竞争 | 不变（沿用现有 histMutex） |

## 7. 文件清单

| 文件 | 操作 |
|------|------|
| `src/datasystem/common/metrics/metrics.h` | HistBuckets、BucketIndex、PercentileFromBuckets |
| `src/datasystem/common/metrics/metrics.cpp` | 实现 + Observe 桶自增 + ClearAll 清桶 |
| `tests/ut/common/metrics/BUILD.bazel` | `metrics_test` 等 UT |
| `tests/ut/common/metrics/metrics_test.cpp` | P99 集成 + `PercentileFromBuckets` gtest |
| `tests/st/common/metrics/BUILD.bazel` | **`cc_test`**: `histogram_p99_perf_test` |
| `tests/st/common/metrics/histogram_p99_perf_test.cpp` | ST 性能/并发冒烟 |

## 8. 使用方式

在仓库根目录 `yuanrong-datasystem`：

```bash
bazel test \
  //tests/ut/common/metrics/... \
  //tests/st/common/metrics:histogram_p99_perf_test \
  --define=enable_urma=false --test_output=errors
# 或直接运行用例二进制:
# ./bazel-bin/tests/st/common/metrics/histogram_p99_perf_test
```

RFC 下 **`scripts/bazel_run_tests.sh`** 会在远端执行与上式等价的 **`bazel test`**（日志 tee 到 `results/`）。

## 9. 参考 patch

同目录 `yuanrong-datasystem-p99-only.patch` 为上述文件的汇总 diff，便于 `git apply`（注意与目标分支基线冲突时需手工合并）。
