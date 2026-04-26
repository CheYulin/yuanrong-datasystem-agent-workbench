# Metrics / 性能报告脚本

## 脚本

- **[gen_kv_perf_report.py](./gen_kv_perf_report.py)** — 从 glog 中的 `metrics_summary` JSON 与可选的 `[Perf Log]:` **PerfPoint** 块生成 ASCII 树或 Markdown 报告。

## 环境

- Python 3（标准库即可：`argparse`、`json`）

## 常用命令

### ASCII 双树（关键路径 + 细化，含分组 Perf）

默认每个输入文件取**最后一条** `metrics_summary`，并在同文件中查找其附近的 `[Perf Log]:` 块。

```bash
python3 scripts/metrics/gen_kv_perf_report.py --ascii-tree /path/to/datasystem_worker.INFO.log
```

示例日志（可复制路径在 IDE 中打开）：

- [docs/yche.log](../../docs/yche.log)

### Markdown 报告（结构化 metrics + Perf 表）

```bash
python3 scripts/metrics/gen_kv_perf_report.py /path/to/datasystem_worker.INFO.log
```

- **`--perf-keys ''`**（空字符串）：Perf 表**不筛选**，输出日志中出现的全部 `KEY: {...}` 行。
- **`--last-only false`**：同一文件内**每条** `metrics_summary` 各出一段（适合多 cycle 日志）。

### 多 snapshot 紧凑表

```bash
python3 scripts/metrics/gen_kv_perf_report.py --table --last-only false /path/to.log
```

### 从 stdin 读

```bash
cat /path/to.log | python3 scripts/metrics/gen_kv_perf_report.py -
```

注意：stdin 模式**无法**关联同文件中的 `[Perf Log]`，ASCII 里 Perf 段落会为空。

### 压测摘要（可选）

```bash
python3 scripts/metrics/gen_kv_perf_report.py --bench-stats bench.txt /path/to.log
```

`bench.txt` 为 `Key: value` 或 `Key=value` 行（`#` 开头为注释）。

## 输出说明（ASCII）

- **图 1**：Client → 入口 Worker → query meta → 跨 worker 拉取 → URMA（metrics），以及 **Perf 关键锚点**（勿与 metrics 相加）。
- **图 2**：**Perf 细化分组树**（Create/Get/query_meta/RPC/URMA/ZMQ/Master/分配等），再接 metrics 的 CRUD、ZMQ、资源、Master 标量。

语义与注意事项见：

- [docs/perf-breakdown-triage-logic.md](../../docs/perf-breakdown-triage-logic.md)

## 一键「较全」输出（ASCII + 全量 Perf 表 Markdown）

在 workbench 根目录执行：

```bash
LOG=/path/to/datasystem_worker.INFO.log
PY=scripts/metrics/gen_kv_perf_report.py
{
  echo '## ASCII'; echo
  python3 "$PY" --ascii-tree "$LOG"
  echo; echo '---'; echo
  echo '## Markdown'; echo
  python3 "$PY" --perf-keys '' "$LOG"
} | tee /tmp/kv-perf-full.txt
```

以上路径中的 **`$PY`** 与仓库内 **[gen_kv_perf_report.py](./gen_kv_perf_report.py)** 为同一文件；在 Cursor / VS Code 中点击相对链接即可跳转到源码。
