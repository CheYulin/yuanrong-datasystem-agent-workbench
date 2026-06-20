# kvtest Playbook

## 概述

独立 C++ 工具，位于 `yuanrong-datasystem/tests/kvtest/`。JSON 配置驱动，支持 Pipeline / Cache / Benchmark 三种模式。

## 编译

```bash
cd yuanrong-datasystem/tests/kvtest

# 默认 SDK: ../../output/cpp（CMake build 产物）
./build.sh

# 指定 SDK（Bazel tar 解压路径等）
./build.sh -s /path/to/sdk -c -j$(nproc)
```

产物：`output/kvtest`、`output/lib/libdatasystem.so`、`deploy_client.py`。

SDK 获取（Bazel）：

```bash
bazel build //bazel:datasystem_sdk --config=release
mkdir -p output/cpp
tar xf bazel-bin/bazel/datasystem_sdk.tar.gz -C output/cpp/ --strip-components=1
cp bazel-bin/libdatasystem.so output/cpp/lib/
```

## 部署前置

```bash
etcd &
dscli start -w --worker_address 127.0.0.1:31501 --etcd_address 127.0.0.1:2379
export JD_HOST_IP=127.0.0.1   # ServiceDiscovery 本机 Worker
```

## Benchmark golden path（smoke）

```bash
cd tests/kvtest/output
cat > config/smoke_set_local.json << 'EOF'
{
  "mode": "benchmark",
  "instance_id": 0,
  "listen_port": 9000,
  "etcd_address": "127.0.0.1:2379",
  "test_mode": "set_local",
  "worker_memory_mb": 4096,
  "num_threads": 8,
  "total_rounds": 3,
  "data_sizes": ["1MB"],
  "set_api": "string_view",
  "cleanup_method": "del"
}
EOF

LD_LIBRARY_PATH=./lib:$LD_LIBRARY_PATH ./kvtest config/smoke_set_local.json
```

## 16 种 Benchmark test_mode

| 模式 | 测量目标 |
|------|---------|
| `set_local` | 本机 SHM Set 吞吐 |
| `set_remote` | 远端 RPC Set 吞吐 |
| `get_local` | 本机 Set+Get 延迟 |
| `get_cross_node` | Worker A Get 拉 Worker B 数据（跨节点） |
| `get_remote_direct` | 远端 Worker 本地 Set+Get |
| `get_remote_cross` | 远端 Worker Get 拉本机数据 |
| `mixed_local` / `mixed_cross_node` | 混合读写 |
| MSet/MGet 批量变体 | 批量 API |

详见 `tests/kvtest/docs/benchmark-guide.md`。

## Set API

- `string_view` — 直接写入
- `create_buffer` — SHM Buffer + latch
- `create_buffer_raw` — SHM Buffer，无锁 memcpy

## 输出

| 文件 | 内容 |
|------|------|
| `benchmark_phases.csv` | 每轮 set/get/del 阶段的 QPS、P50/P90/P99/Max |
| `latency_timeseries.csv` | 周期性指标时序 |

## HTTP 控制

```bash
curl http://127.0.0.1:9000/stats | python3 -m json.tool
curl -X POST http://127.0.0.1:9000/stop
```

## 远程多节点

```bash
python3 deploy_client.py --deploy deploy.json --config config/template.json
```

支持 SSH 与 kubectl transport。

## 自测

```bash
cd tests/kvtest
bash tests/run_all_tests.sh          # C++ + Python UT
bash tests/test_benchmark_integration.sh  # T01–T11（需真实集群）
```

## 故障分流

1. `Invalid SDK dir` → 先 wb-build 或 Bazel SDK tar
2. Worker 连接失败 → etcd、`JD_HOST_IP`、Worker 端口
3. `listen_port` 冲突 → 改 `instance_id` / `listen_port`
4. 跨节点模式失败 → 检查 `remote_worker` 与双 Worker 部署
