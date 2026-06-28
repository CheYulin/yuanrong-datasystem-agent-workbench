# Client Direct Read — MR 1119

**Branch:** `feature/client-direct-read-flow` · **HEAD:** `705f80f2` (squashed from 39 commits; backup `6752fd35` on `backup/feature/client-direct-read-flow-pre-squash-6752fd35`)  
**MR:** https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1119

---

## Summary

- **Client 跨节点直连读**：无本地 worker 时 Client 自查 meta + 拉数据；失败回退 gateway；**不改 Worker**。
- **P2 + HashRing R2**：meta 编排在 Common；Client 注入 transport；`ring_etcd_mod_revision` 版本契约 + `ReadOnlyHashRingView` stale 守卫。
- **P4 URMA data path（实现中）**：Client↔Data Worker `GetObjectRemote` + UB；`ClientUbTransportRegistry` 缓存建链；L1 URMA→TCP 与 gateway 语义对齐。
- **Batch remote get**：`BatchGetObjectRemote` + deferred batch（`ObjectReadDataFlow` Queue→Commit→Take）；按 data worker 分组 + UB max-size 切 batch；gflag `enable_client_direct_read_batch`（默认 true）。
- **Common 复用**：`remote_get_client_helper` 抽取 GetObjectRemote 构建/UB materialize/TCP payload 解析。
- **Prod slim**：direct read 核心模块 + stub cache；ST observer 在 **ST 侧**注册。

**范围外：** Colocate inline read（独立 MR）、Meta+Data 合并 RPC（[1153](https://gitcode.com/openeuler/yuanrong-datasystem/pull/1153)）。

---

## Client 模块（prod）

| 模块 | 职责 |
|------|------|
| `direct_read_flow` | 编排 + fallback（meta/data 两阶段） |
| `client_hash_ring_source` | 路由 + etcd/worker ring refresh |
| `direct_read_rpc_adapter` | RPC + Meta/Data transport + stub cache + **BatchGetObjectRemote** |
| `client_ub_transport_registry` | Client↔Data Worker URMA 建链缓存 |
| `direct_read_observers` | 可选回调槽（release 未注册 = no-op） |

Common：`object_read_data_flow` deferred batch hook；`remote_get_client_helper`。

ST/perf：`DirectReadTestObserver`（`tests/st/…`）注册 observer。

---

## 验证（tiantiyun-80c128g，`705f80f2`，2026-06-28）

构建：`ds_st_object_cache` 增量 build（`ENABLE_PERF=on`）；全量 `ds_st` link 仍因 worktree duplicate-`main` 失败，与 squash 前相同。

| 项 | 结果 |
|----|------|
| UT（DirectRead + ObjectReadDataAccess） | **9/9** |
| ST 功能 TCP（ClientDirectRead*，不含 LEVEL2/LatencyBenchmark） | **22/22** |
| ST LEVEL2 | **2/2**（scale 见 Known issues） |
| ST perf（3× LatencyBenchmark） | **3/3** |
| ST URMA fake | **待 PR 1129** |

**关键 TCP：** `CrossNodeDirectTcpGetMatchesGatewayGet` ✅；batch 默认 `enable_client_direct_read_batch=true`。

### Perf 门禁（256KB，`iters=100`，`warmup=10`）

| 指标 | 阈值 | 实测 | 判定 |
|------|------|------|------|
| P0 cold direct avg | ≪ 10 ms | **2.12 ms** | ✅ |
| P1 cold direct/gateway | ≤ 2.0× | **0.44×** | ✅ |
| P1 remote_only direct/gateway | ≤ 2.0× | **1.53×** | ⚠️ 边界（历史 iters=1000 为 1.50×） |
| fallback | `pathFallbackCount=0` | gtest 断言通过 | ✅ |

| Scenario | avg (ms) |
|----------|----------|
| `cross_node_cold_256k_direct_forced` | 2.12 |
| `cross_node_cold_256k_gateway` | 4.80 |
| `cross_node_local_direct_forced` | 2.06 |
| `cross_node_local_gateway` | 1.45 |
| `remote_only_direct` | 1.95 |
| `remote_only_gateway` | 1.28 |

---

## Known issues（记录，暂不阻塞 MR）

**`ClientDirectReadHashRingScaleTest.LEVEL2_ReadSurvivesWorkerScaleDownAndUp` 偶发 flaky**

- **现象：** worker scale-up 后第三次 `TryGetDirectReadObject` 超时（`client_direct_read_test.cpp:1007`）；首次跑 FAIL（~56s），立即重跑 PASS（~22s）。
- **怀疑：** scale-up 后 ring/route 就绪时序 + 长 build 后集群负载；与 squash/URMA/batch 代码变更无直接证据。
- **跟进：** 加重试/加长 `WaitNodeReady` 后等待；CI 上观察；不纳入本次 MR 修复范围。

---

## Test plan

```bash
# UT + 功能 ST（worktree verify；或 remote 直接跑 ds_ut / ds_st_object_cache）
bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree client-direct-read-flow --sync-local --phase ut

# LEVEL2 + perf（remote ds_st_object_cache）
# LEVEL2: ClientDirectReadTest.LEVEL2_* + ClientDirectReadHashRingScaleTest.LEVEL2_*
# Perf: DS_DIRECT_READ_PERF=1 DS_DIRECT_READ_PERF_ITERS=100 DS_DIRECT_READ_PERF_WARMUP=10 \
#       --gtest_filter='*LatencyBenchmark*'
```

URMA ST：1129 fake 就绪后补 L1 success / registry cache / L1→TCP / 回归。

---

## Deferred

1153 Meta+Data 合并 · R3 client ring version STALE · P3 Worker `ReadOnlyHashRingView` · Colocate · URMA ST fake（1129）· LEVEL2 scale ST flaky 根因
