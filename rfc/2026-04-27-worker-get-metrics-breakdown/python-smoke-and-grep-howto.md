# Python Smoke 与 Get Breakdown 树：怎么跑、怎么对日志验收

目标：在 **`yuanrong-datasystem-agent-workbench`** 里用 **`run_smoke.py`** 拉起本机 **etcd + 多 worker + Python KVClient 客户端**，在 `results/smoke_test_<时间戳>/` 落盘 glog；再用 **`scripts/metrics/grep_get_latency_breakdown.sh`** 从日志中抽取与 Get 相关的 `metrics_summary` 样例，并**打印固定 ASCII 定界总图**（与 [issue-rfc 性能树](./issue-rfc.md#性能-breakdownascii-树) 一致，便于人工对照，**不是**从日志里自动“算”出来的图）。

---

## 1. 前置条件

| 项 | 说明 |
|----|------|
| 目录布局 | 脚本假定 **`yuanrong-datasystem` 与 `yuanrong-datasystem-agent-workbench` 为同级目录**（与 `run_smoke.py` 里 `DS_ROOT = WORKBENCH_ROOT.parent / "yuanrong-datasystem"` 一致）。 |
| Worker 可执行文件 | 已能解析到 `datasystem_worker`（如 `yuanrong-datasystem/build/bin/datasystem_worker` 或 bazel/whl 路径）。找不到时脚本会报 `Build with: cd $DS_ROOT && bash build.sh -t build`（并设置好持久化 `DS_OPENSOURCE_DIR` 等，按团队规范）。 |
| Python | 能 `from yr.datasystem.kv_client import KVClient`；脚本会自探测解释器。 |
| 本机服务 | 需有 **`etcd`**、**`etcdctl`** 可执行；smoke 会占 **127.0.0.1:2379** 及 **worker 端口 31501…**（数量由 `--workers` 定）。跑前会 `pkill` 残留 worker/etcd，**勿与已有集群端口冲突**。 |

验证与完整构建：团队通常在与代码一致的远程机（如 **`xqyun-32c32g`**）上跑；本机 WSL/笔记本若缺 etcd/whl 需自行补环境。

---

## 2. 如何运行 Python smoke

在 **`yuanrong-datasystem-agent-workbench` 内任意目录** 执行均可（脚本用自身路径解算 workbench 根）：

```bash
cd /path/to/yuanrong-datasystem-agent-workbench
python3 scripts/testing/verify/smoke/run_smoke.py --help
```

### 2.1 正式默认（较久、用于 ZMQ 7 项流式指标门控）

- 默认约 **4 workers**、**3 tenants × 2 clients**、**read-loop 120s**、**min ZMQ count 50** 等，墙钟**数分钟级**，用于保证 `zmq_client_queuing_latency` 等 7 项直方图 `count` 够大。

```bash
python3 scripts/testing/verify/smoke/run_smoke.py
```

- 开始时会打印本次 **`Log output: .../results/smoke_test_YYYYMMDD_HHMMSS`**，跑完 exit code **0** 表示：客户端成功 **且** ZMQ 流式 7 项在 `metrics_summary` 中达到 `--min-zmq-metric-count`。

### 2.2 快速试跑（更短、降低 ZMQ 门控；适合先打通环境）

脚本头部注释里给了示例（约 **30s 墙钟** 量级，按机器浮动）：

```bash
python3 scripts/testing/verify/smoke/run_smoke.py \
  --read-loop-sec 12 \
  --keys 80 \
  --tenants 2 \
  --clients-per-tenant 2 \
  --min-zmq-metric-count 5
```

### 2.3 常用参数

| 参数 | 含义 |
|------|------|
| `--workers` | worker 数量（默认 4），端口用脚本内 `WORKER_PORTS` 前 N 个。 |
| `--tenants` / `--clients-per-tenant` | 租户与每租户客户端数。 |
| `--read-loop-sec` | 每客户端**跨租户读循环**持续秒数，驱动 Get RPC 量。 |
| `--keys` | 每客户端 mset 的 key 数，影响预热数据量。 |
| `--min-zmq-metric-count` | 对 7 个 ZMQ 流式指标在 `metrics_summary` JSON 里的**最小** histogram `count` 门槛；**与 Get breakdown 无直接公式关系**，但跑不够可能 exit 1。 |
| `--inner-get-repeat` | 读循环内每轮对 sample keys 的 Get 轮数。 |
| `--log-monitor-interval-ms` | **worker 与 C++ 客户端**上 `log_monitor_interval_ms`（默认 **2000**；最小 500）。数值越小，同一墙钟内 `metrics_summary` 行数越多，便于在短 smoke 里看到指标；**过大**时若 `--read-loop-sec` 很短，整段只跨 **不到一个周期** 时可能 glog 里**还没有** JSON。跑完客户端后脚本会按该周期自动延长 `sleep` 再关进程，使最后一拍有机会落盘。 |

### 2.4 结果目录里有什么

以某次 `results/smoke_test_20260426_035405/` 为例（时间戳以你本机为准）：

| 路径 | 内容 |
|------|------|
| `workers/worker-31501/`（及 31502…） | 各 **worker 的 glog 根**（`--log_dir`）；内含 `*.INFO.log` / `*log.INFO*` 等；worker 已带 **`--log_monitor true` 与** **`--log_monitor_interval_ms`**（默认 2000ms，可用 **`--log-monitor-interval-ms`** 改），应出现含 **`"event":"metrics_summary"`** 的 JSON 行。 |
| `clients/` | 各客户端 glog 子目录、stdout 等。 |
| `metrics_summary.txt`、 `test_summary.json` | 脚本后处理生成的 **ZMQ 等**汇总（门控用）；**Get breakdown 验收仍以原始 glog + 下文 grep 脚本为主**。 |
| `etcd.log` | etcd 标准输出/错误。 |

---

## 3. 跑完后如何用 grep 脚本看 Get 与“性能 breakdown 树”

脚本路径（相对 workbench 根）：

`scripts/metrics/grep_get_latency_breakdown.sh`

### 3.1 对一次 smoke 结果整目录扫（推荐）

将 `<SMOKE_DIR>` 换成上一步的 `.../results/smoke_test_YYYYMMDD_HHMMSS`：

```bash
cd /path/to/yuanrong-datasystem-agent-workbench
bash scripts/metrics/grep_get_latency_breakdown.sh \
  "results/smoke_test_YYYYMMDD_HHMMSS/workers" \
  "results/smoke_test_YYYYMMDD_HHMMSS/clients"
```

也可只给**一个**大目录（若脚本能递归扫到 glog 文件即可）：

```bash
bash scripts/metrics/grep_get_latency_breakdown.sh "results/smoke_test_YYYYMMDD_HHMMSS"
```

> 注：该脚本会 `find` 符合后缀的日志文件；若某次目录结构仅含子目录，以实际是否扫到 `*.INFO` 为准。更稳妥是显式传 **`workers/worker-31501`** 与**客户端的 `.../glog_*/` 下带 `ds_client` 的 `*.INFO.log`**，与 [results/README.md](./results/README.md) 一致。

**远端**（与仓库约定一致时）可：

```bash
ssh xqyun-32c32g 'bash "$HOME/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/metrics/grep_get_latency_breakdown.sh" \
  "$HOME/workspace/git-repos/yuanrong-datasystem-agent-workbench/results/smoke_test_YYYYMMDD_HHMMSS/workers" \
  "$HOME/workspace/git-repos/yuanrong-datasystem-agent-workbench/results/smoke_test_YYYYMMDD_HHMMSS/clients"'
```

（路径按你机器上的 clone 调整。）

### 3.2 输出分三部分（如何“确认”）

1. **按指标名分节**  
   对脚本内置的 Get 相关 Prometheus 名（如 `client_rpc_get_latency`、`worker_process_get_latency`、`worker_rpc_remote_get_outbound_latency` 等）在**任意输入文件**里 **grep 命中**才打印：  
   `=== <metric_name> (sample lines) ===`  
   下面是最多 8 行**原文**样例，多为含 `metrics_summary` 的整行 JSON。  
   若某名全程未出现，**该节省略**（不报错；可能路径未扫到、或本进程未有样本/未开 log_monitor/周期未到）。

2. **提示行**  
   `Per-metric grep done. See JSON lines with metrics_summary or name/count/avg in samples above.`

3. **固定 ASCII 定界总图（你要对的“树状结构”）**  
   以**固定标题**开头（**用于验收是否跑完整脚本、是否可归档**）：

   ```text
   ================================================================================
   Generated: Get performance breakdown tree (RFC 2026-04-worker-get-metrics-breakdown)
   ================================================================================
   ```

   其下是 [issue-rfc](./issue-rfc.md) 中 **Client / Entry / Peer** 角色与**指标名**的参考树，**不依赖**上一步样例行是否齐；用于排障时**对照**各进程上的 metric 名与调用关系。  
   **只想要这一段、不扫日志**时：

   ```bash
   bash scripts/metrics/grep_get_latency_breakdown.sh --tree-only
   ```

3.3 **落盘**（PR/RFC 附件）

```bash
bash scripts/metrics/grep_get_latency_breakdown.sh "results/smoke_test_YYYYMMDD_HHMMSS" \
  2>&1 | tee rfc/2026-04-worker-get-metrics-breakdown/results/samples/get_breakdown_grep_$(date +%Y%m%d).txt
```

保留下文含 **`Generated: Get performance breakdown tree`** 的整段即可与 [验收清单](./README.md#验收清单可勾选) 对齐。

### 3.4 与 ZMQ 专项的关系

`run_smoke.py` 的 exit 0 主要关 **ZMQ 流式 7 项**的样本量。  
**Get breakdown** 各 histogram（`worker_get_threadpool_*`、`worker_rpc_remote_get_*` 等）是否都有非零 `count`，取决于读路径、MsgQ 是否开、是否跨 worker 等，**不**与 ZMQ 门控一一对应；验收时以 **glog 中是否出现指标名** + **定界总图** 为人工/脚本辅助标准。

---

## 4. 样例

本目录已有一份基于某次 `run_smoke.py` 输出跑脚本的全文样例，便于对照版式：  
[results/grep_get_latency_breakdown_sample.txt](./results/grep_get_latency_breakdown_sample.txt)  
更细致的输入/输出说明见 [results/README.md](./results/README.md)。

---

## 5. 常见问题

- **`grep_get_latency_breakdown.sh` 没有某几个 metric 的节**  
  该次日志里**没有**出现对应字符串（或目录未扫到 glog）。先 `rg 'metrics_summary' -l results/.../workers` 看 worker 是否打印 JSON。

- **想确认树的内容对不对**  
  树是脚本**内嵌的固定文本**；`--tree-only` 输出与扫日志后**末尾**一段应一致。差异只可能来自**脚本版本**；应以仓库内 `grep_get_latency_breakdown.sh` 的 `print_breakdown_tree` 为准。

- **smoke 未跑 datasystem 新代码**  
  确保 `find_worker_binary()` 选中的 `datasystem_worker` 是你要验证的构建产物（`build` / bazel / whl 路径与版本一致）。

- **跑完 `grep_get_latency_breakdown.sh` 没有任何 `=== ... ===` 样例行，或 stderr 报「no log files matched」**  
  1. **工作目录**：相对路径 `results/...` 是相对于**当前 shell 所在目录**的。请先在 **`yuanrong-datasystem-agent-workbench` 根目录**执行，或把参数改成**绝对路径**，例如 `$(pwd)/results/smoke_test_.../workers`。  
  2. **glog 文件名**：脚本会收 `*.INFO.log`、以及 Google glog 常见的 `*log.INFO*` / `*.log.INFO.*` 等；若你仍扫不到，用 `find results/smoke_test_... -type f` 看实际文件名，可把手册列出的**单个 glog 文件**作为参数直接传给脚本。  
  3. **本脚本的「性能结果」分两类**：`=== <metric> (sample lines) ===` 是日志里**真实命中**的样例；**末尾**的 **ASCII 定界总图**（`Generated: Get performance breakdown tree`）是**固定模板**，只要脚本跑通就会打印。若你误以为「没有树」，请**滚到终端最底部**或 `2>&1 | tee out.txt` 再 `tail out.txt`；**只要树在，说明脚本执行完整**，只是可能未扫到任何文件、或指标名在日志里未出现。  
  4. 新版本脚本在 stderr 会打一行 **`grep_get_latency_breakdown: scanning N file(s)`**，`N=0` 时优先检查目录与相对路径。
