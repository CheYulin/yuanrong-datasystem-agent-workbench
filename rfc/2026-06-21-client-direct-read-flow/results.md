# Client Direct Read — 验证结果

**Status**: In-Progress  
**Branch**: `feature/client-direct-read-flow` · **HEAD**: `705f80f2`

## 功能 / ST（2026-06-28，tiantiyun，squash 后）

| 类别 | 结果 | 备注 |
| --- | --- | --- |
| UT | 9/9 | `DirectReadFallbackTest` + `ObjectReadDataAccess` + `ObjectReadMetaAccessFlow` |
| ST TCP 功能 | 22/22 | 不含 LEVEL2 / LatencyBenchmark |
| ST LEVEL2 | 2/2 | MetaTimeout ✅；Scale ✅（首次 FAIL，重跑 PASS，见下） |
| ST perf | 3/3 | 256KB，iters=100，warmup=10 |

### Open：LEVEL2 scale flaky

`ClientDirectReadHashRingScaleTest.LEVEL2_ReadSurvivesWorkerScaleDownAndUp`：scale-up 后 `TryGetDirectReadObject` 偶发超时（line 1007）。首次失败、重跑通过。暂记 issue，不阻塞 MR。

## 性能 A/B（gateway vs direct）

| 日期 | commit | payload | iters | 场景 | gateway avg (ms) | direct avg (ms) | ratio | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-28 | 705f80f2 | 256KB | 100 | cold | 4.80 | 2.12 | 0.44× | tiantiyun `ds_st_object_cache` |
| 2026-06-28 | 705f80f2 | 256KB | 100 | remote_only | 1.28 | 1.95 | 1.53× | 同上 |
| 2026-06-25 | 96dc8650+WIP | 256KB | 300 | — | 1.40 | 103.77 | gateway 更快 | [direct-read-perf-20260625_manual](../harness/direct-read-perf-20260625_manual/) |

> 跑完 `run_direct_read_perf_remote.sh` 后，将 `direct_read_perf.md` 一行摘要填入上表。

## 功能 / ST（历史）

见 [pr-description.md](./pr-description.md) 与 [verify-remote-logs.md](./verify-remote-logs.md)。
