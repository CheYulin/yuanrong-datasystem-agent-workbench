# PR1405 `edfe60409` SDD Traceability Audit

- Worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730`
- Exact HEAD: `edfe60409f9ab3a3a95fc5689549d35a5c6ef134`
- Base: `7507f139fdb1222b29eb77542223a68dea6cd19f`
- Mode: read-only source / tests / BUILD / CMake audit; no build, no retest, no remote run.
- CodeGraph use: discovery-only on shared `/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph`, then exact-head `rg` / source / BUILD / CMake re-checks.

## Audit posture

1. `H1` and `H3` are explicitly anchored by existing local one-shot notes:
   - `H1` -> `ControlDegradedEvidenceReport()` / `ServingReady` vs `Complete`.
   - `H3` -> `RequestObjectCacheRecoveryEvidenceIfNeeded()` / `HandleObjectCacheRecoveryEvidenceReady()` and `objectCacheRecoveryRequestInFlight_`.
2. The checked-in handoff material does not spell out per-label definitions for `H2/H4/H5` or `D-S1..D-S8`; those rows below are exact-source inferences from the current test buckets.
3. I found no checked-in exact-head `edfe60409` UT/ST XML/log/pass summary. Older `c52...` / `bc320...` notes are not upgraded to this SHA.

## Traceability table

| Label | Exact gtest name(s) on `edfe60409` | Binary / build wiring | Status | Missing evidence / gap |
| --- | --- | --- | --- | --- |
| H1 | `WorkerRuntimeFacadeTopologyAvailabilityTest.ControlDegradedReopensLocalIsolationWithoutObjectCacheRecoveryEvidence`; `WorkerRuntimeFacadeTopologyAvailabilityTest.ControlDegradedDoesNotForgeAbsentResourceEvidenceIntoCompleteRecovery` | `ds_ut` via `tests/ut/worker/BUILD.bazel: worker_runtime_facade_topology_availability_test`; grouped into `tests/ut/CMakeLists.txt` `ds_ut` | implemented | No exact-head UT pass log/XML; still no checked-in `edfe60409` runtime proof for H1. |
| H2 *(inferred: local fault survival + peer data preserved)* | `WorkerPushMetaTest.LEVEL1_TestKeepAliveLocalIsolationKeepsWorkerAliveAndProtectsPeerData`; `WorkerPushMetaTest.LEVEL1_TestPeerWorkerKvDataSurvivesLocalIsolationAndRecovery` | `ds_st_object_cache` via `tests/st/client/object_cache/BUILD.bazel: client_dfx_test`; grouped by `tests/st/CMakeLists.txt` `DS_ST_OBJECT_CACHE_SRCS -> ds_st_object_cache` | implemented | Label-to-test mapping is only bucket-inferred; no exact-head ST pass evidence. |
| H3 | `WorkerOCServerRecoveryEvidenceTest.ConcurrentIncompleteReportsRequestRecoveryOnce`; `ReadyHandlerRejectsStaleGenerationAndClearsCurrentIncomplete`; `CurrentIncompleteTerminalClearsInFlightWithoutSynchronousRetry`; `CurrentCompleteTerminalRejectedByEpochDriftRetriesEvidenceRequest`; `CurrentCompleteTerminalAfterServingEpochReattachOpensMigration`; `RefreshFailurePropagatesPreservesCommitAndRetries`; `IncompleteRefreshRemainsMigrationFailClosed`; `AuthorizedCompleteGenerationCommitsAndForwardsComplete`; `BasicCommitRequestsFreshEvidenceFromRuntimePostCommitReport`; `NonServingObserveFailClosesBeforeCommitHelper` | `ds_ut_object` via `tests/ut/worker/BUILD.bazel: worker_oc_server_recovery_evidence_test`; explicit add in `tests/ut/CMakeLists.txt` `ds_ut_object` | implemented | Still no exact-head UT pass log/XML. More importantly, checked-in notes still do not prove bounded async terminal retry / observable terminal-failed closure on `edfe60409`; stale-generation early return remains a review risk area. |
| H4 *(inferred: global outage / topology jitter do not self-kill)* | `WorkerPushMetaTest.LEVEL1_TestGlobalBackendOutageDoesNotSelfIsolateWorkers`; `WorkerPushMetaTest.LEVEL1_TestTopologyJitterDoesNotKillLocalWorker`; supporting UT `WorkerRuntimeFacadeTopologyAvailabilityTest.HashRingSelfPassiveScaleDownDoesNotKillWorker` | ST in `ds_st_object_cache`; UT in `ds_ut` | implemented | Bucket-inferred only; no exact-head UT/ST pass evidence. |
| H5 *(inferred: evidence-gated reopen / fallback / stale-primary safety)* | `WorkerPushMetaTest.LEVEL1_TestKeepAliveLocalIsolationRecoversThroughEvidenceGate`; `WorkerPushMetaTest.LEVEL1_TestRecoveringWorkerFallsBackToLocalIsolatedOnDisconnect`; `WorkerStalePrimaryTest.LEVEL1_IsolatedWorkerDoesNotPromotePeerWithoutRecoveryEvidence`; supporting UT `WorkerRuntimeFacadeTopologyAvailabilityTest.PassiveIsolationReopensMigrationOnlyAfterFreshGenerationCompletes` | ST in `ds_st_object_cache`; UT in `ds_ut` | implemented | Bucket-inferred only; no exact-head UT/ST pass evidence. |
| D-S1 *(inferred: ordinary service reopens before `Complete`)* | `MetadataRecoveryTest.MetadataRecoveryBestEffortRetryDoesNotBlockAvailability`; supporting UT `WorkerRuntimeFacadeTopologyAvailabilityTest.TopologyAvailableOpensServingWhileReconciliationIsPending` | ST in `ds_st_object_cache`; UT in `ds_ut` | implemented | No exact-head UT/ST pass evidence. |
| D-S2 *(inferred: migration target rejects isolated / recovering worker)* | `MigrationTargetIsolationTest.LEVEL1_MigrationTargetFiltersIsolatedAndRecoveringWorker` | `ds_st_object_cache` via `migration_target_admission_test.cpp` | implemented | No exact-head ST pass evidence. |
| D-S3 *(inferred: direct object read/write rejected while isolated / recovering)* | `MigrationTargetIsolationTest.LEVEL1_ObjectClientRejectsReadWriteDuringIsolationAndRecovering` | `ds_st_object_cache` | implemented | No exact-head ST pass evidence. |
| D-S4 *(inferred: migration target rejects OOM worker)* | `MigrationTargetOomTest.LEVEL1_MigrationTargetFiltersOutOfMemoryWorker` | `ds_st_object_cache` | implemented | No exact-head ST pass evidence. |
| D-S5 *(inferred: migration target rejects draining worker)* | `MigrationTargetDrainingTest.LEVEL1_MigrationTargetFiltersDrainingWorker` | `ds_st_object_cache` | implemented | No exact-head ST pass evidence. |
| D-S6 *(inferred: combined draining + isolated peer remains fail-closed)* | `MigrationTargetCombinedFaultTest.LEVEL1_MigrationTargetFiltersScaleInSourceAndIsolatedPeerTogether` | `ds_st_object_cache` | implemented | No exact-head ST pass evidence. |
| D-S7 *(inferred: disabled-L2 unrecoverable cleanup, Etcd)* | `EtcdAndCoordinator/MetadataRecoveryDisabledL2Test.UnrecoverableL2DataClearedAfterMetadataOwnerRestart/Etcd` | `ds_st_object_cache` via `metadata_recovery_test.cpp` instantiation | implemented | No exact-head ST pass evidence. |
| D-S8 *(inferred: disabled-L2 unrecoverable cleanup, Coordinator)* | `EtcdAndCoordinator/MetadataRecoveryDisabledL2Test.UnrecoverableL2DataClearedAfterMetadataOwnerRestart/Coordinator` | `ds_st_object_cache` via `metadata_recovery_test.cpp` instantiation | implemented | No exact-head ST pass evidence. |
| voluntary scale-down | `WorkerPushMetaTest.LEVEL1_TestVoluntaryScaleDownStillExitsControlled`; `KVClientWriteRocksdbTest.TestNoneModeVoluntaryScaleDown`; `KVClientWriteRocksdbTest.TestSyncModeVoluntaryScaleDown`; `KVClientWriteRocksdbTest.TestASyncModeVoluntaryScaleDown`; `RoutingTopologyConvergenceTest.U6ScaleDownRejectsWritesAndConvergesRouting`; `KVCacheClientTest.TestRemoteGetFromSelfAddressScenarios1`; supporting UT `WorkerRuntimeFacadeTopologyAvailabilityTest.VoluntaryScaleDownIsNotReopenedByTopologyRecoveryEvidence` | ST split across `ds_st_object_cache` and `ds_st_kv_cache`; UT in `ds_ut`; Bazel targets present in `tests/st/client/object_cache/BUILD.bazel`, `tests/st/client/kv_cache/BUILD.bazel`, `tests/ut/worker/BUILD.bazel`; CMake groups via `DS_ST_OBJECT_CACHE_SRCS`, `DS_ST_KV_CACHE_SRCS`, `ds_ut` | implemented | No exact-head UT/ST pass evidence; checked-in docs keep this as a bucket, not a single explicit label definition. |

## Exact-head source checks that matter

- `src/datasystem/worker/runtime/worker_runtime_facade.cpp`
  - `ControlDegradedEvidenceReport()` still keeps `metadata/slot/ownership` false and only passes through real `resourceReady`.
  - `CheckAdmission()` still treats `MIGRATION_TARGET` as `RequiresCompleteReconciliation`.
  - `CommitRecoveryLocked()` still downgrades to `BasicServingEvidenceReport()` when the generation/topology epoch does not authorize full completion.
- `src/datasystem/worker/worker_oc_server.cpp`
  - `RequestObjectCacheRecoveryEvidenceIfNeeded()` clears `objectCacheRecoveryRequestInFlight_` only on sync dispatch error or complete evidence.
  - `HandleObjectCacheRecoveryEvidenceReady()` clears `objectCacheRecoveryRequestInFlight_` on committed terminal evidence and on current-generation retry path, but stale-generation early return still exits without an explicit clear in that branch.
- `src/datasystem/worker/object_cache/master_worker_oc_service_impl.cpp`
  - `PublishMeta`, `PublishMetaAsync`, and `ChangePrimaryCopy` still route through `WorkerAdmissionKind::MIGRATION_TARGET`.
- `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp`
  - `MigrateDataDirect` and `NotifyRemoteGet` still gate on `MIGRATION_TARGET`.
- `src/datasystem/worker/object_cache/service/worker_oc_service_migrate_impl.cpp`
  - `MigrateData` / `MigrateDataDirect` still perform runtime admission checks before proceeding.

## Key gaps

1. The checked-in repo/workbench state does not provide an exact-head `edfe60409` green UT/ST evidence bundle; every runtime row above is missing exact-head pass logs/XML.
2. `H2/H4/H5` and all `D-S1..D-S8` rows are bucket-level inferences because the checked-in notes do not preserve a first-class per-label mapping.
3. `H3` remains the sharpest semantic gap: the local source/tests improve generation fencing and in-flight cleanup behavior, but the checked-in notes still do not prove exact-head bounded async terminal retry plus observable terminal-failed closure.
4. Because `PublishMeta` / `PublishMetaAsync` / `ChangePrimaryCopy` / `MigrateData*` / `NotifyRemoteGet` are all still under `MIGRATION_TARGET`, any claim that voluntary scale-down and recovery-sensitive flows are exact-head GREEN still needs runtime evidence, not static traceability.
