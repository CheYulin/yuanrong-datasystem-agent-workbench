# Client Direct Read — P2 + HashRing R2

**Branch:** `feature/client-direct-read-flow`  
**MR:** https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1119

---

## Summary

1. **P2 meta 编排收敛** — Client / Worker QueryMeta 统一走 `QueryMetaOrchestratingMetaClient` + `IQueryMetaTransport`；redirect/moving 算法与 payload merge 在 Common。
2. **HashRing R2 版本契约** — `GetClusterStateRspPb.ring_etcd_mod_revision`；Worker 填充 etcd modRevision；Client `LoadFromWorker` 与 etcd 共用版本空间；`ReadOnlyHashRingView` 拒绝 stale/unknown worker 覆盖较新 etcd 快照。
3. **Direct read 生命周期** — `ObjectClientImpl` 用 `mutex` + `shared_ptr` 管理 `directReadRpcAdapter_` / `directReadRingSource_`；`DirectReadFlow` 持有 `shared_ptr`，请求路径禁止无锁 reset raw pointer。
4. **Codex 行为修复** — redirect payload index、steady-state ring refresh、moving 单层重试（外层仅 stale route）。
5. **RPC stub 复用** — client direct read 走 `RpcStubCacheMgr`（QueryMeta / GetObjectRemote），消除 ephemeral channel 导致的 ~100ms 惩罚。
6. **Perf ST** — `CrossNodeGetLatencyBenchmark`（local reader 同 key 诊断）+ `CrossNodeGetLatencyBenchmarkRemoteOnly`（无 healthy local worker，生产语义）+ **`CrossNodeColdGetLatencyBenchmark`（公平冷读：每轮新 key，direct 先于 gateway）**。

**本 MR 范围外**

- **R2 Meta+Data 合并 RPC** → [PR 1153](https://gitcode.com/openeuler/yuanrong-datasystem/pull/1153)；remote-only 场景下 **direct ≤ gateway** 的 perf 验收在 1153 合入后完成。
- Colocate inline data read / `ObjectReadMetaAccessFlow` 拆分 — 三个 revert commit 已从 HEAD 移除，不在 !1119 交付范围内。

**明确不做**

- direct data TCP 按 `GetParam.subTimeoutMs` 做 SDK 订阅时长拆分。
- 不把 Worker `HashRing` 状态机、迁移任务或 data transport policy 下沉到 Client。

---

## 模块边界

| Layer | Owns | Must NOT own |
|-------|------|--------------|
| Common | meta 编排、redirect/moving、merge、`ReadOnlyHashRingView` | Client fallback、Worker 地址解析 |
| Client | gate、fallback、`ClientHashRingSource` 路由、`DirectReadFlow` 编排、TCP data transport | redirect/moving 算法、Worker HashRing 状态机 |
| Worker | transport、deadline、primary replica、`GetClusterState` 版本 | 重复 redirect/moving 算法（本 MR 不改 worker） |

---

## 主要代码变更

### R2 版本契约
- **Proto:** `GetClusterStateRspPb.ring_etcd_mod_revision` (3), `ring_local_version` (4)
- **Worker:** `HashRing::currEtcdModRevisionOfRing_` → `GetClusterState`
- **Client:** `DirectReadRpcAdapter::GetClusterState` 消费 revision；`ReadOnlyHashRingView` 版本守卫

### 生命周期 / 并发
- `ObjectClientImpl::AcquireDirectReadSession` — `directReadStateMutex_` 下创建/复用 session
- `ClientHashRingSource` / `DirectReadFlow` — `shared_ptr` 延长 adapter 与 ring source 生命周期
- 分布式 cutback：`RefreshOnClusterEvent` + ring 版本门控

### P2 控制面
- Common: `query_meta_orchestrating_meta_client`, redirect/merge helpers
- Client/Worker: transport + options 注入（各一份，算法在 Common）

### Client direct read 模块（1119，prod **1243 LOC / 4 文件对**，自 ~1440 LOC / 7 对 slim **-14%**）

| 文件 | 职责 |
|------|------|
| `direct_read_flow` | 编排 + **fallback**：meta phase（outer stale retry）+ data phase + finishGet；`DirectReadFallback` 内嵌 |
| `client_hash_ring_source` | **路由**：`IObjectReadRouteProvider`，etcd/worker ring refresh（原 `DirectReadRouteProvider` 已并入） |
| `direct_read_rpc_adapter` | **RPC 聚合**：GetClusterState / GetObjectRemoteTcp + stub cache；内嵌 `ClientQueryMetaTransport` + `ClientRemoteTcpDataTransport` |
| `direct_read_test_hook` | ST/perf 计数（非 prod 路径） |

**Slim 删除（无行为变更）：** `direct_read_route_provider`、`direct_read_access_adapters`、`client_query_meta_transport`、`client_remote_tcp_data_transport`、`direct_read_fallback` 独立文件 — 逻辑保留在上述 4 模块内。

**RPC 性能要点**
- `direct_read_rpc_adapter` — lazy init `RpcStubCacheMgr`，复用 worker-master / worker-worker stub
- `DS_DIRECT_READ_PERF=1` 时 `DIRECT_READ_PERF_JSON` 含 phase 字段：`meta_rpc_avg_us`、`data_rpc_avg_us`、`inline_data_hits`、`client_other_avg_us`

### 冷读 perf ST（1119）
- `MeasureCrossNodeColdGets` — 每轮 `Put@W0`（不计时）+ 计时 `Get@W1`；`GetStringUuid()` 新 key；轮末 `GDecreaseRef` 释放 SHM
- `CrossNodeColdGetLatencyBenchmark` — 默认跑 **256KB**（warmup 10 / iters 200）与 **8MB**（warmup 3 / iters 20）；**先 8MB 后 256KB**（避免 gateway 阶段 W1 热缓存耗尽 SHM）
- CrossNode 集群 `shared_memory_size_mb=2048`（大 payload 冷读所需）

---

## Perf 验收（准入条件）

**1119 性能门禁（必须全部 PASS）：**

| # | 指标 | 阈值 | tiantiyun |
|---|------|------|-----------|
| P0 | Direct read 灾难性延迟消除 | avg ≪ 10 ms（修复前 ~104 ms） | **2.32 ms**（cold 256KB, `MODE=all`, 2026-06-27） ✅ |
| P0 | vs 修复前改善倍数 | ≥ 30× | **~45×**（104/2.32） ✅ |
| P1 | cold 256KB direct/gateway latency ratio | ≤ 2.0×（direct 不得慢于 gateway 2 倍以上） | **0.44×**（2.32/5.28 ms，direct 更快） ✅ |
| P1 | remote_only direct/gateway latency ratio | ≤ 2.0× | **1.70×**（3.25/1.91 ms） ✅ |
| P1 | Gateway 无回归 | delta ≈ 0 vs 基线 | **1.83 ms**（local 同 key gateway） ✅ |
| P1 | `pathFallbackCount` | 0（真实 direct，非 timeout 回退） | ✅ |

**1153 追加门禁（本 MR 不阻塞）：** remote_only direct avg **≤** gateway avg（Meta+Data 合并 RPC 消除第二 RTT）。

| 场景 | ST | 语义 | 期望 |
|------|-----|------|------|
| `cross_node_local_*` | `CrossNodeGetLatencyBenchmark` | reader@W1 有 local worker；**同 key 复读**；gateway 先测 | **诊断用**；gateway 可能因 W1 SHM 热缓存偏快；direct 多 1 次 client RPC |
| `cross_node_cold_*` | `CrossNodeColdGetLatencyBenchmark` | **每轮新 key** 真实跨节点冷读；direct 先于 gateway | **公平 A/B**；大 payload 下 direct 单跳 W0→Client 明显优于 gateway 双跳 |
| `remote_only_*` | `CrossNodeGetLatencyBenchmarkRemoteOnly` | affinity reader，W0 shutdown，无 healthy local worker；数据在 W1，master@W1 | **生产语义**；1119 门禁见上表；**1153 后 direct ≤ gateway** |

**tiantiyun 冷读（`CrossNodeColdGetLatencyBenchmark`，2026-06-26）：**

| Payload | Path | avg | p99 | direct/gateway |
|---------|------|-----|-----|----------------|
| 256KB | direct | **2.21 ms** | 2.46 ms | direct **2.3× 更快** |
| 256KB | gateway | 5.16 ms | 5.58 ms | |
| 8MB | direct | **9.60 ms** | 10.43 ms | direct **2.5× 更快** |
| 8MB | gateway | 23.91 ms | 26.93 ms | |

> 冷读 vs 同 key：`cross_node_local` 在 8MB 下 gateway ~1.8 ms 不变，因 W1 缓存首包后本地 mmap；冷读 gateway ~24 ms 才是真实跨节点拉取成本。

Harness：`scripts/testing/bench/run_direct_read_perf_remote.sh`（`DS_DIRECT_READ_PERF_MODE=local|remote|all`；ctest regex `CrossNode.*LatencyBenchmark`）

**Remote-only ST 修复（1119）：** Recovery 用例 `FLAGS_master_address` 对齐 `masterIdx=1`；benchmark 数据写在 W1；direct 阶段先于 gateway 测量。

---

## 代码复用（审查要点）

| 复用点 | 实现 | 说明 |
|--------|------|------|
| Meta 编排 | `QueryMetaOrchestratingMetaClient` + `ObjectReadMetaAccessFlow` | Client transport 内嵌于 `DirectReadRpcAdapter::ClientQueryMetaTransport` |
| Data 路径 | `ObjectReadDataFlow` + `DirectReadRpcAdapter::ClientRemoteTcpDataTransport` | **本 MR 不改 worker** |

**回归（MR 合入前：功能 + perf 必过）**

```bash
bash yuanrong-datasystem-agent-workbench/scripts/testing/verify/run_direct_read_regression_remote.sh \
  --worktree client-direct-read-flow --sync-local
```

| Phase | 范围 | 期望 |
|-------|------|------|
| 1 功能 | `ClientDirectRead`（含 LEVEL2，排除 `LatencyBenchmark`） | **24/24 PASS** |
| 2 性能 | `CrossNode.*LatencyBenchmark` + `DS_DIRECT_READ_PERF=1` | **3/3 PASS** + `DIRECT_READ_PERF_JSON` |

构建 `ENABLE_PERF=on -j 40`；perf 默认 256KB / warmup 10 / iters 100 / **`MODE=all`**（可 env 覆盖）。

**CI 快速门禁（不含 LEVEL2/perf）：** 22/22，`ST_CTEST_LABEL_EXCLUDE=level2`

**Latest tiantiyun（2026-06-27，slim 4 模块后）：**

| Phase | 命令 / filter | 结果 | 评判 |
|-------|---------------|------|------|
| UT fallback | `ds_ut --gtest_filter=DirectReadFallback*` | **PASS**（全绿） | 回退 reason 归一化无回归 |
| UT object-cache | `ds_ut_object --gtest_filter=QueryMeta*:ReadOnlyHashRing*:ObjectReadAccess*` | **16/16 PASS** | meta 编排 + ring 版本守卫 |
| ST 功能 | `ClientDirectRead*:-*LatencyBenchmark*` | **24/24 PASS** | 含 LEVEL2 scale；gateway/direct 字节一致 |
| ST perf | `*LatencyBenchmark*` + `DS_DIRECT_READ_PERF=1` + **`MODE=all`** | **3/3 PASS** | 含 remote_only 生产语义 |

**Perf JSON（256KB, warmup=10, iters=100, `MODE=all`，2026-06-27）：**

| scenario | avg | p99 | gate |
|----------|-----|-----|------|
| `cross_node_cold_256k_direct_forced` | **2.32 ms** | 2.56 ms | P0 ≪ 10 ms ✅ |
| `cross_node_cold_256k_gateway` | **5.28 ms** | 7.62 ms | direct **2.28× faster** ✅ |
| `cross_node_local_direct_forced` | 2.01 ms | 2.33 ms | 诊断 ST（同 key） |
| `cross_node_local_gateway` | 1.83 ms | 1.99 ms | W1 SHM 热缓存偏快（预期） |
| `remote_only_direct` | **3.25 ms** | 3.60 ms | P0 ≪ 10 ms ✅；P1 ratio **1.70×** ✅ |
| `remote_only_gateway` | **1.91 ms** | 2.27 ms | direct > gateway 待 **1153** Meta+Data 合并 |

**Scale ST 修复：** `LEVEL2_ReadSurvivesWorkerScaleDownAndUp` — `KillWorker` → `ShutdownNode` + `sleep(8)` + `TryGetDirectReadObject`；单测 ~20s PASS，全量 24/24 PASS。

**构建 /  harness：**
- `client/CMakeLists.txt` — prod **4 文件对**（`direct_read_flow` / `client_hash_ring_source` / `direct_read_rpc_adapter` / `direct_read_test_hook`）
- `tests/st/` — 功能 / perf 拆分：`client_direct_read_test.cpp`（24）+ `client_direct_read_perf_test.cpp`（3，`ENABLE_PERF=on`）
- 并行度 **`-j 40`**；perf 须 `build.sh -p on`（`ENABLE_PERF=on` env  alone 会被 `build_common.sh` 覆盖）

---

## Test plan

| 类别 | 覆盖 |
|------|------|
| UT redirect/orchestrator/fallback | payload index、moving 内层 retry、外层不 retry moving |
| UT `read_only_hash_ring_view` | stale worker、uuid-keyed ring 地址匹配 |
| ST moving/redirect/stale | `MetaMovingRefreshesRingAndSucceeds`、redirect loop、stale route |
| ST **数据一致性** | gateway Get vs direct read 字节级一致（`AssertBuffersEqual`）；bootstrap / steady-state / scale 各阶段 payload 校验 |
| ST steady-state refresh | `SteadyStateRepeatedGetsDoNotRefreshRingPerLookup` |
| ST scale | `ReadSurvivesWorkerScaleDownAndUp` — `ShutdownNode` 优雅缩容；**24/24 全量 PASS** |
| ST recovery / cutback | standby direct read、local worker recovery、distributed ring cutback |
| ST **冷读 perf** | `CrossNodeColdGetLatencyBenchmark` — 256KB + 8MB 新 key 跨节点 A/B |

**Latest tiantiyun：** 功能 **24/24** + perf **3/3**（`MODE=all`）；MR 合入门禁 = 功能全绿 **且** perf JSON 满足 P0/P1 阈值

```bash
# 全量 perf（含 remote_only）
DS_DIRECT_READ_PERF=1 DS_DIRECT_READ_PERF_MODE=all \
  ./ds_st_object_cache --gtest_filter='*LatencyBenchmark*'

bazel test --config=release --config=test \
  //tests/ut/common/object_cache:query_meta_redirect_helper_test \
  //tests/ut/common/object_cache:query_meta_orchestrating_meta_client_test \
  //tests/ut/client:direct_read_fallback_test \
  //tests/ut/common/object_cache:read_only_hash_ring_view_test \
  //tests/st/client/object_cache:client_direct_read_st_test \
  --test_filter='ClientDirectRead*' \
  --test_tag_filters=
```

---

## Deferred

- **R2** — Meta+Data 合并单次 RPC（[PR 1153](https://gitcode.com/openeuler/yuanrong-datasystem/pull/1153)）；合入后 rebase 1119 并跑 `remote_only` perf 验收
- **R3** — Gateway Get 携带 client ring version；Worker STALE
- **P3** — Worker 路由统一 `ReadOnlyHashRingView`
- Colocate inline data path（独立 MR）
- Worker bulk `RedirectRetryWhenMetasMoving` 收敛
