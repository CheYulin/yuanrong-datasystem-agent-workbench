# PR1405 `edfe60409` focused test inventory

Date: 2026-07-30
Mode: read-only
Target worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730`
Exact HEAD: `edfe60409f9ab3a3a95fc5689549d35a5c6ef134`

## Scope

This note inventories the one-shot added/modified UT/ST buckets selected by
`/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/run_pr1405_added_cases_gtest_20260730.sh`,
with extra focus on the `st_object` bucket that was timing out at `720s` when
run as one large in-process gtest bucket.

No code edits, push, PR update, or retest were performed.

## Exact binary/source registrations

### UT/ST grouped executables

- `ds_ut`, `ds_ut_object`: [tests/ut/CMakeLists.txt](/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730/tests/ut/CMakeLists.txt:153)
- `cluster_topology_contract_ut`: [tests/ut/CMakeLists.txt](/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730/tests/ut/CMakeLists.txt:211)
- `ds_st_object_cache`, `ds_st_kv_cache`: [tests/st/CMakeLists.txt](/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730/tests/st/CMakeLists.txt:122)
- CTest registration for ST grouped binaries: [tests/st/CMakeLists.txt](/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730/tests/st/CMakeLists.txt:329)

### `st_object` source registrations actually pulled by the regex

The script regex for `st_object` is broader than the 29 hand-listed non-Stream
case names. Because it matches whole suites, it also pulls:

- `MetadataRecoveryTest.FailoverRestoreObjectWithTtl`
- `MetadataRecoveryTest.RestartRestoreObjectWithTtl`
- `WorkerPushMetaTest.TestWorkerTimeoutAndPushMeta`

And because the metadata suites are `TEST_P(..., testing::Bool())`, each such
case expands to both `Etcd` and `Coordinator`.

Relevant sources:

- metadata recovery `TEST_P` + instantiation:
  [tests/st/worker/object_cache/metadata_recovery_test.cpp](/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730/tests/st/worker/object_cache/metadata_recovery_test.cpp:434)
- migration target admission tests:
  [tests/st/client/object_cache/migration_target_admission_test.cpp](/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730/tests/st/client/object_cache/migration_target_admission_test.cpp:201)
- worker/object client DFX tests:
  [tests/st/client/object_cache/client_dfx_test.cpp](/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730/tests/st/client/object_cache/client_dfx_test.cpp:2311)

## Exact current inventory from existing exact-path evidence

Exact-path CTest added-case run:

- run log:
  [run.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/run.log)
- summary:
  [summary.tsv](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/summary.tsv)

This run explicitly used:

- `W=/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730`
- `B=/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730/.clion-remote/pr1405-oneshot-squash-20260730/build`

Inventory:

| bucket | exact selected count | note |
|---|---:|---|
| `ut_core` | 160 | exact-path CTest summary |
| `ut_object` | 287 | 282 passed + 5 disabled/not-run |
| `ut_cluster` | 137 | exact-path CTest summary |
| `st_kv` | 8 | exact-path CTest summary |
| `st_object` | 40 | exact-path CTest summary |

`ut_object` disabled/not-run exact names:

- `MigrateDataHandlerSpillTest.TestMigrateObjectsByFastTransport`
- `MigrateDataHandlerSpillTest.TestReleaseFailEnqueueTask`
- `MigrateDataHandlerSpillTest.TestFastTransportRetryOnRpcError`
- `MigrateDataServiceTest.TestQueryMetaFromMasterMeetsRPCError`
- `WorkerOcServiceImplTest.ClearDataImplDispatchesMatchedObjectsToClearAndRebuild`

Evidence: [ut_object/ctest.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/ut_object/ctest.log:555)

## Exact `st_object` 40-case expansion

Exact-path case list and CTest timings:
[st_object/ctest.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/st_object/ctest.log:1)

Breakdown:

- 18 non-parameterized tests
- 22 parameterized metadata tests
  - `MetadataRecoveryTest`: 5 logical tests x 2 backends = 10
  - `MetadataRecoveryDisabledTest`: 4 logical tests x 2 backends = 8
  - `MetadataRecoveryL2Test`: 1 logical test x 2 backends = 2
  - `MetadataRecoveryDisabledL2Test`: 1 logical test x 2 backends = 2

Observed exact-path CTest wall time for the 40 individually discovered tests:

- `275.38s` total

Important: this `275.38s` is per-test CTest execution, not one single
in-process gtest process. It is useful for relative sizing, but it does not
disprove the user-observed `720s` timeout risk for the one-shot whole-bucket
gtest mode.

## Why the one-shot whole `st_object` gtest bucket is risky

Older exact-path in-process evidence under the same squash worktree shows that a
broader `ds_st_object_cache` bucket can run near the timeout cliff:

- [st-object.xml](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/st-affc33a9/case-results-st-object-affc33a9-20260730154457/st-object.xml)
- [summary.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/st-affc33a9/case-results-st-object-affc33a9-20260730154457/summary.log)

That exact-path run recorded:

- `46` tests
- `707.294s` total
- many slow metadata-recovery and combined-fault cases

So the safe reading is:

- `ctest` split-per-test mode finishes comfortably
- one large in-process `ds_st_object_cache --gtest_filter=...` bucket remains
  too close to the `720s` cliff

## Proposed safe sequential `st_object` split

Recommendation: split into **6** sequential gtest sub-buckets, all with
`ST_JOBS=1`, one process per bucket, and fresh result/output directories.

### Bucket 1: migration-target admission first

Purpose:
- design-case evidence first for H4/H2 admission gating

Filter:

```bash
--gtest_filter='MigrationTargetIsolationTest.LEVEL1_MigrationTargetFiltersIsolatedAndRecoveringWorker:MigrationTargetIsolationTest.LEVEL1_ObjectClientRejectsReadWriteDuringIsolationAndRecovering:MigrationTargetOomTest.LEVEL1_MigrationTargetFiltersOutOfMemoryWorker:MigrationTargetDrainingTest.LEVEL1_MigrationTargetFiltersDrainingWorker:MigrationTargetCombinedFaultTest.LEVEL1_MigrationTargetFiltersScaleInSourceAndIsolatedPeerTogether:OCClientSingleWorkerSelfHealingTest.CreateOutOfMemoryKeepsClientInitAdmission'
```

Expected count:
- 6

Observed exact-path CTest sum:
- about `85s`

Suggested timeout:
- `180s`

### Bucket 2: local/global isolation without metadata fanout

Purpose:
- early H1/H5 worker-survival evidence before slower metadata buckets

Filter:

```bash
--gtest_filter='WorkerPushMetaScaleOutFaultTest.LEVEL1_ScaleOutPreservesDataWhenWorkerIsLocallyIsolated:WorkerPushMetaScaleOutFaultTest.LEVEL1_ScaleOutSurvivesTransientGlobalBackendOutage:WorkerPushMetaTest.LEVEL1_TestKeepAliveLocalIsolationKeepsWorkerAliveAndProtectsPeerData:WorkerPushMetaTest.LEVEL1_TestPeerWorkerKvDataSurvivesLocalIsolationAndRecovery:WorkerPushMetaTest.LEVEL1_TestGlobalBackendOutageDoesNotSelfIsolateWorkers:WorkerPushMetaTest.LEVEL1_TestTopologyJitterDoesNotKillLocalWorker'
```

Expected count:
- 6

Observed exact-path CTest sum:
- about `89.7s`

Suggested timeout:
- `240s`

### Bucket 3: recovery evidence gate / fallback / primary / controlled exit

Purpose:
- H3 evidence gate and stale-primary/transport edge behavior

Filter:

```bash
--gtest_filter='WorkerPushMetaTest.LEVEL1_TestVoluntaryScaleDownStillExitsControlled:WorkerPushMetaTest.LEVEL1_TestKeepAliveLocalIsolationRecoversThroughEvidenceGate:WorkerPushMetaTest.LEVEL1_TestRecoveringWorkerFallsBackToLocalIsolatedOnDisconnect:WorkerPushMetaTest.TestWorkerTimeoutAndPushMeta:WorkerStalePrimaryTest.LEVEL1_IsolatedWorkerDoesNotPromotePeerWithoutRecoveryEvidence:WorkerPushMetaTransportTest.LEVEL1_TestTransportReadRejectedDuringIsolationAndBeforeRunning'
```

Expected count:
- 6

Observed exact-path CTest sum:
- about `98.7s`

Suggested timeout:
- `240s`

### Bucket 4: `MetadataRecoveryTest` family only

Purpose:
- isolate the heaviest parameterized metadata suite

Filter:

```bash
--gtest_filter='*MetadataRecoveryTest.MetadataOwnerRestart*:*MetadataRecoveryTest.UnrecoverableLocalDataClearedAfterMetadataOwnerRestart*:*MetadataRecoveryTest.MetadataRecoveryBestEffortRetryDoesNotBlockAvailability*:*MetadataRecoveryTest.FailoverRestoreObjectWithTtl*:*MetadataRecoveryTest.RestartRestoreObjectWithTtl*'
```

Expected count:
- 10

Why separate:
- older exact-path in-process evidence shows this suite carries several of the
  slowest cases

Suggested timeout:
- `240s`

### Bucket 5: `MetadataRecoveryDisabledTest` family only

Filter:

```bash
--gtest_filter='*MetadataRecoveryDisabledTest.MetadataOwnerRestart*:*MetadataRecoveryDisabledTest.UnrecoverableLocalDataClearedAfterMetadataOwnerRestart*:*MetadataRecoveryDisabledTest.OtherWorkersRecoverMetadataBeforeClearingDataWithoutMetadata*:*MetadataRecoveryDisabledTest.OrphanLocalDataRequiresRecoveryOrClearDataWithoutMeta*'
```

Expected count:
- 8

Suggested timeout:
- `240s`

### Bucket 6: L2 metadata recovery only

Filter:

```bash
--gtest_filter='*MetadataRecoveryL2Test.UnrecoverableL2DataClearedAfterMetadataOwnerRestart*:*MetadataRecoveryDisabledL2Test.UnrecoverableL2DataClearedAfterMetadataOwnerRestart*'
```

Expected count:
- 4

Suggested timeout:
- `120s`

## Why 6 buckets, not fewer

This split keeps the design-signal cases first and isolates the parameterized
metadata families, which are the most likely source of a whole-bucket timeout in
in-process gtest mode.

Expected sequential order:

1. Bucket 1 migration/admission
2. Bucket 2 local/global isolation
3. Bucket 3 evidence gate/fallback/primary/transport
4. Bucket 4 metadata recovery core
5. Bucket 5 metadata recovery disabled
6. Bucket 6 metadata recovery L2

If an early bucket fails, the design-case evidence is already collected before
the heavier metadata-only buckets start.

## Port and shared-state collision risks

Run sequentially only.

Known risks from these STs:

- local `127.0.0.1` worker/coordinator/etcd/mock service port allocation
- `/tmp` Unix-domain socket directory reuse
- RocksDB / cluster-root directories created under the build tree
- lingering keepalive / lease-expiry / recovery timers inside one gtest process
- metadata-owner restart and worker restart/kill sequences that can leave
  teardown lag

Safety constraints:

- keep `ST_JOBS=1`
- never overlap two `ds_st_*` buckets on the same node/build tree
- use a fresh result root per bucket
- prefer one process per bucket instead of one mega-filter
- do not mix `st_kv` and `st_object` concurrently

The biggest collision risk is not fixed-port hardcoding; it is teardown lag and
shared in-process cluster state when too many restart/isolation scenarios share
one gtest process.

## Existing XML/logs: valid vs stale for exact-equivalent `edfe60409`

### Valid for exact-equivalent `edfe60409`

These are valid exact-path evidence because they explicitly target the squash
worktree path and build directory:

- [ctest-added-case-regression-20260730193036/run.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/run.log)
- [ctest-added-case-regression-20260730193036/summary.tsv](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/summary.tsv)
- [ctest-added-case-regression-20260730193036/st_object/ctest.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/st_object/ctest.log)
- [ctest-added-case-regression-20260730193036/st_object/meta.txt](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/st_object/meta.txt)
- [ctest-added-case-regression-20260730193036/st_kv/ctest.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/st_kv/ctest.log)
- [ctest-added-case-regression-20260730193036/ut_core/ctest.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/ut_core/ctest.log)
- [ctest-added-case-regression-20260730193036/ut_object/ctest.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/ut_object/ctest.log)
- [ctest-added-case-regression-20260730193036/ut_cluster/ctest.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/ctest-added-case-regression-20260730193036/ut_cluster/ctest.log)

Advisory exact-path runtime-risk evidence, but not exact 40-case selector:

- [st-affc33a9/case-results-st-object-affc33a9-20260730154457/st-object.xml](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/st-affc33a9/case-results-st-object-affc33a9-20260730154457/st-object.xml)
- [st-affc33a9/case-results-st-object-affc33a9-20260730154457/summary.log](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/rebase-bf998d9d-20260730145114/st-affc33a9/case-results-st-object-affc33a9-20260730154457/summary.log)

### Stale for exact-equivalent `edfe60409`

All bundles that point to source-branch worktrees such as
`/home/worktrees/pr1405-source-d38d3c9ac` or commit-labelled directories like
`dc2f851`, `2a8e2bce`, `2cd1b92`, `0c39c24`, `6abb39e`, `ac4292165`,
`3004a98bb`, `e21bfe298` are stale for exact-equivalent `edfe60409`.

They remain useful only as historical runtime-shaping hints, not as exact-head
evidence.

Examples:

- [README-dc2f851-20260730.md](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/README-dc2f851-20260730.md)
- [new-st-all-2cd1b92-20260730141633/summary.tsv](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/new-st-all-2cd1b92-20260730141633/summary.tsv)
- [focused-ut-3004a98bb-20260730123757/summary.tsv](/home/t14s/workspace/git-repos/pr1405-one-shot-evidence/focused-ut-3004a98bb-20260730123757/summary.tsv)

## Bottom line

- The exact current inventory for the squash worktree is `160 / 287 / 137 / 8 / 40`.
- The exact `st_object` selector expands to `40`, not `29`, because suite-level
  matching pulls extra `MetadataRecoveryTest` members plus `TestWorkerTimeoutAndPushMeta`,
  and parameterized metadata cases double by backend.
- The safest focused split is **6 sequential `st_object` sub-buckets**.
