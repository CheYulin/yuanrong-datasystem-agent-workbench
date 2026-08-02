# CI 8907 Worker Coordinator Isolation Fix

## Context

- PR: openeuler/yuanrong-datasystem !1798
- CI: aarch64 check_build #8907
- Source head before fix: `066caeb3b23929d6baa902f28c4706721345e8e8`
- Remote validation worktree: `/home/ds-verify-worker-isolation-urma-20260802/src`
- CMake cache: `BUILD_WITH_URMA_MOCK=on`
- Build parallelism: `-j80`

## CI Failures

The rerun-failed section left three active failures:

| Case | Symptom |
| --- | --- |
| `TopologyRecoveryManagerTest.UnboundRequestsDoNotConsumeClusterAdmission` | Segmentation fault during manager teardown with `pending_work=1`. |
| `TopologyRecoveryManagerTest.RequestsOnePayloadForIdenticalHighestEvidence` | Segmentation fault during manager teardown with `pending_work=1`. |
| `KVCacheClientServiceDiscoverySwitchBackTest.TestRecoverLocalWorker` | Timed out after worker restart; restarted worker rejected reconciliation with `local topology member is not admitted`. |

## Root Cause

1. `TopologyRecoveryManager` scheduled delayed reconcile work that still captured the manager object. `Shutdown()` marked `stopping_` but did not wait for `pendingRecoveryWork_` to drain before object teardown. Some async branches also skipped counter release when the leader round changed.
2. Restart reconciliation checked full topology serving readiness before calling `NotifyReconciliationDone()`. In membership rejoin flow, that creates a circular wait: the restarted worker cannot become admitted until reconciliation completion is reported, but reconciliation completion was blocked by "local member not admitted".

## Fix

1. Added `CompleteRecoveryWorkLocked()` and used it for payload validation, reconcile, delayed reconcile, and submit-failure paths.
2. `BeginLeaderRound()` no longer resets in-flight async counters owned by closures from the previous round.
3. `Shutdown()` wakes delayed tasks and waits for `pendingRecoveryWork_ == 0` before destroying the recovery pool.
4. `GetReadyToWork()` skips the pre-notify `CheckWaitTopologyReady()` only for restart rejoin, calls `NotifyReconciliationDone()`, then waits for topology readiness before setting health.
5. Added UT `TopologyRecoveryManagerTest.ShutdownCancelsDelayedReconcile`.

## Validation

| Type | Command | Result | Time |
| --- | --- | --- | --- |
| CMake build | `cmake --build build --target ds_ut -j80` | PASS | linked `ds_ut` |
| CMake build | `cmake --build build --target ds_st_kv_cache -j80` | PASS | linked `ds_st_kv_cache` |
| UT | `./build/tests/ut/ds_ut --gtest_filter=TopologyRecoveryManagerTest.UnboundRequestsDoNotConsumeClusterAdmission:TopologyRecoveryManagerTest.RequestsOnePayloadForIdenticalHighestEvidence:TopologyRecoveryManagerTest.ShutdownCancelsDelayedReconcile --gtest_repeat=20` | PASS, 3 cases x 20 | last iteration 5 ms |
| UT | `./build/tests/ut/ds_ut --gtest_filter=TopologyRecoveryManagerTest.RecoveryTasksPreserveSubmittingTraceContext` | PASS, 1 case | 3 ms |
| ST | `TEST_SRCDIR=/home/ds-verify-worker-isolation-urma-20260802/src TEST_WORKSPACE=. ./build/tests/st/ds_st_kv_cache --gtest_filter=KVCacheClientServiceDiscoverySwitchBackTest.TestRecoverLocalWorker` | PASS, 1 case | 13.583 s |
| Static | `clang-format --dry-run --Werror ...` | NOT PASS: existing whole-file format violations outside this diff | N/A |
| Static | `clang-tidy -p build ...` | Started, stopped after >200 s while processing large worker file; no completed result | >200 s |

## Notes

- The first two ST attempts failed before business logic because the CMake fallback resolved `mock_obs_service.py` to `/tests/...`; setting `TEST_SRCDIR` and `TEST_WORKSPACE=.` made the ST locate the source-tree script.
- No full-file formatting was kept, to avoid review noise from pre-existing style drift.

## CodeCheck Follow-up

Export file: `openeuler_yuanrong-datasystem_全量导出（未解决）_20260802152130.xlsx`

| Rule | Location | Action |
| --- | --- | --- |
| `G.FMT.06-CPP` function call parameter indentation | `src/datasystem/worker/worker_oc_server.cpp:1335` | Replaced inline builder lambda with a local callback variable to avoid chained-call indentation noise. |
| `G.FMT.06-CPP` function call parameter indentation | `src/datasystem/worker/worker_oc_server.cpp:1359` | Replaced inline builder lambda with a local callback variable to avoid chained-call indentation noise. |
| `G.FUN.01-CPP` function parameter count | `src/datasystem/worker/worker_oc_server.cpp:500` | Introduced a small `PeerHashRingRefreshContext` parameter object; this keeps behavior unchanged and avoids broad refactoring. |
| `G.NAM.03-CPP` static variable naming | `src/datasystem/worker/worker_oc_server.cpp:488` | Renamed `SHA256_HEX_SIZE` to `sha256HexSize`. |
| `G.FUN.01-CPP` function size | `src/datasystem/coordinator/topology_recovery_manager.cpp:954` | Not modified per request; `MaybeFinalize` is 52 non-comment lines and should be handled by shielding/suppression if gate requires it. |

Validation must run only on `tiantiyun-80c128g`. A local tmux build was started briefly by mistake and stopped before
completion; do not use that partial local log as verification evidence. Remote validation uses
`/home/ds-thirdparty-cache`, `BUILD_WITH_URMA_MOCK=on`, and requested `-j80`.

### Remote Validation After CodeCheck Fix

Commit: `65bfeed0f24403cd80a178a37659b2d67b6ce98e`

| Type | Command | Result | Time |
| --- | --- | --- | --- |
| CMake build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache bash build.sh -b cmake -t build -U on -X off -P off -s off -j 80 -u 80 -i on` | PASS | 491 s |
| UT fixed failures | `./build/tests/ut/ds_ut --gtest_filter=TopologyRecoveryManagerTest.UnboundRequestsDoNotConsumeClusterAdmission:TopologyRecoveryManagerTest.RequestsOnePayloadForIdenticalHighestEvidence` | PASS, 2 cases | 0.07 s |
| ST fixed failure | `TEST_SRCDIR=<repo> TEST_WORKSPACE=. ./build/tests/st/ds_st_kv_cache --gtest_filter=KVCacheClientServiceDiscoverySwitchBackTest.TestRecoverLocalWorker` | PASS, 1 case | 13.61 s |
