# RFC: Worker Get 时延可观测与分段（含 Client / 本地 / 远端 breakdown）

- **Status**: **Draft**
- **Started**: 2026-04-26
- **Goal（性能定位与定界）**:
  - **定位**：在 avg/max 上区分慢段落在 client、本 worker 线程池、QueryMeta、寻址、跨 worker RPC、对端 URMA 等**哪一截**。
  - **定界**：通过 **不混桶的 metric 名** + **进程角色**（client / entry / peer），在无全链路 trace 时仍能对照各进程 `metrics_summary`，将问题**收敛到 1～2 个可行动因**（见 [issue-rfc.md](./issue-rfc.md) 定界表）。
  - 在 **尽量少改代码** 的前提下完成上述可观测，并给 **Bazel 单测 + 日志** 的验收；Workbench 提供 **一个 grep 脚本**（[`grep_get_latency_breakdown.sh`](../../scripts/metrics/grep_get_latency_breakdown.sh)）。

**修改计划（分阶段、可合并/可小步 MR）**：[modification_plan.md](./modification_plan.md)

---

## 一句话

- **Client**：继续用现成 `client_rpc_get_latency` 表示读 E2E；验收时在 client 日志的 `metrics_summary` 中核对 **count/avg/max**。
- **本地 entry worker**：修正 MsgQ 下 `worker_process_get_latency` 严重低估；补充 **threadpool queue / exec** 两段；**保持** `worker_rpc_query_meta_latency`；将混用的 **remote get** 拆成 **outbound / inbound** 两个直方图；补上 **hashring 寻址** 与 **QueryMeta 之后** 两段（均为新增 histogram，不插入已有 id）。
- **对端 data worker**：`remote_worker_urma_write/wait` 仅在 **Worker→Worker 拉数数据面** 与通用 `worker_urma_*` 同时采样；`worker_rpc_query_meta` **不** 在 RFC 一期拆「专指对端拉数链路的 meta」（见下「非目标」），验收时以 **对端进程** 上的 `worker_rpc_query_meta_latency` 为参考。

详案见 [design.md](./design.md)。

---

## 与目标的映射

| 你的目标 | RFC 落点（metric 名 / 行为） |
|----------|------------------------------|
| Client 读 E2E avg/max | `client_rpc_get_latency`（不改名） |
| 本地：线程池入队→唤醒 | 新增 `worker_get_threadpool_queue_latency`（仅 MsgQ 路径 Observe） |
| 本地：线程内执行完 | 新增 `worker_get_threadpool_exec_latency` + 修正 `worker_process_get_latency` 为 handle E2E |
| 本地：QueryMeta | 已有 `worker_rpc_query_meta_latency` |
| 本地：原 `worker_rpc_get_remote_object` 混用 | id 13 字符串改为 **outbound** 名，新增 **inbound** id（见 design） |
| 远端：inbound/URMA/「meta」 | inbound + `remote_worker_urma_*`；**一期** 不新增 `remote_worker_query_meta` |
| 快速 grep 分析 | Workbench 脚本，见 design §脚本 |

---

## 非目标（刻意缩小范围）

- **不** 改 ZMQ/客户端 stub 的 queue flow 7 项（与现有 ZMQ RFC 正交）。
- **不** 在 Master 上新增 Get 专项 metric（本 RFC 只动 worker 侧 + 必要 common）。
- **不** 为「对端上仅因本次 GetObject* 而发生的 QueryMeta」单开 histogram（对端主路径常不调 QueryMeta；开桶需大改 `WorkerRemoteMasterOCApi` 或全链路打标，**超出最小改动**）；文档中写清用 **对端进程** 的 `worker_rpc_query_meta_latency` 作侧写。
- **不** 用 `git clean` 或大范围重构 perf / tick。

---

## 验收清单（可勾选）

1. **单元测试（优先 Bazel 增量/缓存，效率更高）**：在 `yuanrong-datasystem` 根目录执行 `bazel test //tests/ut/common/metrics:metrics_test`（或 `--test_output=all` 对标 `ctest -V`）。目标对应源文件 `tests/ut/common/metrics/metrics_test.cpp`；断言 `GetKvMetricDescs` 与 `KV_METRIC_END` 一致、**新增/改名** 的 name 在 `DumpSummaryForTest` 中正确。若无 Bazel 环境，可退回 `cmake` 构建后 `ctest -R metrics_test`（见 [design.md §7](./design.md#7-验收与回归)）。
2. **日志验收（本地或 `xqyun-32c32g`）**：跑会触发 Get 的场景（如现有 smoke 或 st），在 glog 中 `grep metrics_summary` 或脚本输出，确认：
   - `client_rpc_get_latency` 有非零 count（client 侧）；
   - 开启 MsgQ 的 worker 上 `worker_get_threadpool_queue_latency` 与 `worker_get_threadpool_exec_latency` 有样本，`worker_process_get_latency` 与 **queue+exec 量级一致**（允许计时边界误差，不作为严格等式）；
   - 跨 worker 时 **outbound** 与对端 **inbound** 均有样本；对端 **URMA 数据面** 与全进程混桶在 **`worker_urma_write_latency` / `worker_urma_wait_latency`**（已取消独立 `remote_worker_urma_*` 名，见 [IMPLEMENTATION_LOG](./IMPLEMENTATION_LOG.md)）；
   - `worker_get_meta_addr_hashring_latency` / `worker_get_post_query_meta_phase_latency` 在对应路径被触发时非零（未触发可为 0，文档说明）。
3. **Workbench 脚本**：执行 [`scripts/metrics/grep_get_latency_breakdown.sh`](../../scripts/metrics/grep_get_latency_breakdown.sh)；对日志目录 `grep` 各 metric 样例行，并在输出**末尾生成与 [issue-rfc.md](./issue-rfc.md) 中「性能 Breakdown（ASCII 树）」一致的定界总图**（以 `Generated: Get performance breakdown tree` 开头）。**验收**须保留该段（或重定向为 `get_breakdown_tree.txt`）。仅生成树：`.../grep_get_latency_breakdown.sh --tree-only`。

> 第 2 点具体命令见 [design.md §7](./design.md#7-验收与回归)。

---

## 相关文档

- [python-smoke-and-grep-howto.md](./python-smoke-and-grep-howto.md)：**如何运行 `run_smoke.py`、结果目录结构、以及对日志跑 `grep_get_latency_breakdown.sh` 确认定界总图。**
- [results/README.md](./results/README.md)：**`grep_get_latency_breakdown.sh` 输入/输出**、结果长什么样、留档重定向、示例命令。
- [issue-rfc.md](./issue-rfc.md)：背景、**定位/定界**目标、Layer 表、**性能 Breakdown ASCII 树**、定界决策树、变更与验证。
- [modification_plan.md](./modification_plan.md)：**分阶段修改计划**（0～6 阶段）、每阶段验收、合并/回滚与验证主机。
- [design.md](./design.md)：指标表、**最小**文件 diff、时间线、breaking change、回滚、Bazel 验收命令；**附录**含 Mermaid **树状时间线**图（逻辑树 + 三进程并行）。

---

## 状态更新

- 实现合并 datasystem 与脚本合并 workbench 后，将本 **Status** 置为 **Done** 并更新 `rfc/README.md` 表。
