# DataSystem Histogram P99 设计文档

## 1. 背景与目标

### 问题

**P99 histogram 缺失**：原有 `metrics` 的 Histogram 只记录 count / avg / max，无法满足 P99 分位点与尾延迟观测需求。

### 目标

- 在 Histogram 汇总中增加 **P99**（基于 20 个固定 bucket 的近似算法）
- JSON 中 `total` 与 `delta` 均携带 `p99` 字段，与监控侧对齐

---

## 2. P99 Histogram 设计

### 2.1 Bucket 划分

```cpp
constexpr std::array<uint64_t, 20> HIST_BUCKET_UPPER = {
    1, 2, 5, 10, 20, 50, 100, 200, 500, 1000,       // 1us ~ 1ms
    2000, 3000, 4000, 5000,                          // 1ms ~ 5ms
    10000, 20000, 50000, 100000, 1000000, 60000000   // 5ms ~ 60s
};
```

### 2.2 Bucket 索引

```cpp
inline size_t BucketIndex(uint64_t value) {
    auto it = std::upper_bound(HIST_BUCKET_UPPER.begin(), HIST_BUCKET_UPPER.end(), value);
    // 返回第一个 > value 的 bucket 上界
}
```

- `std::upper_bound` 找第一个 **>** value 的桶上界
- 与分位语义一致：例如 **`Observe(10)`** 时报告的分位可能落在 **20µs** 量级（桶上界），符合「超过 99% 的样本 ≤ 该上界」的表述，而非简单等于单次观测值 **10**

### 2.3 P99 计算算法

```cpp
uint64_t PercentileFromBuckets(const HistBuckets& buckets, uint64_t count,
                                uint32_t percentile, uint64_t overflowMax)
```

1. `target = (count * percentile + 99) / 100` — 目标排名
2. 遍历 bucket，累加计数，找第一个 `seen >= target` 的 bucket
3. 边界情况：
   - **count == 0** → 0
   - **count <= 10** → 返回 overflowMax（小样本不用插值）
   - **末桶且 overflowMax > 0** → 返回 overflowMax（真实最大值，而非桶上界）

### 2.4 Delta Histogram

```cpp
inline HistBuckets DeltaBuckets(const HistBuckets& curr, const HistBuckets& last)
```

- 周期内增量：`delta[i] = curr[i] >= last[i] ? curr[i] - last[i] : curr[i]`
- 应对计数器回绕 / 重置场景

### 2.5 Observe 与分桶

每次 `Histogram::Observe(us)` 须在 **`histMutex`** 下同步更新：

- 原有 `count` / `sum` / `max` / `periodMax`
- **`histBuckets[BucketIndex(us)]++`**

否则桶计数全零，P99 会错误退化为 max/兜底。详见 [implementation-plan.md](implementation-plan.md) §3。

---

## 3. JSON 输出格式

### 3.1 Histogram Metric 示例

```json
{
  "name": "test_histogram",
  "total": {"count": 1000, "avg_us": 50, "max_us": 200, "p99": 150},
  "delta": {"count": 100, "avg_us": 48, "max_us": 180, "p99": 140}
}
```

### 3.2 新增字段

- `total.p99` — 累计 P99
- `delta.p99` — 本周期 P99

仅增加字段，旧解析器可忽略未知键。

---

## 4. 测试

### 4.1 单元测试

`tests/ut/common/metrics/metrics_test.cpp`：

- `HistogramMetricJson` 增加 `totalP99`、`deltaP99`
- 更新所有 Histogram 相关断言

### 4.2 验证命令

```bash
bazel build //src/datasystem/common/metrics:metrics
bazel test //tests/ut/common/metrics/metrics_test --test_output=all
```

---

## 5. 文件变更清单（metrics）

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/datasystem/common/metrics/metrics.cpp` | 修改 | 桶数组、`PercentileFromBuckets`、`DeltaBuckets`、`BuildSummary` 输出 `p99`；`Observe`/`ClearAll` 维护桶 |
| `src/datasystem/common/metrics/metrics.h` | 修改 | `HIST_BUCKET_UPPER`、`HistBuckets`、`BucketIndex`、声明 |
| `tests/ut/common/metrics/metrics_test.cpp` | 修改 | `HistogramMetricJson` 与期望值 |

参考增量：`reference/metrics-p99-histogram-only.patch`（本 RFC 目录）。
