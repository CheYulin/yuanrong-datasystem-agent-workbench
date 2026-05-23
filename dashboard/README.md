# DataSystem Worker Metrics Dashboard

生成业务视角的 Worker Metrics 可视化 HTML Dashboard。

## 快速使用

### 生成 Dashboard

```bash
# 下载 ECharts（仅首次需要）
curl -sL https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js -o echarts.min.js

# 生成 HTML（self-contained，直接双击打开）
python gen_worker_metrics_dashboard.py /path/to/metrics.log

# 或指定输出路径
python gen_worker_metrics_dashboard.py /path/to/metrics.log output.html

# 或指定 ECharts 路径
python gen_worker_metrics_dashboard.py metrics.log output.html ./echarts.min.js
```

### 直接打开

生成后直接双击 `worker_metrics_biz_view.html` 即可在浏览器打开，**无需联网**。

### HTTP 服务（可选）

```bash
cd /root/agent/hermes-workspace
python -m http.server 8080
# 访问 http://localhost:8080/worker_metrics_biz_view.html
```

## 图表布局（从上到下）

### 1. 📊 Ops Count per Cycle — 流量模型
堆叠柱状图展示每周期读写 ops 数量：
- **Write (Create)** — 红色，CreateMeta RPC count
- **Write (Publish)** — 橙色，Publish count
- **Read (Get)** — 蓝色，Get count

用于快速判断流量模型（读多写少？写多读少？周期性？）

### 2. 📖 Read Flow & E2E
QueryMeta RPC → PullRemote 子流程的 P99/MAX 延迟曲线：
- QueryMeta RPC / ProcGet / E2E (RPC层)
- PostQuery / ThreadpoolExec / PullRemote Out (PullRemote 子阶段)

### 3. ⚙️ URMA / Inbound
数据提供者视角——本节点作为数据源被 Pull 时的操作：
- URMA Write / Wait / Nanosleep
- Inbound RemoteGet（本节点被其他节点 Pull 时）

### 4. ✍️ Write Flow
CreateMeta RPC → Publish → ProcCreate 子流程的 P99/MAX 延迟。

### 5. 📥 ZMQ Queue Wait
Server Q Wait vs Client Q Wait 延迟对比。

### 6. Latency Tables
所有指标的 Min / Avg / Max / P95 汇总表（P99 和 MAX 分开）。

## 日志格式

输入为 worker metrics log，每行为 pipe 分隔，消息体为 JSON：

```
ts | level | source | hostname | thread_ids | trace_id | err | op | latency(μs) | bytes | details
```

其中 `details` 包含 `{"event":"metrics_summary","cycle":N,...}` 格式的 metrics 数组，每个 metric 含 `total{}` 和 `delta{}` 两个 histogram 窗口。

**所有 delta histogram 字段：**
- `p50_us`, `p90_us`, `p99_us`, `max_us` — 延迟分位数
- `count` — 该周期内的操作次数

**支持的 ops 计数：**
- `worker_rpc_create_meta_latency` → delta.count = Create ops
- `worker_process_publish_latency` → delta.count = Publish ops
- `worker_process_get_latency` → delta.count = Get ops

## 依赖

- Python 3.8+
- ECharts 5.4.3（本地 `echarts.min.js`，已嵌入 HTML，无需单独下载）

## 文件

- `gen_worker_metrics_dashboard.py` — 生成脚本
- `worker_metrics_biz_view.html` — 生成的 Dashboard（self-contained）
- `echarts.min.js` — ECharts 库（可删除，已嵌入 HTML）
