# Get metrics breakdown：实现与验证记录

## 代码改动摘要（yuanrong-datasystem）

- `kv_metrics`：id 13 重命名为 `WORKER_RPC_REMOTE_GET_OUTBOUND_*`；在 `URMA_IMPORT_JFR` 前增加 id 63–67（inbound、threadpool queue/exec、hashring、post query meta；URMA 仍用全进程 `worker_urma_*`）。
- `worker_worker_oc_api`：outbound timer。
- `worker_worker_oc_service_impl`：三处 inbound。
- `urma_manager`：`WaitToFinish` / `UrmaWriteImpl` 上沿用既有 `WORKER_URMA_WRITE_LATENCY` / `WORKER_URMA_WAIT_LATENCY` 埋点（**不再**维护独立 `remote_worker_urma_*` 指标 id；W↔W 对端与其它 URMA 路径共用同桶，见下「修订」；**不** 对 `UrmaWriteImpl` 做额外花括号作用域，与 Get breakdown 需求无关）。

**修订（与初版 RFC 差异）**：`remote_worker_urma_*` 已弃用，对端与全进程 **共用** `worker_urma_write_latency` / `worker_urma_wait_latency` 枚举与 Prometheus 名。
- `worker_oc_service_get_impl`：`Get()` 的 MsgQ/非 MsgQ 分支对 queue、exec、`WORKER_PROCESS_GET`（E2E）做 Observe；`ProcessObjectsNotExistInLocal` 在 `after_query_meta` 后 `post_query_meta_phase`。
- `worker_oc_service_impl::Get`：删除外层 `WORKER_PROCESS_GET` `METRIC_TIMER`（与 get_impl 内 E2E 一致语义避免双计/误导）。
- `etcd_cluster_manager::GetMetaAddressNotCheckConnection`：非 `IsCentralized()` 成功返回前对 hashring 路径 `Observe`。
- `tests/ut/common/metrics/metrics_test.cpp`：id13 新名与 JSON 断言。

## 行宽

- 已修正本次触及行中的超长（如 `kv_metrics.cpp` 原合并行拆行；`urma_manager` 保持与改 metrics 前一致的写法，未为计时器增加块作用域）。

## 验证

### 本地（WSL）

- 未安装 Bazel 7.4.1：`bazel test //tests/ut/common/metrics:metrics_test` 无法执行。

### 远程 xqyun-32c32g（`ssh xqyun-32c32g`）

- 仓库路径：`/root/workspace/git-repos/yuanrong-datasystem`。
- **本会话**：已将本机 `yuanrong-datasystem/` `rsync` 到上述路径后执行：

```bash
cd /root/workspace/git-repos/yuanrong-datasystem
bazel test //tests/ut/common/metrics:metrics_test
```

- 结果：**`PASSED`**（约 0.5s 测试时间，整次 `bazel test` 约 43s）。

### 集成 / Breakdown 树（按 RFC 验收）

- 对带 worker 指标日志的目录执行：

`yuanrong-datasystem-agent-workbench/scripts/metrics/grep_get_latency_breakdown.sh <logdir>`

- 保留含 `Generated: Get performance breakdown tree` 的完整输出作附件。

## Smoke / 集成 ST（`metrics_summary` + 打点 grep）

RFC [README 验收清单 §2](README.md#验收清单可勾选) 与 [issue-rfc 测试验证](issue-rfc.md#测试验证) 要求：**会触发 Get 的场景** 跑完后对日志执行 `grep_get_latency_breakdown.sh`。

### 1) Python smoke（主路径）

- 使用 **workbench/仓库** 中既有 **`run_smoke.py`** 等 **Python** 冒烟/集成把集群跑起来、触发 Put/Get 后，对产生的 **client / worker 日志根目录** 执行 §2 的 `grep_get_latency_breakdown.sh`。
- 在 worker 上确保开启 `-log_monitor=true` 或等价 gflags，使 `metrics_summary` 行出现。确认 JSON 中可见 `worker_process_get_latency`、`worker_rpc_remote_get_outbound_latency`、`worker_get_threadpool_exec_latency` 等新名时，以日志 grep/脚本样例行与 [issue-rfc 定界表](./issue-rfc.md) 对照即可；**不** 再维护 C++ 用例 `tests/st/client/kv_cache/kv_metrics_smoke_test.cpp`（已删除）。
- 单 worker 上 **queue** 仍可能为 0、**inbound** 无跨 worker 时无样本，属 RFC「未触发可为 0」。

### 2) 日志 + Breakdown 树

```bash
# LOG_ROOT 为集群根目录，含 worker0/log/*.INFO.log 等
bash yuanrong-datasystem-agent-workbench/scripts/metrics/grep_get_latency_breakdown.sh "$LOG_ROOT"
# 将末尾含 "Generated: Get performance breakdown tree" 整段重定向备查
# bash .../grep_get_latency_breakdown.sh "$LOG_ROOT" 2>&1 | tee get_breakdown_tree.txt
```

工作区里 **`bugfix/logs-0425`** 为 **旧** `metrics_summary` 行（如仍含 `worker_rpc_get_remote_object_latency`）；**新代码**跑出的日志应出现 **`worker_rpc_remote_get_outbound_latency`** 等名。

## 待办

- 在 Python smoke 可跑通的环境完成 **日志 + Breakdown 树** 一次，并把 `get_breakdown_tree.txt` 或等效摘录取证。
