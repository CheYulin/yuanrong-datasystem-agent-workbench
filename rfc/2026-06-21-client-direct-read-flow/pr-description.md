# Client Direct Read — MR 1119

**Branch:** `feature/client-direct-read-flow` · **HEAD:** `fc904ff0`  
**MR:** https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1119

---

## Summary

- **Client 跨节点直连读**：无本地 worker 时 Client 自查 meta + 拉数据；失败回退 gateway；**不改 Worker**。
- **P2 + HashRing R2**：meta 编排在 Common；Client 注入 transport；`ring_etcd_mod_revision` 版本契约 + `ReadOnlyHashRingView` stale 守卫。
- **Prod slim**：7 文件对 → **3 核心模块**（~1.2k LOC）；RPC stub cache 消除 ~100ms ephemeral channel 惩罚。
- **ST**：功能 24 + perf 3（`MODE=all`）；observer 计数/注入在 **ST 侧**注册，prod 仅 `DirectReadObserver` 扩展点。

**范围外：** Colocate inline read（独立 MR）、Meta+Data 合并 RPC（[1153](https://gitcode.com/openeuler/yuanrong-datasystem/pull/1153)）。

---

## Client 模块（prod）

| 模块 | 职责 |
|------|------|
| `direct_read_flow` | 编排 + fallback（meta/data 两阶段） |
| `client_hash_ring_source` | 路由 + etcd/worker ring refresh |
| `direct_read_rpc_adapter` | RPC + Meta/Data transport + stub cache |
| `direct_read_observers` | 可选回调槽（release 未注册 = no-op） |

ST/perf：`DirectReadTestObserver`（`tests/st/…`）注册 observer。

---

## 验证（tiantiyun，`fc904ff0`）

| 项 | 结果 |
|----|------|
| UT | **18/18** |
| ST 功能 | **24/24**（含 LEVEL2 scale / recovery / cutback） |
| ST perf `MODE=all` | **3/3** |
| CI 预期 | **22/22**（`-LE level2`，不含 scale+perf） |

**Perf 门禁（256KB, iters=100）：**

| 指标 | 阈值 | 实测 |
|------|------|------|
| P0 cold direct avg | ≪ 10 ms | **2.18 ms** |
| P1 cold direct/gateway | ≤ 2.0× | **0.45×**（direct 更快） |
| P1 remote_only ratio | ≤ 2.0× | **1.50×** |
| fallback | `pathFallbackCount=0` | ✅ |

> remote_only direct（3.11 ms）> gateway（2.07 ms）→ **1153 后**验收 `direct ≤ gateway`，不阻塞本 MR。

---

## Test plan

```bash
# 回归（功能 + perf）
bash yuanrong-datasystem-agent-workbench/scripts/testing/verify/run_direct_read_regression_remote.sh \
  --worktree client-direct-read-flow --sync-local
```

构建：`build.sh -p on -j 40`；perf env：`DS_DIRECT_READ_PERF=1 DS_DIRECT_READ_PERF_MODE=all`。

---

## Deferred

1153 Meta+Data 合并 · R3 client ring version STALE · P3 Worker `ReadOnlyHashRingView` · Colocate · URMA
