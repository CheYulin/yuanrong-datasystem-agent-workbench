# dsbench vs kvtest 选型

## 决策表

| 用户意图 | 推荐工具 | 理由 |
|---------|---------|------|
| 快速验证 Worker 部署后 KV 是否正常 | **dsbench** SINGLE | 一条命令，与发布 wheel 同源 |
| 产品需求/SR 回归（RFC 中 dsbench 引用） | **dsbench** CUSTOMIZED 或 FULL | 与 openYuanrong 安装包一致 |
| 区分 SHM 本地读 vs 跨节点 RPC Get | **kvtest** Benchmark `get_cross_node` 等 | dsbench 无 per-path 拓扑 |
| Cache 命中率 / Pipeline QPS 控制 | **kvtest** | dsbench 不支持 |
| Set API 路径对比（string_view vs create_buffer） | **kvtest** Benchmark | dsbench 无 Set API 切换 |
| Agent harness smoke（tiantiyun） | 各 1 条 golden path | `bench.dsbench.smoke` / `bench.kvtest.smoke` |

## 能力对比

| 能力 | dsbench | kvtest |
|------|---------|--------|
| 安装方式 | pip wheel（含 dscli） | 源码 `./build.sh` + SDK |
| 多机编排 | Python CLI SSH → Worker 上跑 dsbench_cpp | `deploy_client.py`（SSH/kubectl） |
| 并发读写压测 | `--concurrent` | Benchmark `mixed_*` 模式 |
| 逐阶段 CSV | Python 汇总 CSV（BENCHMARK-RESULT 行） | `benchmark_phases.csv` / `latency_timeseries.csv` |
| HTTP 控制面 | 无 | `:listen_port/stats`、`:listen_port/stop` |
| FULL 内置用例集 | `--all`（每 Worker ≥25GB SHM） | 无等价物；用 `test_benchmark_integration.sh` T01–T11 |

## Handoff

- **bench 通过，需深挖热点** → `wb-perf`（perf/bpftrace/metrics_summary）
- **bench 失败，需查日志** → `wb-log-analysis` / `ds-log-analysis`
- **需先编译 SDK** → `wb-build`
