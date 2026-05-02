# `grep_get_latency_breakdown.sh`：输入、输出与结果长什么样

脚本路径：[`../../../scripts/metrics/grep_get_latency_breakdown.sh`](../../../scripts/metrics/grep_get_latency_breakdown.sh)。

**样例输出（已落盘）**：[grep_get_latency_breakdown_sample.txt](./grep_get_latency_breakdown_sample.txt)（在 `xqyun-32c32g` 上对 `run_smoke.py` 结果目录 `smoke_test_20260426_035405` 的 worker-31501 + 客户端 `INFO` 日志跑脚本得到的全文）。

**重要**：脚本**只向标准输出（stdout）打印**，默认**不**在磁盘上创建结果文件。需要留档时在本机对 stdout 做重定向，例如 `bash ... > get_breakdown_grep.txt 2>&1`。

---

## 1. 输入

### 1.1 命令行

```text
bash grep_get_latency_breakdown.sh <DIR|FILE> [<DIR|FILE> ...]
bash grep_get_latency_breakdown.sh --tree-only
```

- **必须**至少提供**一个**参数（`--tree-only` 除外）：可以是**目录**或**文件**的混用。
- **目录**：递归收集以下后缀/名（`find`）：
  - `*.log`、`*.INFO`、`*.txt`、`worker*.log`、`*.INFO.log`
- **文件**：直接作为搜索对象（不必符合上述通配名）。

### 1.2 常见输入从哪来

| 场景 | 典型路径示例 |
|------|----------------|
| Python `run_smoke.py` | `results/smoke_test_<时间戳>/workers/worker-<port>/`（worker glog 目录） |
| 同上，客户端 C++ glog | `results/smoke_test_<时间戳>/client_glog_t*_c*_ds_client_*.INFO.log` 或 `.../clients/glog_*/ds_client_*.INFO.log` |
| C++ ST（Embedded cluster） | 集群根下 `worker0/log/datasystem_worker.INFO.log` 等 |
| 多 worker 定界 | 把 **entry / peer** 的 worker 目录或 `INFO` 文件**都**作为参数传入，一次扫全 |

**远端**（`xqyun-32c32g`）上若 workbench 在 `~/workspace/git-repos/yuanrong-datasystem-agent-workbench/`，`results/` 在 workbench 树内。

### 1.3 单条完整示例（SSH 远端一次跑完）

将 `smoke_test_<时间戳>` 换成你的结果目录名：

```bash
ssh xqyun-32c32g 'bash "$HOME/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/metrics/grep_get_latency_breakdown.sh" \
  "$HOME/workspace/git-repos/yuanrong-datasystem-agent-workbench/results/smoke_test_YYYYMMDD_HHMMSS/workers/worker-31501" \
  "$HOME/workspace/git-repos/yuanrong-datasystem-agent-workbench/results/smoke_test_YYYYMMDD_HHMMSS/client_glog_t0_c0_ds_client_<pid>.INFO.log"'
```

已登录远端时，在 shell 中去掉最外层 `ssh ...`。

---

## 2. 输出在哪里、长什么样

### 2.1 位置

- **终端 stdout**：所有内容。
- **无默认落盘**；重定向示例：
  - `... > rfc/2026-04-worker-get-metrics-breakdown/results/samples/get_breakdown_grep.txt`

### 2.2 结构（自上而下）

1. **按指标名的若干节（仅当该名在**任一**输入文件里出现过）**  
   对内置列表中的每个 name（如 `client_rpc_get_latency`、`worker_process_get_latency`、…），若 `grep`/`rg` 有命中，则输出：

   ```text
   === <metric_name> (sample lines) ===
   <path>:<line_no>:<整行内容，常为含 "metrics_summary" 的 JSON 行>
   ... 最多 8 行样例 ...
   ```

   若某指标名在**所有**给定日志里都不出现，**该名整节不会出现**（不是报错）。

2. **结束提示行**  
   `Per-metric grep done. See JSON lines with metrics_summary or name/count/avg in samples above.`

3. **固定 ASCII 定界总图**（与 `issue-rfc.md` 中树一致；由脚本内 `print_breakdown_tree` 生成）  
   以如下标题开头：

   ```text
   ================================================================================
   Generated: Get performance breakdown tree (RFC 2026-04-worker-get-metrics-breakdown)
   ================================================================================
   ```

   随后是 **Client / Entry worker / Peer worker** 的 metric 名与角色说明，用于人工对照，**不是**从日志自动解析出的数据。

### 2.3 `--tree-only`（不扫日志）

```bash
bash grep_get_latency_breakdown.sh --tree-only
```

**只**打印第 2.2 节第 3 步的 **ASCII 定界总图**，不做任何 `grep`；用于对名字或定界图做复制粘贴，**0 个输入文件**。

### 2.4 与工具链的关系

- 若已安装 **ripgrep**（`rg`），脚本优先用 `rg`；否则用 `grep`。
- 样例行通常来自 glog 中带 `"event":"metrics_summary"` 的 JSON；指标在 JSON 的 `metrics[]` 里以 `"name":"..."` 出现。

---

## 3. 与 RFC 验收的对应关系

- 验收时需在集成/smoke 日志上跑本脚本，并**保留**输出中含 `Generated: Get performance breakdown tree` 的整段 ASCII 树，或重定向为 `get_breakdown_tree.txt`；详见上级 [README.md](../README.md)「验收清单」第 3 条。
