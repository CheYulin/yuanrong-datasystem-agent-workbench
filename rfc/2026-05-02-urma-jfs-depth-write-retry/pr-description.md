# /kind feature

**这是什么类型的 PR？**

/kind feature（URMA worker 数据面可调参 + 容错；新增 Histogram；默认 depth 变小可能影响吞吐，属有意可调风险）

---

## 这个 PR 做了什么 / 为什么需要

在 URMA **worker → worker（Object Cache）write** 路径上：

1. **Send jetty JFS depth 可配置**：新增 **`urma_send_jfs_depth`**（默认 **2**），仅作用于 **`UrmaJetty::Create`** 中 **send** 分支的 **`jfsConfig.depth`**；recv 深度与 recv 侧 JFR 常量策略不变。
2. **Post 遇 `URMA_EAGAIN` 可退避重试**：在 **`UrmaWriteImpl`** 内循环 **`PostJettyRw`**；仅 **`URMA_EAGAIN`** 在 **`reqTimeoutDuration.CalcRealRemainingTime() > 0`** 时 **`nanosleep`** 后继续；其它返回码立即失败；deadline 耗尽返回 **`K_URMA_ERROR`** 并 **`DeleteEvent`**。
3. **退避间隔可配置**：新增 **`urma_write_spin_retry_sleep_us`**（默认 **50**，单位 **µs**；允许 **0**；校验上限 **1_000_000** µs）。
4. **可观测**：新增 **`WORKER_URMA_WRITE_SPIN_LATENCY`**（**`worker_urma_write_spin_latency`**，Histogram **µs**），对每个 write **分段**在 **post 成功**或 **失败退出**（非 EAGAIN / deadline）记录一次耗时。
5. **不打 `URMA_NANOSLEEP` 在 write 重试**：避免与 poll 线程 **`URMA_NANOSLEEP_LATENCY`** 语义混淆；**poll 线程原 `METRIC_TIMER(URMA_NANOSLEEP_LATENCY)` 保留**。

**为什么需要**：浅 JFS / 设备侧 transient 资源不足时，`EAGAIN` 直接失败会放大尾延迟与失败率；需要 **可观测的 post 重试代价** 与 **可运维的 depth / sleep** 旋钮。UMDK bond 路径在部分场景返回 **`URMA_EAGAIN`**，与本重试语义对齐。

---

## 接口 / 兼容性影响

| 类别 | 影响 |
|------|------|
| 对外业务 API | **无** 签名变更 |
| `StatusCode` | **无** 新枚举（仍用既有 **`K_URMA_ERROR`** 等承载 post / deadline 失败） |
| 默认行为 | **`urma_send_jfs_depth` 默认 2**（原硬编码 send depth 通常为 256 量级）：可能影响 **并发未完成 post 数** 与 **吞吐**，需按环境覆盖 flag |
| 行为语义 | **`URMA_EAGAIN`** 下由 **立即失败** 变为 **在 RPC 剩余时间内重试**：可能改变 **尾延迟分布** 与 **错误率** |
| 监控 | 新增 **`worker_urma_write_spin_latency`**；**`URMA_NANOSLEEP_LATENCY`** id **保留**，write 路径不再写入 |

---

## 主要代码变更（`yuanrong-datasystem`）

| 文件 | 摘要 |
|------|------|
| `src/datasystem/common/util/gflag/common_gflags.h` | Declare 两个 uint32 flag |
| `src/datasystem/common/util/gflag/common_gflag_define.cpp` | Define 默认值与 help |
| `src/datasystem/common/util/gflag/common_gflags_validate.cpp` | Range validators + register |
| `src/datasystem/common/rdma/urma_resource.cpp` | Send depth → **`FLAGS_urma_send_jfs_depth`** |
| `src/datasystem/common/rdma/urma_manager.cpp` | **`UrmaWriteImpl`** 重试 + **`WORKER_URMA_WRITE_SPIN_LATENCY`**；**`timespec`** 注释 |
| `src/datasystem/common/metrics/kv_metrics.h` / `kv_metrics.cpp` | 追加 **`WORKER_URMA_WRITE_SPIN_LATENCY`**（**末尾 id**，不重排旧 id） |
| `src/datasystem/common/rdma/BUILD.bazel` | **`urma_manager`** 依赖 **`common_metrics`** |

---

## 测试与验证

| 层级 | 命令 / 说明 |
|------|-------------|
| Metrics UT | `bazel test //tests/ut/common/metrics:metrics_test` |
| 非 URMA 编译 | `bazel build //src/datasystem/common/rdma:common_rdma //src/datasystem/common/util/gflag:common_util_gflag_def //src/datasystem/common/metrics:common_metrics` |
| URMA 编译 | 需 **`enable_urma`**（或等价）+ SDK：`//src/datasystem/common/rdma:urma_manager` |
| 数据面 | **须在 URMA 实机** 做 Put / 压力与 **metrics_summary** 对照（通用远端/CI 不可替代） |

第三方缓存构建请仍使用持久 **`DS_OPENSOURCE_DIR`**（团队 CMake 约定）。

---

## 关联

- Workbench RFC：[issue-rfc.md](./issue-rfc.md) · [README](./README.md)
- Fixes #`<ISSUE_ID>`

---

## 建议的 PR 标题

`feat(urma): configurable send JFS depth, EAGAIN retry on write post, spin latency histogram`

---

## Self-checklist

- [ ] **`urma_send_jfs_depth` / `urma_write_spin_retry_sleep_us`** 默认值与说明已与运维对齐
- [ ] **`UrmaWriteImpl`**：**仅 `URMA_EAGAIN`** 重试；deadline 检查；错误路径 **`DeleteEvent`**
- [ ] **`worker_urma_write_spin_latency`**：**终态 Observe**（成功 / 非 EAGAIN / deadline）
- [ ] Write 重试 **`nanosleep`**：**未** 使用 **`METRIC_TIMER(URMA_NANOSLEEP_LATENCY)`**；poll 线程侧保留既有用法
- [ ] **`metrics_test`** 通过；非 URMA **Bazel** 至少编译 **`common_rdma` + gflags + metrics**
- [ ] URMA 实机 **`metrics_summary`** 或等价导出中出现 **`worker_urma_write_spin_latency`**（验收截图或日志节选）
