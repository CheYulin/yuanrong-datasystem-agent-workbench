# /kind feature

**这是什么类型的 PR？**

/kind feature（可观测性增强：Histogram **JSON 汇总**增加 **P99**；不改变 metric id、不改变 `Observe(us)` 调用方入参；对仅解析 count/avg/max 的采集端 **向后兼容**）

---

**这个 PR 做了什么 / 为什么需要**

本变更扩展 **通用 `datasystem::metrics` Histogram 框架** 的 **汇总字段**（`metrics_summary` 中带 **P99**），与「在业务路径上新增某条 Histogram」类改动 **正交**：后者增加 **埋点条目**，前者让 **每一条** Histogram 在 JSON 里多一个尾延迟维度，避免只知 avg/max 时误判尾部分布。

1. **固定桶近似 P99**  
   为每个 Histogram slot 维护 **`HistBuckets[20]`**；桶上界覆盖 **1µs～60s**（低延迟段更密），与 Issue/RFC 中 `HIST_BUCKET_UPPER` 表一致。

2. **Observe 与汇总一致**  
   在 **`Histogram::Observe`** 内、**`histMutex`** 下对 **`histBuckets[BucketIndex(us)]++`**；**`BuildSummary`** 在同锁内读桶并计算 **`total.p99`** / **`delta.p99`**。  
   **注意**：若仅应用不完整 patch（缺 **Observe 写桶** / **`ClearAll` 清桶**），简单 UT 可能仍通过，**混合样本**下 P99 会错——合入前须代码审查上述两点。

3. **Delta 与周期 max**  
   **`DeltaBuckets`** 做桶差分并处理回绕；**`delta.p99`** 的 overflow 使用 **`periodMax.exchange(0)`**。

4. **边界策略**  
   小样本（**`count <= 10`**）、末桶溢出等按 Issue/RFC 约定：小样本返回 overflowMax；末桶且 overflowMax 有效时返回真实 max。

5. **测试**  
   更新 **`//tests/ut/common/metrics:metrics_test`** 中 **`HistogramMetricJson`** 与断言；建议后续补 **混合样本** UT（Issue/RFC「测试验证」节）。

---

**基线与分支说明**

- **Datasystem** 应 rebase 到目标主干（`main` / `master` / 团队当前合并基线），与既有 lightweight metrics 体系统一。
- 若设计说明、patch、脚本放在 **另一仓库或附件**，请在 MR 中一并说明路径或附件名；评审以 **本 MR 代码 diff** 为准。

---

**接口/兼容性影响**

- **无**对外 C++/Python **业务 API 签名**变化。
- **无**`StatusCode` / RPC 协议字段变化。
- **无**`KvMetricId` **枚举移位**或已有指标 **改名**（本 PR 动的是 **框架层汇总 JSON**）。
- **监控/采集侧**：Histogram 每条多两个标量字段 **`total.p99`**、**`delta.p99`**；仅依赖旧三字段的解析器 **可继续使用**；若强类型反序列化 **拒绝未知键**，需放宽或升级（属消费方适配）。

---

**主要代码变更（`yuanrong-datasystem`）**

**修改**

- `src/datasystem/common/metrics/metrics.h`  
  - `HIST_BUCKET_UPPER`、`HistBuckets`、`BucketIndex`、`PercentileFromBuckets` 声明
- `src/datasystem/common/metrics/metrics.cpp`  
  - `PercentileFromBuckets`、`DeltaBuckets`  
  - `MetricSlot` / `LastSnapshot` 增加 **`histBuckets`**（及必要的 **`last` 同步**）  
  - **`Histogram::Observe`**：**桶自增**  
  - **`ClearAll`**（或等价重置路径）：**桶清零**  
  - **`BuildSummary`**：输出 **`p99`**
- `tests/ut/common/metrics/metrics_test.cpp`  
  - `HistogramMetricJson(..., totalP99, ..., deltaP99)` 与全部相关用例

**初版 patch 中需人工合并项**

- 若参考 patch **可能未包含**完整的 **Observe/ClearAll** 块，以实现仓库 **本 MR 实际 diff** 为准。

---

**测试与交付物**

- **必选**：`bazel test //tests/ut/common/metrics:metrics_test`（及团队要求的 C++ 行宽/格式检查）。
- **可选**：远端或 CI 上 `bazel test //tests/...` 全量日志，并注明 **commit SHA**。
- 设计说明、验证记录、仅 metrics 的 `.patch`、远端跑测脚本等：由团队决定放在本仓子目录或另一仓；**本 PR 描述不依赖具体相对路径**。

---

**最新验证结果（可复跑）**

1. **UT（Bazel）**（主机与路径以实际环境为准）  
   - `bazel test //tests/ut/common/metrics:metrics_test --test_output=errors`  
   - 结果：以实现时 **`PASSED`** 为准；填写本 PR 时附上 **commit SHA**。

2. **行宽**  
   - 团队若有 `check_cpp_line_width` 或同类脚本，应对 **本次变更 C++** 无违规行。

3. **远端全量**（可选）  
   - 在约定环境执行全量测试；在 PR 描述或评论中附上日志路径或 **通过/失败摘要**。

第三方缓存：远程构建请使用团队约定的持久依赖目录（如 **`DS_OPENSOURCE_DIR`**）。

---

**关联**

- 对应 Issue：**#<ISSUE_ID>**（请替换为实际编号；无则删除本行）

---

**建议的 PR 标题**

- **Datasystem**：`feat(metrics): add approximate p99 to histogram summary`  
- **仅文档/附件**：`docs: histogram P99 RFC attachment`（按需）

（若单仓合入：`feat(metrics): histogram summary p99 + docs`）

---

**Self-checklist**

- [ ] **`Histogram::Observe`** 在 **`histMutex`** 下对 **`histBuckets`** 递增；**`ClearAll`**（或重置）清零桶
- [ ] **`BuildSummary`** 在 **`histMutex`** 下读桶；**`total`/`delta`** JSON 含 **`p99`**
- [ ] `bazel test //tests/ut/common/metrics:metrics_test` 通过
- [ ] 行宽/格式检查通过（若团队启用）
- [ ] （可选）混合样本 UT，防止桶未更新回归
- [ ] （可选）远端 `//tests/...` 全量日志与 **commit SHA** 一并附上
