# Client Direct Read — MR 1119

**Branch:** `feature/client-direct-read-flow`  
**MR:** https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1119

---

## Summary

- **Client 跨节点直连读**：无本地 worker 时 Client 自查 meta + 拉数据；失败回退 gateway；**不改 Worker**。
- **P2 + HashRing R2**：meta 编排在 Common；Client 注入 transport；`ring_etcd_mod_revision` 版本契约 + `ReadOnlyHashRingView` stale 守卫。
- **P4 URMA data path（Draft→实现中）**：Client↔Data Worker `GetObjectRemote` + UB；`ClientUbTransportRegistry` 缓存建链；L1 URMA→TCP 与 gateway 语义对齐。
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

## 验证（tiantiyun，TCP + batch，2026-06-28）

| 项 | 结果 |
|----|------|
| UT（DirectRead + ObjectReadDataAccess） | **9/9** |
| ST 功能 TCP（ClientDirectRead*，不含 LEVEL2/LatencyBenchmark） | **22/22** |
| ST URMA fake | **待 PR 1129**（tiantiyun fake 环境） |
| 全量 harness `ds_st` link | worktree 既有 duplicate-`main` 问题；object-cache ST 用 `ds_st_object_cache` 验证 |

**关键 TCP 用例：** `CrossNodeDirectTcpGetMatchesGatewayGet` ✅；batch 默认开启（`enable_client_direct_read_batch=true`）。

**历史 perf 门禁（`fc904ff0`，256KB iters=100）：** P0 cold direct **2.18 ms**；P1 direct/gateway **0.45×**；fallback **0**。

---

## Test plan

```bash
# UT
UT_CTEST_REGEX='DirectRead|ObjectReadAccess|read_access' \
  bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree client-direct-read-flow --sync-local --phase ut

# ST 功能（object-cache 二进制）
# 在 remote build dir 跑 ds_st_object_cache，filter ClientDirectRead*（排除 LEVEL2/LatencyBenchmark）

# 全量回归（需修复 ds_st duplicate main 或沿用 ds_st_object_cache 分批）
bash scripts/testing/verify/run_direct_read_regression_remote.sh \
  --worktree client-direct-read-flow --sync-local
```

URMA ST：1129 fake 就绪后补 L1 success / registry cache / L1→TCP / 回归。

---

## Deferred

1153 Meta+Data 合并 · R3 client ring version STALE · P3 Worker `ReadOnlyHashRingView` · Colocate · URMA ST fake（1129）
