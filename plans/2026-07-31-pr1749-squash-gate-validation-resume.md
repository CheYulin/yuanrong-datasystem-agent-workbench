# PR1749 Squash Gate Validation Resume

Updated: 2026-07-31 08:35 Asia/Shanghai

## Hard constraints

- Never push code to `openeuler/yuanrong-datasystem`.
- Push only to the verified `yche-huawei` fork.
- Do not run `/retest` manually.
- Continue from the exact branch and remote build below; do not start a cold duplicate build.
- CMake UT/ST is the test priority. Bazel only needs source build validation.
- Reuse `/home/ds-thirdparty-cache`; do not clean or expunge it.
- No more product fixes in this phase. Only build and test the current squash candidate.

## Delivery identity

- Worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730`
- Branch: `feat/worker-self-healing-main-20260716-squash-20260731`
- Base: `main/master = 7507f139fdb1222b29eb77542223a68dea6cd19f`
- HEAD: `9f88dde0eb5455b848e9932e97ddf41090e5afad`
- Commit count over base: `1`
- Fork ref: `origin/feat/worker-self-healing-main-20260716-squash-20260731`
- Pull request: https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1749
- PR state at creation: opened, non-WIP, conflict status clean.
- Existing PR source `feat/worker-self-healing-main-20260716` remains at `d02ca76eb`; do not update it in this phase.

## Diff summary

- 206 files, `+23502/-3371` locally.
- Production source: 118 files, `+9194/-2249`.
- Tests: 70 files, `+13569/-908`.
- Build/review tooling: 16 files, `+606/-27`.
- Other: 2 files, `+133/-187`.

## Completed gate fix

The stable `#8731` failure was caused by using the 70 percent source threshold as the target ceiling. The current head uses the eviction high watermark as the target boundary:

- 76 percent fresh projection: allowed.
- 89 percent: allowed.
- 90 percent boundary: rejected.
- Historical 90/92 percent repick cases remain rejected.

Fresh Tiantiyun evidence on the equivalent pre-squash tree:

- `ResourceManagerTest.SwapOnTriggerGivesFreshProjectionAndCorrectBudget`: 1/1 PASS, 3 ms.
- Three historical `MemoryRebalanceSchedulerTest` gate names: 3/3 PASS.
- `MemoryRebalanceSchedulerTest.*`: 22/22 PASS, 5 ms.
- Independent exact-head review: no P0/P1/P2.
- Evidence directory: `/home/cache/pr1405-8731-rebalance-gate-evidence/` on Tiantiyun.

## Disabled PR-added slow ST

Only PR-added, slow, non-one-shot-DryRun cases were disabled:

1. `WorkerPushMetaScaleOutFaultTest.DISABLED_LEVEL1_ScaleOutPreservesDataWhenWorkerIsLocallyIsolated`
2. `WorkerPushMetaScaleOutFaultTest.DISABLED_LEVEL1_ScaleOutSurvivesTransientGlobalBackendOutage`
3. `KVClientEtcdDfxTest.DISABLED_LEVEL1_TestStartingWorkerRejectsClientSetupBeforeReady`
4. `OCClientSingleWorkerSelfHealingTest.DISABLED_CreateOutOfMemoryKeepsClientInitAdmission`

Do not disable `WorkerPushMetaTransportTest.LEVEL1_TestTransportReadRejectedDuringIsolationAndBeforeRunning`; it covers the S1 isolation close-door contract. H1-H5, D-S1-D-S8, and voluntary scale-down remain enabled.

## Active Tiantiyun build

- Host alias: `tiantiyun-80c128g`
- tmux session: `pr1405-squash-9f88`
- Remote source: `/home/cache/pr1405-8731-rebalance-gate-src`
- Reused build: `/home/cache/build-pr1405-8731-rebalance-gate-cached`
- Evidence: `/home/cache/pr1405-squash-9f88-evidence`
- Build log: `/home/cache/pr1405-squash-9f88-evidence/build.log`
- Exit file: `/home/cache/pr1405-squash-9f88-evidence/build.exit`
- Current command builds `ds_ut_object`, `ds_st_object_cache`, and `ds_st_kv_cache` with `-j 16`.
- At handoff the build was still running in tmux while filling missing exact-hash third-party artifacts.
- `CMakeCache.txt` proves dependencies resolve under `/home/ds-thirdparty-cache`, including OpenSSL, gRPC, Protobuf, RocksDB, GTest, Zlib, and others.

First command after restart:

```bash
ssh tiantiyun-80c128g 'if test -f /home/cache/pr1405-squash-9f88-evidence/build.exit; then printf "exit="; cat /home/cache/pr1405-squash-9f88-evidence/build.exit; else echo running; fi; tmux ls 2>/dev/null | grep pr1405-squash-9f88 || true; tail -30 /home/cache/pr1405-squash-9f88-evidence/build.log'
```

If the first build exits zero, run the full default CMake build in the same build directory and same tmux session before tests. Do not clean:

```bash
cd /home/cache/build-pr1405-8731-rebalance-gate-cached
cmake --build . -j 16
```

## Focused verification matrix

Every filter must first run `--gtest_list_tests`; zero matches is failure, not PASS. Write XML, log, runtime, and exit status under `/home/cache/pr1405-squash-9f88-evidence`.

### Runtime and topology UT

- `WorkerRuntimeFacadeTopologyAvailabilityTest.ControlDegradedReopensLocalIsolationWithoutObjectCacheRecoveryEvidence`
- `WorkerRuntimeFacadeTopologyAvailabilityTest.ControlDegradedDoesNotForgeAbsentResourceEvidenceIntoCompleteRecovery`
- `WorkerRuntimeFacadeTopologyAvailabilityTest.TopologyAvailableOpensServingWhileReconciliationIsPending`
- `WorkerRuntimeFacadeTopologyAvailabilityTest.HashRingSelfPassiveScaleDownDoesNotKillWorker`
- `WorkerRuntimeFacadeTopologyAvailabilityTest.PassiveIsolationReopensMigrationOnlyAfterFreshGenerationCompletes`
- `WorkerRuntimeFacadeTopologyAvailabilityTest.VoluntaryScaleDownIsNotReopenedByTopologyRecoveryEvidence`
- `WorkerRuntimeStateTest.ReadGuardLinearizesIsolationTransition`
- `WorkerRuntimeStateTest.StaleCompleteEvidenceCannotReopenLocalIsolation`
- `TopologyControlHostTest.StopsEmptyRuntimeBeforeReusingClusterSlot`
- Eight focused `TopologyEngineTest` and `TopologyTaskExecutorTest` cases from the one-shot matrix.

### Object-cache UT and historical gate failures

- `WorkerOCServerRecoveryEvidenceTest.*`
- Ten `MigrateDataAdmissionTest` and `MigrateDataServiceTest` admission/drain cases.
- `MemoryRebalanceSchedulerTest.SuccessfulResultHoldsInflightToBlockImmediateRepick`
- `MemoryRebalanceSchedulerTest.SuccessHoldsInflightToPreventRepickToJustReceivedTarget`
- `MemoryRebalanceSchedulerTest.ConcurrentSourceRejectsOverflowTarget`
- `MemoryRebalanceSchedulerTest.ProjectedTargetUsesEvictionHighWatermarkBoundary`
- `ResourceManagerTest.SwapOnTriggerGivesFreshProjectionAndCorrectBudget`

### H1-H5 and D-S1-D-S8 object ST

- Keepalive local isolation, peer data survival, global outage, topology jitter, evidence-gated recovery, recovering disconnect fallback, stale primary, and transport isolation-read cases.
- `MetadataRecoveryBestEffortRetryDoesNotBlockAvailability` for Etcd and Coordinator.
- Migration target isolation, object direct read/write rejection, OOM, draining, and combined fault.
- Disabled-L2 unrecoverable cleanup for Etcd and Coordinator.

### Historical object ST

- `ObjectClientTest.QueryMetaAndCreateMetaConcurrently`
- `ObjectClientWithTokenTest.QueryMetaAndCreateMetaConcurrently`
- `RdmaObjectClientAuthorizationTest.TestBatchRemoteGet4`
- `SlotRecoveryEtcdTest.CanonicalizesLocalPendingTasks`

### Voluntary scale-down and historical KV ST

- Three `KVClientWriteRocksdbTest.*VoluntaryScaleDown` modes.
- `RoutingTopologyConvergenceTest.U6ScaleDownRejectsWritesAndConvergesRouting`
- `KVCacheClientTest.TestRemoteGetFromSelfAddressScenarios1`
- `KVCacheClientTest.GetTimeoutNotAddShmUnit`
- `STCClientDistMasterTest.TestSetNxKeepsExistingValueAcrossWorkers`
- `KVClientTransportSetTest.RoutedSetPublishesDataAndMetadata`
- `KVClientTransportSetTest.U1U2DirectWriteReadAtMetadataOwner`

### Other historical failures

- `EtcdStoreTest.TestKeepAliveLocalIsolationRecoveryTriggersLocalRecoveryOnce`
- `EtcdStoreTest.TestKeepAliveLocalIsolationRecoverySurvivesFirstLeaseRebuildFailure`
- `CoordinatorRaftNodeTest.RecoverUsesPersistedConfigurationWithoutBootstrapPeers`

`KVClientTransportSetConnectOptionsTest.DISABLED_MetaOwnerPolicyRoutesWritesToMetadataOwner` is already disabled and must be reported as disabled, not PASS.

## Next reporting and PR update

1. Report build status separately from UT and ST.
2. Report totals as passed/failed/disabled/not-run with exact names and runtime.
3. Classify failures as product, test, harness, or environment before changing code.
4. Update PR !1749 description only after fresh exact-head evidence exists.
5. Do not trigger `/retest`; PR creation already allows the normal gate flow.
