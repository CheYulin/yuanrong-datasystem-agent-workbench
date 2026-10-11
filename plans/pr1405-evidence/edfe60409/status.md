# PR1405 Stage Evidence: edfe60409

## Identity

- Local branch: `squash/pr1405-oneshot-20260730`
- Product head: `edfe60409f9ab3a3a95fc5689549d35a5c6ef134`
- Fork branch: `origin/feat/worker-self-healing-main-20260716`
- Verified fork head: `edfe60409f9ab3a3a95fc5689549d35a5c6ef134`
- Latest fetched `main/master`: `7507f139fdb1222b29eb77542223a68dea6cd19f`
- Divergence at capture: 57 commits ahead, 2 commits behind

The Tiantiyun validation worktree was checked out at `9b99a3169` with the two
stage fixes applied as a dirty binary diff. The local `9b99a3169..edfe60409`
binary diff and the remote dirty diff both have SHA-256:

`a583d7d725bb21e61053ca763531be85a309c59c7b30e6c0333ccfde58d4b9b0`

This proves byte-equivalent product changes, but it is not a literal clean
checkout of `edfe60409`. The post-rebase run must use a literal exact HEAD.

## Gate Results

| Bucket | Passed | Failed | Disabled/Skipped | Runtime |
|---|---:|---:|---:|---:|
| CI #8722 eight-case regression gate | 8 | 0 | 0 | See per-suite XML |
| Focused UT (`ds_ut`) | 144 | 0 | 0 | 0.952 s |
| Focused UT (`ds_ut_object`) | 272 | 0 | 5 disabled | 34.761 s |
| Focused UT (`cluster_topology_contract_ut`) | 79 | 0 | 0 | 10.550 s |
| Object-cache focused ST | 36 | 0 | 4 skipped | 716.946 s total |
| KV focused ST | 10 | 0 | 0 | 242.105 s |

Unique passed cases in this stage: **548**. There are **0 observed failures**,
**5 disabled UT cases**, and **4 skipped ST cases**.

The eight #8722 regressions are all green:

1. `ResourceManagerTest.SwapOnTriggerGivesFreshProjectionAndCorrectBudget`
2. `EtcdStoreTest.TestKeepAliveLocalIsolationRecoveryTriggersLocalRecoveryOnce`
3. `EtcdStoreTest.TestKeepAliveLocalIsolationRecoverySurvivesFirstLeaseRebuildFailure`
4. `KVCacheClientTest.GetTimeoutNotAddShmUnit`
5. `STCClientDistMasterTest.TestSetNxKeepsExistingValueAcrossWorkers`
6. `KVClientTransportSetTest.RoutedSetPublishesDataAndMetadata`
7. `KVClientTransportSetTest.U1U2DirectWriteReadAtMetadataOwner`
8. `KVClientTransportSetConnectOptionsTest.MetaOwnerPolicyRoutesWritesToMetadataOwner`

## Evidence

- `gate8/`: XML and manifest for the eight CI regression cases.
- `ut-st-kv/`: XML and metadata for three focused UT buckets and focused KV ST.
- `st-object/`: XML and metadata for four focused object-cache ST buckets.
- Runner: `plans/pr1405-evidence/run_edfe60409_ut_st_kv_gate.sh`
- Runner: `plans/pr1405-evidence/run_edfe60409_st_object_gate.sh`
- Remote build/cache: CMake products under the dedicated Tiantiyun worktree,
  reusing `/home/ds-thirdparty-cache`.

## Remaining Release Gaps

1. Four metadata-owner restart variants are source-level `GTEST_SKIP` for the
   known bRPC migration gap. They are not closed by this run.
2. H3 still lacks complete dynamic evidence for bounded asynchronous terminal
   retry, terminal-failure observability, and in-flight state cleanup.
3. Coordinator metadata tests log that topology metadata scan requires ETCD
   persistence. Their passing status does not close the metadata ownership gap.
4. Focused ST cases exceed the desired six-second per-case budget; this stage
   proves behavior, not the ST latency target.
5. The branch is two commits behind `main/master`; rebase, rebuild, and literal
   exact-HEAD reruns are required before merge readiness or CI retest.
