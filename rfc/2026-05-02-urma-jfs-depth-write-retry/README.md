# URMA send JFS depth（gflags）、write post 重试与 spin 时延可观测

## Status

**In-Progress**（datasystem 侧改动已在分支落地；URMA 数据面需在实机验收）

## 一句话

通过 **gflags** 配置 send jetty **JFS depth**（默认 2）与 **`URMA_EAGAIN`** 退避间隔（默认 50µs）；在 **`UrmaWriteImpl`** 内按 **`reqTimeoutDuration::CalcRealRemainingTime()`** 截止重试 **post send WR**；新增 **`worker_urma_write_spin_latency`**（Histogram，µs）度量单次分段 post 直至成功或失败退出的耗时。

## 文档索引

| 文件 | 说明 |
|------|------|
| [issue-rfc.md](./issue-rfc.md) | 背景、方案、变更清单、验证与不兼容说明 |
| [pr-description.md](./pr-description.md) | PR / MR 标题与正文模板（可复制） |

## Datasystem 落点（摘要）

| 区域 | 路径 |
|------|------|
| Gflags | `common/util/gflag/common_gflags.{h}`, `common_gflag_define.cpp`, `common_gflags_validate.cpp` |
| Jetty JFS depth | `common/rdma/urma_resource.cpp`（仅 send 分支用 flag） |
| Write post 重试 + spin 指标 | `common/rdma/urma_manager.cpp`（`UrmaWriteImpl`） |
| 指标注册 | `common/metrics/kv_metrics.{h,cpp}`（`WORKER_URMA_WRITE_SPIN_LATENCY`，id 顺延） |
| Bazel | `common/rdma/BUILD.bazel`：`urma_manager` 显式依赖 `common_metrics` |

## 关联

- UMDK 参考：`bondp_post_send_wr_and_store` 在 WR 缓冲不足等场景返回 **`URMA_EAGAIN`**（资源暂不可用）。
- Workbench 走读笔记：[2026-04-urma-jfs-cqe-error9-walkthrough.md](../2026-04-urma-jfs-cqe-error9-walkthrough.md)（主题相近，非同一缺陷）。
