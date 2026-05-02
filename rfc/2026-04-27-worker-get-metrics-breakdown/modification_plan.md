# 修改计划：Get 时延可观测（性能定位/定界）

> 目标：按 **依赖顺序** 最小化合并冲突与返工；每阶段**可独立** `bazel test //tests/ut/common/metrics:metrics_test`（改 metrics 后应立即绿）。

## 阶段 0：基线

- 分支自主干；确认当前 `//tests/ut/common/metrics:metrics_test` 通过。

## 阶段 1：`kv_metrics` 与单测名同步

**改动**：

- `kv_metrics.h` / `kv_metrics.cpp`：id=13 重命名为 **outbound**（枚举名 + 字符串 `worker_rpc_remote_get_outbound_latency`）；在 `URMA_IMPORT_JFR` 前 **追加** 新 id：`inbound`、`threadpool_queue`、`threadpool_exec`、`hashring_meta_addr`、`post_query_meta_phase`、`remote_worker_urma_write`、`remote_worker_urma_wait`（名称与 [design.md §2](./design.md#2-指标表name--时间线位置) 表一致）。
- `tests/ut/common/metrics/metrics_test.cpp`：更新 `KV_METRIC_END` 相关断言、id13 相关字符串/JSON 用例。

**验收**：`bazel test //tests/ut/common/metrics:metrics_test`。

**定界价值**：无运行时行为；为后续打点开始。

---

## 阶段 2：Remote get outbound / inbound 分流

**改动**：

- `worker_worker_oc_api.cpp`：`WORKER_RPC_REMOTE_GET_OUTBOUND`。
- `worker_worker_oc_service_impl.cpp`：三处改为 **inbound**。

**验收**：同阶段 1 单测；可选手工 grep 全仓无旧枚举名（若已删）。

**定界价值**：跨 worker 时**发起端/目的端** histogram 不混桶。

---

## 阶段 3：Get 线程池 + `process_get` 语义修正

**改动**：

- `worker_oc_service_get_impl.cpp`：`Get()` 内 queue/exec/E2E Observe；**同步/MsgQ 分支**都覆盖；修正 `process_get` 为 handle E2E。
- `worker_oc_service_impl.cpp`：移除 `getProc_->Get` 外层的 **误导** `WORKER_PROCESS_GET` timer（避免双计或错误时序）。

**验收**：单测 + 有 MsgQ 的集成日志中 `queue+exec` 与 `process_get` 量级一致（允许小误差）。

**定界价值**：**定位**慢在池排队还是业务；与 client 做差时不再被「仅到入队」欺骗。

---

## 阶段 4：HashRing + post-QueryMeta

**改动**：

- `etcd_cluster_manager.cpp`：`GetMetaAddressNotCheckConnection` 非 centralized 路径 Observe `hashring`。
- `worker_oc_service_get_impl.cpp`：`ProcessObjectsNotExistInLocal` 内 `after_query_meta` 后 `post_query_meta_phase` Timer。

**验收**：在会触发该路径的 st/环境中样本非 0；未触发为 0 可接受。

**定界价值**：**定界** Master 前路由 vs 元数据已返回后本地重逻辑。

---

## 阶段 5：Remote data URMA 双桶

**改动**：

- 新增 `urma_metrics_peer.{h,cpp}`；`common/rdma/CMakeLists.txt` 加入 `FAST_TRANSPORT` 源列表。
- `worker_worker_oc_service_impl.cpp`：`GetObjectRemoteImpl` 入口与 `WaitFastTransportAndFallback` 前 **Scope**。
- `urma_manager.cpp`：`UrmaWriteImpl` / `WaitToFinish` 在 `worker_urma_*` 后条件再记 `remote_worker_urma_*`（`#ifdef USE_URMA` 与现有块一致）。

**验收**：有远程 URMA 时，对端日志中 `remote_worker_urma_*` 与 `worker_urma_*` 可对照；无则均为 0。

**定界价值**：**定界**「普遍 URMA 慢」vs「仅 worker-worker 拉数面慢」。

---

## 阶段 6：Workbench 脚本与文档收口

**改动**：

- 确认 [`grep_get_latency_breakdown.sh`](../../scripts/metrics/grep_get_latency_breakdown.sh) 的 pattern 与最终 **Prometheus 名**一致；脚本在扫日志**后**须输出 [issue-rfc 性能 Breakdown ASCII 树](issue-rfc.md#性能-breakdownascii-树)（`--tree-only` 仅打树）。

**验收**：对一次集成日志目录运行脚本，能命中新名并打印样例行；**输出末尾含完整 Breakdown 树**（可保存为验收附件）。

**定界价值**：排障**效率** + **验收可归档的定界总图**；非代码路径。

---

## 合并与回滚

- 推荐 **1 个 MR** 或按阶段 1→2→3… **多个小 MR**（易 review）；每阶段后主干保持可测。
- 回滚：revert 对应 commit；Grafana/告警若绑 **字符串** `worker_rpc_get_remote_object_latency` 的需改为 **outbound** 新名见 [design §5](./design.md#5-breaking-change-与文档)。

---

## 验证主机

- 与仓库约定：构建与完整验证优先在 **`xqyun-32c32g`**；`DS_OPENSOURCE_DIR` 独立持久化目录，不放进每次删的 `build/`（与团队 remote 构建习惯一致即可）。
