# Client Direct Read — MR 1119

**Branch:** `feature/client-direct-read-flow` · **HEAD:** `2fc064f7`  
**MR:** https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1119

---

## Summary

- **Client 跨节点直连读**：无本地 worker 时 Client 自查 meta + 拉数据；**失败直接返回错误，不再 fallback gateway**；**不改 Worker**。
- **P2 + HashRing R2**：meta 编排在 Common（`ObjectReadMetaAccessFlow`）；data 在 Common（`ObjectReadDataFlow`，client direct read 专用）；Client 注入 transport；`ring_etcd_mod_revision` 版本契约 + `ReadOnlyHashRingView` stale 守卫。
- **P4 URMA data path（实现中）**：Client↔Data Worker `GetObjectRemote` + UB；`ClientUbTransportRegistry` 缓存建链；L1 URMA→TCP 与 gateway 语义对齐。
- **Batch remote get**：`BatchGetObjectRemote` + deferred batch（`ObjectReadDataFlow` Queue→Commit→Take）；按 data worker 分组；**默认 always-on**。
- **Flags 收敛**：仅保留 `enable_client_direct_read`、`client_direct_read_force`。
- **Codecheck（2026-06-28）**：修复 G.NAM/G.FMT/G.INC/析构/魔法数等 43 条未解决项（就地修复，无新增模块）。

**范围外：** Colocate inline read（独立 MR）、Meta+Data 合并 RPC（[1153](https://gitcode.com/openeuler/yuanrong-datasystem/pull/1153)）、scale 后 full migration 路由 ST（follow-up）。

---

## 验证（tiantiyun-80c128g，`c5ca8919` + ST fix @ 2026-06-29）

| 项 | 结果 |
|----|------|
| UT（DirectRead + ObjectReadDataAccess + ObjectReadMetaAccessFlow） | **9/9** ✅ |
| ST 功能 TCP（ClientDirectRead*，不含 LatencyBenchmark，**`-j1`**） | **24/24** ✅ |
| ST perf（3× LatencyBenchmark，256KB，iters=100，warmup=10） | **3/3** ✅ |
| ST URMA fake | **待 PR 1129** |

**ST 说明：** LEVEL2 用例受 80s KillTimer 约束；functional 回归请用 **`CTEST_JOBS_ST=1`**（`run_direct_read_regression_remote.sh` 已默认）。`-j4` 并行时 LEVEL2 偶发失败。

**Harness 注意：** 完整 `build.sh -i on` 在 `kv_client_example` 链接阶段失败（与 direct read 无关）；回归用 `cmake --build --target ds_st_object_cache` + `ctest` 可绕过。

---

## Perf 门禁（256KB payload，`iters=100`，`warmup=10`，tiantiyun）

环境：`DS_DIRECT_READ_PERF=1 DS_DIRECT_READ_PERF_SIZE=262144 DS_DIRECT_READ_PERF_ITERS=100 DS_DIRECT_READ_PERF_WARMUP=10`

### CrossNodeGetLatencyBenchmark（reader 在 worker1，object 在 worker0）

| Scenario | Get avg (ms) | 说明 |
|----------|-------------:|------|
| `cross_node_local_gateway` | **0.93** | `enable_client_direct_read=false`，经本地 worker gateway |
| `cross_node_local_direct_forced` | **1.28** | `enable_client_direct_read=true` + force direct |

ratio (direct/gateway) = **1.37×** ✅（阈值 ≤ 2.0×）

### CrossNodeColdGetLatencyBenchmark（256KB 冷读，每 iter 新 key）

| Scenario | Get avg (ms) | meta_rpc avg (ms) | data_rpc avg (ms) |
|----------|-------------:|------------------:|------------------:|
| `cross_node_cold_256k_gateway` | **4.12** | — | — |
| `cross_node_cold_256k_direct_forced` | **1.72** | **1.56** | **0.00**（inline hit） |

ratio (direct/gateway) = **0.42×** ✅（direct 更快）

### CrossNodeGetLatencyBenchmarkRemoteOnly（reader 无本地 worker）

| Scenario | Get avg (ms) | 说明 |
|----------|-------------:|------|
| `remote_only_gateway` | **1.27** | 无 local worker，gateway 路径 |
| `remote_only_direct` | **1.77** | 无 local worker，direct read |

ratio (direct/gateway) = **1.39×** ✅（阈值 ≤ 2.0×）

### 汇总判定

| 指标 | 阈值 | 实测 | 判定 |
|------|------|------|------|
| P0 cold direct avg | ≪ 10 ms | **1.72 ms** | ✅ |
| P1 cold direct/gateway | ≤ 2.0× | **0.42×** | ✅ |
| P1 local direct/gateway | ≤ 2.0× | **1.37×** | ✅ |
| P1 remote_only direct/gateway | ≤ 2.0× | **1.39×** | ✅ |
| fallback | 已移除 gateway fallback | ST 断言 `pathFallbackCount=0` | ✅ |

> Perf JSON 由 ST `PrintDirectReadPerfJson` 输出（`DIRECT_READ_PERF_JSON=…`），字段 `avg_us` ÷ 1000 = Get avg (ms)。

---

## ST 用例耗时（`-j1` 功能 / `-j1` perf，CTestCostData）

| 类别 | Wall time |
|------|-----------|
| 功能 ST 24 用例 | **~122 s** |
| Perf ST 3 用例 | **~25 s** |

多数 level0 单用例 CTest cost **~2–3 s**；`EXCLUSIVE_LEVEL2_*`（scale / distributed recovery）各 **~15–20 s**。

---

## Test plan

```bash
# 功能 + perf（ST 默认 -j1）
bash scripts/testing/verify/run_direct_read_regression_remote.sh \
  --worktree client-direct-read-flow --sync-local

# 仅功能 ST
CTEST_JOBS_ST=1 ctest --test-dir <build> -R ClientDirectRead -E LatencyBenchmark -j1 --timeout 600

# Perf 256KB
DS_DIRECT_READ_PERF=1 DS_DIRECT_READ_PERF_ITERS=100 DS_DIRECT_READ_PERF_WARMUP=10 \
  DS_DIRECT_READ_PERF_SIZE=262144 ctest -R 'CrossNode.*LatencyBenchmark' -j1
```

---

## Deferred

1153 Meta+Data 合并 · URMA ST fake（1129）· scale 后 changed_ranges migration ST
