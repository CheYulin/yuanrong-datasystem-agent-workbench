# Client Direct Read — 验证结果

**Status**: In-Progress  
**Branch**: `feature/client-direct-read-flow` · **HEAD**: `11334922`

## 功能 / ST（2026-06-28，tiantiyun，review fix 后）

| 类别 | 结果 | 备注 |
| --- | --- | --- |
| UT | 9/9 | `DirectReadFallbackTest` + `ObjectReadDataAccess` + `ObjectReadMetaAccessFlow` |
| ST TCP 功能 | 24/24 | `-j4`，含 2× `EXCLUSIVE_LEVEL2_*` |
| ST perf | 3/3 | 256KB，iters=100，warmup=10 |

### ST 用例耗时（秒，CTestCostData，`-j4` 功能 / `-j1` perf）

功能用例（22 个 level0 + 2 个 EXCLUSIVE level2 串行）wall **~45 s**；多数 level0 单用例 CTest cost **~1 s**（并行分摊）。

| 用例 | sec |
| --- | --- |
| DefaultDisabledUsesWorkerPath | 1 |
| SameNodeUsesWorkerPathWhenEnabled | 1 |
| CrossNodeDirectTcpGetMatchesGatewayGet | 1 |
| DataWorkerUnavailableReturnsError | 1 |
| DirectQueriesMetaOnSuccessfulGet | 1 |
| StaleRouteReturnsErrorAfterRetry | 1 |
| RedirectLoopReturnsError | 1 |
| LEVEL2_MetaTimeoutReturnsError | 1 |
| BatchGetReturnsErrorOnStaleRoute | 1 |
| SameNodeGet/SameNodeWrite (×2) | 1 each |
| CrossNodeGetWithLocalWorker / Write×2 / WriteVisible | 1 each |
| HashRing: MetaMoving / Bootstrap / SteadyState / ColocatedInline | 1 each |
| RemoteDataPathWhenInlineBypassed | 1 |
| Recovery: Standby / Cutback / RemoteOnly | 1 each |
| EXCLUSIVE_LEVEL2_LocalWorkerRecoveryCutbackWithDistributedRing | serial |
| EXCLUSIVE_LEVEL2_ReadSurvivesWorkerScaleDownAndUp | serial |

Perf（wall **~22 s**）：

| 用例 | sec |
| --- | --- |
| CrossNodeGetLatencyBenchmark | ~7 |
| CrossNodeColdGetLatencyBenchmark | ~8 |
| CrossNodeGetLatencyBenchmarkRemoteOnly | ~7 |

## 性能 A/B（gateway vs direct）

| 日期 | payload | iters | 场景 | gateway avg (ms) | direct avg (ms) | ratio | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-28 | 256KB | 100 | cold | ~4.8 | ~2.1 | ~0.44× | tiantiyun perf ST PASS |
| 2026-06-28 | 256KB | 100 | remote_only | ~1.3 | ~2.0 | ~1.5× | 同上 |
| 2026-06-25 | 256KB | 300 | — | 1.40 | 103.77 | gateway 更快 | [direct-read-perf-20260625_manual](../harness/direct-read-perf-20260625_manual/) |

## Open / Deferred

- **177002940** scale 后 `changed_ranges` full migration 路由 ST — follow-up，当前 ST 已覆盖优雅缩容/扩容后读可用 + ring refresh。
- URMA ST fake — 待 PR 1129。
