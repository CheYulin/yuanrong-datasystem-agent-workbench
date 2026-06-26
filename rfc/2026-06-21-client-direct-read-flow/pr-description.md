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
| Client | gate、fallback、ring refresh **policy**、TCP data read、cutback 版本门控 | redirect/moving 算法、Worker HashRing 状态机 |
| Worker | transport、deadline、primary replica、`GetClusterState` 版本 | 重复 redirect/moving 算法 |

---

## 主要代码变更

### R2 版本契约
- **Proto:** `GetClusterStateRspPb.ring_etcd_mod_revision` (3), `ring_local_version` (4)
- **Worker:** `HashRing::currEtcdModRevisionOfRing_` → `GetClusterState`
- **Client:** `DirectReadRpcAdapter::GetClusterState` 消费 revision；`ReadOnlyHashRingView` 版本守卫

### 生命周期 / 并发
- `ObjectClientImpl::AcquireDirectReadSession` — `directReadStateMutex_` 下创建/复用 session
- `ClientHashRingSource` / `DirectReadRouteProvider` / `DirectReadFlow` — `shared_ptr` 延长 adapter 与 ring source 生命周期
- 分布式 cutback：`RefreshOnClusterEvent` + ring 版本门控

### P2 控制面
- Common: `query_meta_orchestrating_meta_client`, redirect/merge helpers
- Client/Worker: transport + options 注入（各一份，算法在 Common）

### Direct read RPC 性能（1119）
- `direct_read_rpc_stub_util` — lazy init `RpcStubCacheMgr`，复用 worker-master / worker-worker stub（tcp direct + pool）
- `RpcStubCacheMgr::Init` 幂等
- centralized-master 下跳过 master RPC warmup（消除 ST perf 日志噪声）

### 冷读 perf ST（1119）
- `MeasureCrossNodeColdGets` — 每轮 `Put@W0`（不计时）+ 计时 `Get@W1`；`GetStringUuid()` 新 key；轮末 `GDecreaseRef` 释放 SHM
- `CrossNodeColdGetLatencyBenchmark` — 默认跑 **256KB**（warmup 10 / iters 200）与 **8MB**（warmup 3 / iters 20）；**先 8MB 后 256KB**（避免 gateway 阶段 W1 热缓存耗尽 SHM）
- CrossNode 集群 `shared_memory_size_mb=2048`（大 payload 冷读所需）

---

## Perf 验收（准入条件）

**1119 性能门禁（必须全部 PASS）：**

| # | 指标 | 阈值 | tiantiyun |
|---|------|------|-----------|
| P0 | Direct read 灾难性延迟消除 | avg ≪ 10 ms（修复前 ~104 ms） | **~2.67 ms**（remote_only 1000 iters） ✅ |
| P0 | vs 修复前改善倍数 | ≥ 30× | **~35×** ✅ |
| P1 | remote_only direct/gateway | ≤ 2.0× | **1.62×**（256KB 1000 iters） ✅ |
| P1 | Gateway 无回归 | delta ≈ 0 | **~1.53 ms** ✅ |
| P1 | remote_only `pathFallbackCount` | 0（真实 direct，非 timeout 回退） | ✅ |

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
| Meta 编排 | `QueryMetaOrchestratingMetaClient` + `ObjectReadMetaAccessFlow` | Client/Worker 同算法，Transport 最薄 |
| Redirect/moving | `query_meta_redirect_helper` | Common 单测覆盖 |
| Buffer 组装 | `FinishDirectReadGet` → `ProcessGetResponse` | 与 gateway Get 字节级一致 |
| RPC 连接 | `direct_read_rpc_stub_util` → `RpcStubCacheMgr` | 与 worker-master/worker-worker 同池 |
| Data 路径 | `ObjectReadDataFlow` + `ClientRemoteTcpDataTransport` | Worker 侧 `BatchGetObjectRemote` 待 1119 后续 batch 接线（不重复 1153 合并 RPC） |

**Latest tiantiyun：** `ClientDirectRead*` — **25/26 PASS**（2 perf ST 常规 SKIP；scale ST 建议 `--gtest_filter=ReadSurvivesWorkerScaleDownAndUp` 隔离跑）；冷读 perf ST 需 `DS_DIRECT_READ_PERF=1`

---

## Test plan

| 类别 | 覆盖 |
|------|------|
| UT redirect/orchestrator/fallback | payload index、moving 内层 retry、外层不 retry moving |
| UT `read_only_hash_ring_view` | stale worker、uuid-keyed ring 地址匹配 |
| ST moving/redirect/stale | `MetaMovingRefreshesRingAndSucceeds`、redirect loop、stale route |
| ST **数据一致性** | gateway Get vs direct read 字节级一致（`AssertBuffersEqual`）；bootstrap / steady-state / scale 各阶段 payload 校验 |
| ST steady-state refresh | `SteadyStateRepeatedGetsDoNotRefreshRingPerLookup` |
| ST scale | `ReadSurvivesWorkerScaleDownAndUp` — payload 存活（隔离运行；全量套件偶发 SIGABRT） |
| ST recovery / cutback | standby direct read、local worker recovery、distributed ring cutback |
| ST **冷读 perf** | `CrossNodeColdGetLatencyBenchmark` — 256KB + 8MB 新 key 跨节点 A/B |

**Latest tiantiyun（targeted）：** `ClientDirectRead*` filter — 25/26 PASS（scale 隔离；perf ST 需 `DS_DIRECT_READ_PERF=1`）

```bash
# 冷读 perf（256KB + 8MB）
DS_DIRECT_READ_PERF=1 DS_DIRECT_READ_PERF_MODE=local \
  ./ds_st_object_cache --gtest_filter=ClientDirectReadCrossNodeTest.CrossNodeColdGetLatencyBenchmark

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
