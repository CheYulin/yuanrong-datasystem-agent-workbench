# /kind feature

**这是什么类型的 PR？**

/kind feature（可观测性增强；不改错误码、不改对外业务 API；指标名/语义有 breaking 面需迁移看板）

---

**这个 PR 做了什么 / 为什么需要**

与 [ZMQ TCP/RPC metrics](../2026-04-zmq-rpc-metrics/issue-rfc.md) 互补：ZMQ 层回答 **client↔worker 通信框架**；本专项回答 **ObjectCache 读（Get）在 worker 业务侧** 时间落在 **本进程线程池、寻址、Master 元数据、跨 worker 拉数、URMA 数据面** 的哪一截，支撑 **无全链路 trace** 时凭各进程 `metrics_summary` 做 **1～2 个可行动因** 的定界。

1. **修正误导性统计**  
   去掉 `WorkerOCServiceImpl::Get` 外层对 `WORKER_PROCESS_GET` 的过早计时，在 `WorkerOcServiceGetImpl::Get` 内将 **`worker_process_get_latency` 恢复为 entry worker 上 handle 的 E2E**（MsgQ 下与 queue+exec 量级一致；非 MsgQ 为 exec 段）。

2. **线程池可定界**  
   新增 **`worker_get_threadpool_queue_latency`**（仅 MsgQ：入队 → 回调首行）、**`worker_get_threadpool_exec_latency`**（`ProcessGetObjectRequest` 整段），慢时可区分 **调度/排队** 与 **业务执行**。

3. **跨 worker 不混桶**  
   将原 id=**13** 上混用的 `worker_rpc_get_remote_object_latency` 拆为：  
   - **`worker_rpc_remote_get_outbound_latency`**：本 worker 作为发起方调对端 `GetObjectRemote*`；  
   - **`worker_rpc_remote_get_inbound_latency`**：本 worker 作为被拉方处理入站 `GetObjectRemote*` / `BatchGetObjectRemote*`。

4. **元数据链路与后处理**  
   非 centralized 时 **`worker_get_meta_addr_hashring_latency`**（`GetMetaAddressNotCheckConnection`）；**`worker_get_post_query_meta_phase_latency`**（`QueryMetadataFromMaster` 成功后的本地后处理段）。

5. **URMA 与测试/工具**  
   实现上 **不** 再单独维护 `remote_worker_urma_*` 指标名；W↔W 拉数与全进程路径 **共用** `worker_urma_write_latency` / `worker_urma_wait_latency`（`UrmaWriteImpl` **不** 为本需求另改块作用域或计时时序）。Workbench 提供日志 grep 与 **定界总图** 脚本，RFC 与结果说明见同目录文档。

6. **测试可落地**  
   更新 `metrics` 单测中 id13/新名断言。集成/冒烟侧以 **Python smoke** 产物日志为准，用 `grep_get_latency_breakdown.sh` 对 `metrics_summary` 与定界树做验收（不维护 C++ `kv_metrics_smoke_test`）。

---

**基线与分支说明**

- **Datasystem 实现**应 rebase 到目标主干（`main/master` / 团队当前合并基线），与已有 `!586` 起 lightweight metrics 体系统一。
- **Workbench**：RFC 与 `scripts/metrics/grep_get_latency_breakdown.sh` 可与 datasystem 变更 **分仓合入**；排障时以 **同一版本** 的指标名为准。

---

**接口/兼容性影响**

- **无**对外 C++/Python **业务 API 签名**变化。
- **无**`StatusCode` / 协议字段变化。
- **有 breaking（监控/告警/采集）**  
  - 若看板/告警仍绑定 **字符串** `worker_rpc_get_remote_object_latency`：需改为 **`worker_rpc_remote_get_outbound_latency`**（入站请用 **`worker_rpc_remote_get_inbound_latency`** 另面板）。  
  - **`worker_process_get_latency` 语义变更**：历史曲线与“修正后 E2E”**不可**直接横比，需在发版说明中注明。
- Bazel/CMake 以仓库现况为准；第三方缓存请使用**持久** `DS_OPENSOURCE_DIR`（与团队 remote 构建约定一致）。

---

**主要代码变更（`yuanrong-datasystem`，对齐 IMPLEMENTATION_LOG）**

**新增/扩展**

- `src/datasystem/common/metrics/kv_metrics.h` / `kv_metrics.cpp`  
  - id=**13** 字符串/枚举：→ **`WORKER_RPC_REMOTE_GET_OUTBOUND_LATENCY`** / `worker_rpc_remote_get_outbound_latency`  
  - 在 `URMA_IMPORT_JFR` 与 `KV_METRIC_END` 之间 **追加** id **63–67**（inbound、threadpool queue/exec、hashring、post query meta 等，见 design 表；**不** 增加独立 `remote_worker_urma_*` id，与 [IMPLEMENTATION_LOG](./IMPLEMENTATION_LOG.md) 一致）
- `tests/ut/common/metrics/metrics_test.cpp`  
  - `KV_METRIC_END` 与 id13/JSON 名断言随重命名更新

**修改**

- `src/datasystem/worker/object_cache/worker_worker_oc_api.cpp`  
  - 远程 `GetObjectRemote`：**outbound** `METRIC_TIMER`
- `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp`  
  - 入站 `GetObjectRemote*` / `BatchGetObjectRemote*`：**inbound** `METRIC_TIMER`
- `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp`  
  - `Get()`：MsgQ/非 MsgQ 下 queue、exec、**`WORKER_PROCESS_GET`（E2E）**；`ProcessObjectsNotExistInLocal`：**post_query_meta** 段
- `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`  
  - 删除对 `getProc_->Get` 外层的误导 **`WORKER_PROCESS_GET`** 计时
- `src/datasystem/worker/cluster_manager/etcd_cluster_manager.cpp`  
  - `GetMetaAddressNotCheckConnection`：非 centralized 时 **hashring** `Observe`
- `src/datasystem/common/rdma/urma_manager.cpp`  
  - 沿用既有 `WORKER_URMA_WRITE_LATENCY` / `WORKER_URMA_WAIT_LATENCY` 埋点；**未** 为 Get breakdown 做额外花括号/直包含等改动。与 RFC 初稿差异：**未** 双写 `remote_worker_urma_*` 指标（见上）

**初版 design 中未采用 / 以 IMPLEMENTATION_LOG 为准**

- **`urma_metrics_peer.{h,cpp}` 与 `remote_worker_urma_{write,wait}_latency` 专桶**：一期 **不** 合入，避免与全进程 `worker_urma_*` 并维护两套名；定界时联读对端/本进程 `worker_urma_*` 与 Get 相关 outbound/inbound（见 [README](./README.md) 与 IMPLEMENTATION_LOG）。

---

**测试与脚本/文档交付（`yuanrong-datasystem-agent-workbench`）**

- 脚本：  
  - `scripts/metrics/grep_get_latency_breakdown.sh`（日志内 metric 样例行 + 末尾 **Generated: Get performance breakdown tree**；支持 `--tree-only`）
- 文档：  
  - `rfc/2026-04-worker-get-metrics-breakdown/README.md`  
  - `rfc/2026-04-worker-get-metrics-breakdown/design.md`  
  - `rfc/2026-04-worker-get-metrics-breakdown/issue-rfc.md`  
  - `rfc/2026-04-worker-get-metrics-breakdown/modification_plan.md`  
  - `rfc/2026-04-worker-get-metrics-breakdown/IMPLEMENTATION_LOG.md`  
  - `rfc/2026-04-worker-get-metrics-breakdown/results/README.md`（脚本输入/输出、样例：[`results/grep_get_latency_breakdown_sample.txt`](./results/grep_get_latency_breakdown_sample.txt)）

---

**最新验证结果（基于 IMPLEMENTATION_LOG / 可复跑）**

1. **远端 UT（Bazel，推荐）**（主机 **`xqyun-32c32g`**，路径以实际克隆为准）  
   - `bazel test //tests/ut/common/metrics:metrics_test`  
   - 结果：**PASSED**（实现记录中单次约数秒级 wall，含分析/缓存因素以现场为准）

2. **集成 Breakdown 树**  
   - `bash scripts/metrics/grep_get_latency_breakdown.sh <LOG_DIR>`  
   - 验收时保留含 **`Generated: Get performance breakdown tree`** 的整段输出，或重定向为 `get_breakdown_tree.txt`

3. **Python smoke + 集成日志**  
   - 以团队现有 `run_smoke.py` 等 **Python** 冒烟/集成跑出的 client/worker 日志目录为输入，跑 `grep_get_latency_breakdown.sh` 验收 `metrics_summary` 与定界树；单 worker 上 **queue** 可能为 0、无跨 worker 时 **inbound** 无样本，属 RFC「未触发可为 0」

4. **与 ZMQ 专项的回归关系**  
   - 本 PR **不** 改 ZMQ issue-rfc 已合入的 7 项流式时延语义；**联读** `client_rpc_get_latency`、`zmq_*` 与本专项 worker 分段做纵贯定界（见 [issue-rfc 定界树](./issue-rfc.md#性能-breakdownascii-树)）

---

**关联**

关联：Worker Get 路径时延可观测与定界（与 ZMQ metrics 正交互补）  
Fixes #<ISSUE_ID>

---

**建议的 PR 标题**

- **Datasystem**：`fix(metrics): worker Get latency breakdown and remote get in/out split`  
- **Workbench / 文档 only**：`docs(rfc): add worker Get metrics breakdown RFC and log grep tool`

（若单仓合入，可合并为一条：`fix(metrics): worker Get breakdown, outbound/inbound split, and workbench grep tool`）

---

**Self-checklist**

- [x] 不改 `StatusCode`，不改对外业务 API
- [x] `worker_process_get` 与 MsgQ queue/exec 语义与 RFC 一致；去掉外层误导 timer
- [x] id=**13** 改为 outbound 名；新增 inbound 与 63–67 分段 id（**无** 独立 `remote_worker_urma_*` 与初版双桶，以 IMPLEMENTATION_LOG 为准）
- [x] `bazel test //tests/ut/common/metrics:metrics_test` 在目标环境通过（见 IMPLEMENTATION_LOG）
- [x] 看板/告警迁移说明：`worker_rpc_get_remote_object_latency` → outbound/inbound 新名；`worker_process_get` 历史不可比
- [x] Workbench `grep_get_latency_breakdown.sh` 与 `results/README.md`、样例输出可对上
- [ ] 集成/ smoke 日志跑 `grep_get_latency_breakdown.sh` 并附 **`Generated: Get performance breakdown tree`** 段（或 `get_breakdown_tree.txt`）
