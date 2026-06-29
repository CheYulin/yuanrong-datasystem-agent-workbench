# Client Direct Read — 性能验证

**Status**: In-Progress  
**Branch**: `feature/client-direct-read-flow`

## 目标

对比 **功能开启前后** 跨节点 Object Get 时延，输出 **avg / p99 / p99.99**（与 kvtest metrics 口径一致）。

| 路径 | 含义 | ST 如何触发 |
| --- | --- | --- |
| `cross_node_gateway` | 旧路径：reader 连本地 worker，由 worker 代理跨节点拉数据 | `enable_client_direct_read=false` |
| `cross_node_direct` | 新路径：client 直连 meta/data（ST 用 `ForceDirectRead` 绕过 cutback） | `enable_client_direct_read=true` + `SetForceDirectRead(true)` |

拓扑：2 worker ST cluster；writer@worker0 Put，reader@worker1 Get（与 `CrossNodeWriteVisibleToDirectRead` 一致）。

## 运行

```bash
cd yuanrong-datasystem-agent-workbench

# 默认 256KB × 1000 次（warmup 50）
bash scripts/testing/bench/run_direct_read_perf_remote.sh \
  --worktree client-direct-read-flow \
  --branch feature/client-direct-read-flow \
  --sync-local

# 1MB payload、更多样本
DS_DIRECT_READ_PERF_SIZE=1048576 DS_DIRECT_READ_PERF_ITERS=2000 \
  bash scripts/testing/bench/run_direct_read_perf_remote.sh --sync-local
```

产物（`results/harness/direct-read-perf-*`）：

- `direct_read_perf.log` — 完整 ctest 日志
- `direct_read_perf.json` — 机器可读汇总 + gateway/direct delta
- `direct_read_perf.md` — 表格摘要

ST 用例：`ClientDirectReadCrossNodeTest.CrossNodeGetLatencyBenchmark`  
仅当 `DS_DIRECT_READ_PERF=1` 时执行；常规 ST 回归默认 **SKIP**。

## 与现有 harness 的关系

| 工具 | 适用 | 分位 |
| --- | --- | --- |
| `run_smoke_metrics_30s.sh` | KV ZMQ RPC | p99（glog histogram） |
| `run_kvtest_smoke_remote.sh` | KV Set/Get | p90/p99/p99.9/p99.99 |
| `run_dsbench_smoke_remote.sh` | dsbench KV | p90/p99/max |
| **`run_direct_read_perf_remote.sh`** | **OC 跨节点 Get gateway vs direct** | **avg/p99/p99.99** |

Object cache 尚无独立 dsbench/kvtest 场景；本 ST bench 填补 direct read 专项 A/B。

## 结果记录

实测数据写入 [`results.md`](./results.md)（每次跑完把 `direct_read_perf.md` 摘要粘贴或链接 evidence 目录）。

## 解读注意

1. **ST 单机多 worker**：网络 RTT 低于真实跨机；看 **相对 delta** 比绝对值更有意义。
2. **`ForceDirectRead`** 仅用于 A/B；生产无 local worker 时走 natural direct，有 local worker 时默认 gateway（见 hash-ring-refresh-policy）。
3. **同节点回归**：本 bench 不测 same-node；应用现有 OC ST + smoke 保证无行为变化。
4. **p99.99 样本量**：建议 `ITERS ≥ 1000`；样本过少时分位不稳定。
