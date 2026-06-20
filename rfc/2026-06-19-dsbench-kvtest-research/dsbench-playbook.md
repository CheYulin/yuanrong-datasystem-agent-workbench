# dsbench Playbook

## 架构

1. **Python CLI** (`dsbench`) — 入口在 `setup.py` entry point `yr.datasystem.cli.benchmark.main:main`
2. **C++ 执行层** (`dsbench_cpp`) — 打包在 wheel 内，通过 SSH 在 Worker 地址对应节点执行
3. **Executor** — `cli/benchmark/executor.py`：本地 IP 检测、SSH 执行、`ensure_dsbench_cpp_executable`

### SSH 约束

- 用户名 = 当前执行命令的用户
- 私钥 = `~/.ssh/id_rsa`（可通过 `~/.ssh/config` 改端口，默认 22）
- 多机需 SSH 互信

## 安装

```bash
pip install openyuanrong-datasystem
# 或源码编译 wheel 后 pip install dist/*.whl
dsbench --help
dscli --help
```

Worker 与 dsbench_cpp **版本必须一致**（同一次 wheel 安装）。

## 部署前置

```bash
# etcd（示例）
etcd --listen-client-urls http://127.0.0.1:2379 --advertise-client-urls http://127.0.0.1:2379 &

# 单机 Worker
dscli start -w \
  --worker_address "127.0.0.1:31501" \
  --etcd_address "127.0.0.1:2379" \
  --shared_memory_size_mb 4096
```

## 运行模式

### SINGLE（默认）

```bash
dsbench kv \
  -n 100 -s 1MB -c 4 -t 1 -b 1 \
  -S "127.0.0.1:31501" \
  -G "127.0.0.1:31501"
```

### 并发读写

```bash
dsbench kv --concurrent \
  -c 8 -t 1 -n 200 -s 1MB -b 1 \
  -S "127.0.0.1:31501" \
  -G "127.0.0.1:31501"
```

默认模式：set → get → del 顺序。并发模式：prefill → 并发 set/get → del。

### CUSTOMIZED

```bash
dsbench kv -f testcases.json \
  -S "127.0.0.1:31501" \
  -G "127.0.0.1:31501"
```

JSON 每条需含：`num`, `size`, `client_num`, `thread_num`, `batch_num`；且 `client_num × thread_num ≤ 128`。

### FULL（慎用）

```bash
dsbench kv --all -S "..." -G "..."
```

**每 Worker 至少 25GB 共享内存**。smoke 默认不跑 FULL。

## 关键参数

| 参数 | 说明 |
|------|------|
| `-S, --set_worker_addresses` | Set 目标 Worker（必填） |
| `-G, --get_worker_addresses` | Get 目标 Worker（必填）；del 用列表首个 Worker |
| `-c, -t, -b` | client 数、每 client 线程数、batch 大小 |
| `-n, -s, -p` | key 数、数据大小、key 前缀 |
| `--concurrent` | 并发读写 |
| `--skip_local` | Get 时跳过与当前 get worker 同址的 set 数据 |
| `--min_log_level` | 0=INFO, 1=WARNING, 2=ERROR（全局，放子命令前） |

## 输出解读

`dsbench_cpp` 每行格式（由 `KVBenchOutputHandler._parse_benchmark_result` 解析）：

```
BENCHMARK-RESULT:<action>-<client>-<thread>-<num>-<size>-<batch>, avg, min, p90, p99, max, tps, throughput
```

汇总 CSV 列：`action`, `size`, `count`, `batch`, `client`, `thread`, `worker`, `avg[ms]`, `min[ms]`, `p90[ms]`, `p99[ms]`, `max[ms]`, `tps[count/sec]`, `throughput[MB/sec]`。

## 观测

```bash
dsbench show   # IP、版本、内存、THP、HugePages、CPU MHz
dsbench --min_log_level=0 --log_monitor_enable=true kv ...
dscli collect_log --cluster_config_path ./cluster_config.json
```

## 故障分流

1. `dsbench: command not found` → wheel 未安装或 PATH
2. SSH / permission denied → 互信、`dsbench_cpp` chmod（executor 自动尝试）
3. Worker 不可达 → 检查 `-S/-G` 与 `dscli start` 状态
4. OOM / SHM 不足 → 增大 `--shared_memory_size_mb` 或减小 `-n/-s`；FULL 需 25GB+
5. `client_num × thread_num > 128` → 减 `-c` 或 `-t`
