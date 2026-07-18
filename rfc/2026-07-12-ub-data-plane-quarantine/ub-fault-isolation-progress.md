# UB Fault Isolation Progress

Last updated: 2026-07-18 (P23 5/5 end-to-end acceptance signals complete; P24 latest-master local gates pass;
GitCode CodeCheck plus downstream x86/aarch64 build and ST fixes validated locally/xqyun; GitCode success gate remains)

## Build And Test Discipline

- Build host: `xqyun-32c32g`
- Latest target base: `main/master` at `9bc17ec95`.
- Latest pushed branch HEAD before CodeCheck fix: `ba6e4e1be`.
- Build tree: `/home/worktrees/ub-fault-isolation-main/.clion-remote/build`
- URMA Mock build tree: `/home/worktrees/ub-fault-isolation-main/.clion-remote/build-urma-mock`
- Third-party cache: `DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache`
- xqyun ST runtime tools: `PATH=/home/worktrees/ub-fault-isolation-main/.clion-remote/tools/bin:$PATH`
  for `etcd` and `etcdctl`.
- Focused CMake targets: `ds_ut_object` for worker/object admission and `ds_ut` for client transport admission.
- Focused Bazel target status: new UT targets run on xqyun with `/home/cache/tools/bazel-7.4.1`.
- Focused test filter:
  `UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable:WorkerOcServiceMigrateUbAdmissionTest.LocalProbeSuccessRestoresIncomingMigrationAdmission`

## Phase Status

| Phase | Scope | Status | New Cases | Total Focused Cases | Latest Focused Test Time |
| --- | --- | --- | ---: | ---: | --- |
| P0 | CLion/CMake cache path and remote build script | Done | 0 | 0 | Build/index only |
| P1 | UB failure classification and peer admission state machine | Done | 10 | 10 | ~0.09s |
| P2 | Block migration to quarantined target before remote API creation | Done | 1 | 11 | ~0.09s |
| P3 | Share admission state with worker migration paths | Done | 0 | 11 | covered by existing focused UT |
| P4 | Learn failed migration result into admission state | Done | 1 | 12 | 0.091s |
| P5 | Fail fast when read hits quarantined data worker | Done | 1 | 13 | 0.090s |
| P6 | Block publish and multipublish when local worker is quarantined | Done | 2 | 15 | 0.094s |
| P7 | Skip quarantined workers during migration redirect selection | Done | 1 | 16 | 0.075s |
| P8 | URMA Mock unavailable-to-available recovery case | Done | 1 ST | 17 | 3.853s gtest / 4.437s process |
| P9 | Review gap closure and PR readiness validation | Done | 3 UT | 19 UT + 1 ST | 14ms gtest / 0.098s UT process; 4117ms ST gtest / 4.995s ST process |
| P10 | Production migration-target hard-quarantine recovery probe adapter | Done | 2 UT | 21 UT + 1 ST | 16ms gtest / 0.110s UT process |
| P11 | Local-worker hard-quarantine recovery probe adapter | Done | 4 UT | 25 UT + 1 ST | 10ms gtest / 0.085s UT process |
| P12 | Client-put UB failure report into worker admission | Done | 2 UT | 27 UT + 1 ST | 9ms gtest / 0.096s UT process; 3220ms ST gtest / 3.782s ST process |
| P13 | Bazel target registration and xqyun Bazel 7.4.1 execution | Done | 5 Bazel targets | 27 UT + 1 ST | 5/5 Bazel targets passed in 423.279s final run |
| P14 | HTML coverage closure: health summary model, probe backoff visibility, RPC timeout boundary | Done | 3 UT | 30 UT + 1 ST | 2ms gtest / 0.079s process |
| P15 | Final xqyun focused CMake refresh after Bazel metadata fixes | Done | 0 | 30 UT + 1 ST | 14ms gtest / 0.317s process |
| P16 | GitCode review closure: keep recovered peers available during stale probe-start race | Done | 1 UT | 31 UT + 1 ST | 31 UT: 11ms gtest / 0.100s process; Bazel 5/5 in 21.570s |
| P17 | GitCode review closure: CLion `BUILD_DIR` rewrite and MSet hard-failure report priority | Done | 1 focused UT + 1 script check | 32 UT + 1 ST | CMake 2 tests: 2ms gtest / 0.107s process; Bazel 6/6 in 21.574s |
| P18 | Rebase to latest main and re-audit all HTML acceptance paths | Done | 1 build regression fix | 33 UT + 1 ST | CLion index 1493s; UT 0.119s + 0.114s; ST 5.190s |
| P19 | Client-local sender isolation and Direct/Batch Direct Read grouped admission | Done | 16 UT | 49 UT + 1 ST | client 14/14: 56ms; worker 2/2: 0.07s; Bazel: 1.0s test |
| P20 | Provider structured UB status for client Get and worker RemoteGet | Done | 10 UT | 59 UT + 1 ST | Worker 12/12: 3ms; URMA Mock target: 9.5s; new CQE UT: 1ms |
| P21 | GlobalFact AND UB admission with guarded recovery | Done | 10 UT | 69 UT + 1 ST | object 23/23: 7ms; role matrix 1/1: 0ms |
| P22 | Self-only O(N) health propagation | Done | 17 UT | 86 UT + 1 ST | lease sync 4/4: 3ms; fencing 3/3: 0ms |
| P23 | URMA Mock end-to-end acceptance closure | Done (5/5 E2E signals) | 13 UT + 7 ST | 99 UT + 8 ST | xqyun provider UT 15/15, client UT 28/28; latest ST 7/7 in 55.05s total |
| P24 | Final xqyun CMake/Bazel 7.4.1 and GitCode success gate | In progress (latest-master local gates pass; CodeCheck and downstream x86/aarch64 fixes validated; GitCode pending) | 3 UT-equivalent guards + 8 ST regression focus | 102 UT + 8 ST | CLion CMake 1165s; focused CMake 43/43 UT and 7/7 ST; Bazel UT 24/24 in 2.1s; two ST targets linked in 821.206s; CodeCheck-fix CMake 56/56 and Bazel target PASS; downstream build-fix validation: CMake mock 56/56 in 1s, Bazel wheel 787s, default CMake ds_ut build 924s; #7422 fix validation: 3 focused UT in 0s, single ST 22s, 8 ST in 181s |

## P24 CodeCheck Fix Evidence

The first GitCode retest on `ba6e4e1be` failed in `check_code` with 20 deterministic OpenLiBing findings:
6 level-1 issues, 13 level-2 issues, and 1 level-3 issue. The fix keeps behavior unchanged and addresses only
style/maintainability findings:

- replace Python `print` calls in the CLion compile-command rewrite helper with structured logging.
- add explicit default destructors for the new UB health/admission/classifier interfaces.
- replace fragile forward declarations with existing local headers where required by CodeCheck.
- name the reported magic values for provider status, probe buffer size, probe backoff, and transporter priorities.
- split long `TransportLayer::{Set,MSet}` retry paths and `ReplicaReader::Read` logic into smaller helper methods.
- flatten the nested batch-read exhaustion branch and replace the infinite `while (true)` loop with an explicit
  `keepRunning` loop condition.

Fresh xqyun validation after the CodeCheck fix:

```bash
cmake --build . -j16 --target ds_ut ds_ut_object
./tests/ut/ds_ut \
  --gtest_filter='ReplicaReaderAdmissionTest.*:TransportLayerAdmissionTest.*:UbTransporterMSetFailureReportTest.*'
./tests/ut/ds_ut_object \
  --gtest_filter='WorkerOcServiceGetUbAdmissionTest.*:PeerUbAdmissionTest.*:UbFailureClassifierTest.*:UbHealthSummaryCacheTest.*:UrmaRecoveryProbeBufferTest.*'
/home/cache/tools/bazel-7.4.1 --output_user_root=/home/cache/bazel-ds test \
  //tests/ut/client:p19_transport_admission_test \
  --config=test --config=urma_mock --jobs=16 --local_test_jobs=4 \
  --nocache_test_results --test_output=errors
```

- CMake incremental build with cached third-party dependencies passed in `476.71 s` at `-j16`.
- CMake client admission suites passed `21/21` in `0.95 s` process time.
- CMake worker/provider admission suites passed `35/35` in `0.08 s` process time.
- Bazel 7.4.1 `//tests/ut/client:p19_transport_admission_test` passed `1/1` target, `24/24` GTest cases,
  `2.0 s` test time, and `390.162 s` elapsed after cache-key reanalysis.
- `git diff --check` and Python `py_compile` passed before this evidence update; rerun before committing.

Second CodeCheck retest on `c8baac544` reduced the report to 4 minor issues: three nested-depth findings in
`ReplicaReader::{Read,ReadBatch}` and one old long-function finding caused by formatting-only churn in
`UrmaManager::UrmaGatherWriteImpl`. The follow-up fix:

- moves batch unavailable-replica advancement into `AdvanceUnavailableReplica`.
- flattens unary unavailable-replica handling in `ReplicaReader::Read`.
- restores `urma_manager.cpp` formatting-only churn so the final diff touches only the recovery probe segment-size
  constant and its two uses.
- fixes two timing/order assumptions in the new transport admission UT: probe observation now uses a named `3 s`
  timeout, and the concurrent admitted-Set test no longer assumes `std::async` launch order.

Fresh xqyun validation after the second CodeCheck fix:

- CMake incremental `ds_ut` build passed in `62 s` at `-j16` with cached third-party dependencies.
- Client admission suites passed `21/21` twice consecutively: `756 ms` and `779 ms`.
- Worker/provider admission suites passed `35/35` in `3 ms`.
- Bazel 7.4.1 `//tests/ut/client:p19_transport_admission_test` passed `1/1` target, `24/24` GTest cases,
  `1.8 s` test time, and `29.235 s` elapsed with 10 actions.

Third CodeCheck retest on `969717452` left one minor nested-depth finding in `ReplicaReader::Read`. The final fix
fully flattens unavailable-source handling by using separate `unavailableSource && !hasNextReplica` and
`unavailableSource` branches.

Fresh xqyun validation after the final flattening:

- CMake incremental `ds_ut` build passed in `50 s` at `-j16`.
- Client admission suites passed `21/21` in `808 ms`.
- Worker/provider admission suites passed `35/35` in `12 ms`.
- Bazel 7.4.1 `//tests/ut/client:p19_transport_admission_test` passed `1/1` target, `24/24` GTest cases,
  `1.9 s` test time, and `23.919 s` elapsed with 6 actions.

Fourth GitCode retest on `9cc6ba49a` passed CodeCheck, license, SCA, and x86_64 `check_build`, then exposed two
downstream build gaps:

- aarch64 default CMake `ds_ut` failed compiling `tests/ut/client/transport_admission_test.cpp` because the
  URMA recovery probe unit test included `UrmaManager`, which includes the real `ub/umdk/urma/urma_api.h` header
  when neither `USE_URMA` nor `USE_URMA_MOCK` is enabled. The fix keeps all non-URMA admission tests in default
  `ds_ut`, but compiles the dedicated URMA probe-segment assertion only for URMA or URMA Mock builds and skips it
  otherwise.
- openyuanrong Bazel wheel failed linking `datasystem_coordinator` with unresolved `fast_transport_base` symbols
  referenced by `common_shared_memory`. The symbols exist in `common_rdma`, but Bazel placed them in a static
  `--start-lib/--end-lib` group before later references from `common_shared_memory`; the fix adds the coordinator
  dependency and marks `common_rdma` `alwayslink` so wheel linkage extracts the required objects deterministically.

Fresh xqyun validation after the downstream build fixes:

- CMake URMA Mock `ds_ut` build at `-j16` passed in `64 s`; focused
  `TransportLayerAdmissionTest.*:DataPlaneManagerAdmissionTest.*:ReplicaReaderAdmissionTest.*:UrmaRecoveryProbeBufferTest.*`
  passed in `1 s`.
- Bazel 7.4.1 `//src/datasystem/coordinator:datasystem_coordinator` passed with `/home/cache/bazel-ds` in `10 s`.
- Bazel 7.4.1 `//bazel:datasystem_wheel` passed with `/home/cache/bazel-ds` in `787 s`; warm recheck passed in `1 s`.
- Default non-mock CMake `ds_ut` build passed with `DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache` in `924 s`.
  An earlier no-cache attempt was interrupted immediately after detecting the wrong cache path and was rerun with
  the required third-party cache.

Fifth GitCode retest after `293aeacbb` passed CodeCheck, license, SCA, and x86_64 `check_build`, then exposed
aarch64 ST failures in `CoordinatorServiceDiscoveryTest.*` plus the role-matrix unit case. The local/xqyun
root-cause fixes are:

- UB health lease sidecar keys now live under `/datasystem_ub_health` instead of appending `_ub_health` inside the
  topology membership root. This avoids Coordinator recovery rejecting sidecar range operations as crossing the
  protected topology keyspace.
- Member watch registration retries transient `K_NOT_READY` inside the one-shot `TopologyEngine::StartMemberRole`
  path. This covers the coordinator-service-discovery startup window where the worker may register watches before the
  coordinator has fully established its membership recovery context, without adding a broad Worker startup barrier.
- The role-matrix test now parses `MEMBER_ADDRESS` into `HostPort` before calling `ObjectEndpointPolicy`; the old
  direct constructor did not parse `host:port`, so expected-allowed roles were reported as denied.
- Disabled-RocksDB ST startup no longer creates an async RocksStore pool before returning a disabled store. The
  failing aarch64 ST process previously stayed alive but never wrote the worker health file after consuming
  `master.disableRocksDb` during pre-start RocksDB initialization.

Fresh xqyun validation after the aarch64 ST fixes:

- Incremental `datasystem_worker_bin ds_st_object_cache` build passed in `4 s` at `-j16` with
  `DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache`.
- `TopologyRecoveryManagerTest.UbHealthSidecarRangeStaysOutsideTopologyRoot` plus the existing topology boundary
  guard passed `2/2` in `7 ms` GTest time and `0 s` shell seconds.
- `ObjectEndpointPolicyTest.DataPlaneAdmissionEnforcesRoleSpecificGlobalFact` passed `1/1` in `0 ms` GTest time and
  `0 s` shell seconds.
- `CoordinatorServiceDiscoveryTest.RandomSelectsReadyWorker` passed `1/1` in `22284 ms` GTest time and `22 s` shell
  seconds.
- The eight `CoordinatorServiceDiscoveryTest` cases that failed in #7422 passed together in `180663 ms` GTest time
  and `181 s` shell seconds.

Sixth GitCode retest on `6e00d22af` passed CodeCheck, license, SCA, and x86_64 `check_build`; aarch64 completed the
3498 default tests but then timed out in six scale/embedded ST cases with `keepAliveTimeoutTimer` evidence. The
candidate broad-risk change was the extra Worker startup `WaitForTopologyReady()` barrier inserted before UB health
lease sync. It is not required for the original #7422 CoordinatorServiceDiscovery failure once watch registration
retry is present, so the barrier was removed while keeping the sidecar, watch-retry, and disabled-RocksDB fixes.

Fresh xqyun validation after removing that startup barrier:

- Incremental `datasystem_worker_bin ds_st_object_cache` build passed in `4 s` at `-j16`.
- The same eight `CoordinatorServiceDiscoveryTest` cases passed together in `179688 ms` GTest time and `180 s` shell
  seconds, proving the startup barrier was not necessary for the aarch64 CoordinatorServiceDiscovery fix.

## P21 Completion Evidence

P21 added 10 UT in total. The final five cases added after the initial role and ingress gates are:

- `RedirectTargetSelectionSkipsGlobalFactDeniedWorkers`: `0 ms`; a FAILED candidate is skipped before redirect.
- `ProbeSuccessDoesNotOverridePublishGlobalFactDeny`: `0 ms`; a probe-time ACTIVE-to-FAILED transition keeps local publish quarantined.
- `ProbeSuccessDoesNotOverrideMultiPublishGlobalFactDeny`: `0 ms`; MultiPublish uses the same guarded recovery contract.
- `ProbeSuccessDoesNotOverrideIncomingMigrationGlobalFactDeny`: `1 ms` in the final suite (`2 ms` RED); incoming migration remains unavailable.
- `OutgoingMigrationUsesTopologySpecificSourceAndTargetRoles`: `0 ms`; four decisions prove ordinary ACTIVE-only roles and topology-specific LEAVING/JOINING allowances.

Final xqyun cached CMake evidence:

```bash
./tests/ut/ds_ut_object \
  --gtest_filter='DataMigratorUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:WorkerOcServiceMigrateUbAdmissionTest.*'
./tests/ut/ds_ut \
  --gtest_filter='ObjectEndpointPolicyTest.DataPlaneAdmissionEnforcesRoleSpecificGlobalFact'
```

- Object admission suites: 23/23 passed, 7 ms GTest, 0.072 s process.
- Role matrix: 1/1 passed, 0 ms GTest, 0.084 s process; it evaluates 36 state/role decisions.
- HTML UC-5 and UC-6 now have complete P21 unit-level admission and guarded-recovery coverage. Their required URMA Mock end-to-end evidence remains assigned to P23.

## P22 Incremental Evidence

- `UbHealthSummaryCacheTest.RejectsWrongIncarnationAndStaleEpoch`: `0 ms`; only the topology-expected Worker incarnation and a non-stale epoch can update the candidate cache.
- `UbHealthSummaryCacheTest.StoresExactlyOneRecordPerWorkerAtClusterScale`: `0 ms`; 1000 single-Worker summaries produce exactly 1000 cache records, with no nested peer collection.
- `PeerUbAdmissionTest.SelfHealthSummaryNeverExportsObservedPeers`: `0 ms`; a peer failure cannot leak into the serving Worker's self-only summary.
- `WorkerServiceUbHealthTest.HeartbeatCarriesExactlyOneCompleteSelfSummary`: `1 ms`; heartbeat exports exactly one serving-Worker identity/incarnation/state/epoch/reason/backoff record.
- `WorkerServiceUbHealthTest.HeartbeatConsumerAppliesOnlyMatchingIncarnation`: `0 ms`; heartbeat consumption rejects a summary whose incarnation does not match `worker_start_id`.
- P22 heartbeat/cache aggregate: 5/5 passed, `1 ms` GTest and `0.059 s` process time on xqyun. The proto/header change caused a one-time dependent rebuild and is not counted as test execution time.
- `WorkerOcServiceGetUbAdmissionTest.ClientWritebackFailureQuarantinesProviderSelfAdmission`: `0 ms`; Worker-to-client provider ERROR4 updates the executing Worker's self admission.
- `WorkerOcServiceGetUbAdmissionTest.RemoteGetWritebackFailureQuarantinesProviderSelfAdmission`: `0 ms`; Worker-to-worker provider ERROR4 updates the same self admission while preserving structured response detail.
- P22 provider self-admission suite: 14/14 passed, `3 ms` GTest and `0.066 s` process time on xqyun.
- `PeerUbAdmissionTest.RecoveryProbeHonorsObservableExponentialBackoffDeadline`: `0 ms`; an injected clock proves zero probes before the deadline, exponential extension after failure, and deadline reset after successful recovery.
- P21+P22 regression after enabling the production `1 s` base backoff: 51/51 passed, `9 ms` GTest and `0.074 s` process time on xqyun.
- `WorkerServiceUbHealthTest.HeartbeatNotifiesCandidateCacheOnlyAfterFencing`: `0 ms`; only a summary accepted by incarnation/epoch fencing notifies the candidate cache. The three heartbeat tests pass in `1 ms` GTest and `0.072 s` process time.
- `RoutingTest.UbHealthSummaryFiltersCandidateUntilFencedRecovery`: `0 ms`; an unavailable candidate is skipped, a stale recovery is ignored, and a newer writable epoch re-admits it. Process time is `0.079 s`.
- Ordinary routed Put/Get now reads the in-memory `UbHealthFilter`; heartbeat updates it asynchronously through a weak callback.
- `UbHealthLeaseSyncTest.*`: 4/4 passed, `3 ms` GTest and `0.048 s` process time on xqyun. The
  1000-Worker case took `2 ms` and stored exactly 1000 single-Worker records; TTL deletion removed only the expired
  peer, while a malformed live value conservatively retained the last quarantine.
- `PeerUbAdmissionTest.LeaseSnapshot*`: 3/3 passed, `0 ms` GTest and `0.068 s` process time. Snapshot removal drops
  only global state, a lower epoch cannot recover the same incarnation, and a retired incarnation cannot overwrite
  the current process generation.
- The production `datasystem_worker_bin` target builds with the lease synchronizer wired after READY publication and
  stopped before the coordination backend. ETCD binds the sidecar record to the membership lease; Coordinator uses
  the same membership TTL. P22 is complete with 17 new UT and 86 cumulative UT plus 1 ST.
- Bazel 7.4.1 verification passed for both new focused targets: `peer_ub_admission_test` in `1.6 s` and
  `ub_health_lease_sync_test` in `4.2 s`; the combined cached run completed in `45.601 s`.
- Production Bazel target `//src/datasystem/worker:datasystem_worker` passed in `1062.073 s` (`17:43.17` shell time,
  2144 executed actions). This one-time run rebuilt production dependencies after the Bazel configuration changed;
  it is recorded separately from focused test execution time.

## P23 Incremental Evidence

- `UrmaObjectClientTest.RpcTimeoutDoesNotQuarantineProviderUbWrite`: 1/1 passed, `4222 ms` GTest and `4.840 s`
  process time on xqyun. The first Get returned `K_RPC_DEADLINE_EXCEEDED`; after removing only the metadata delay,
  the same provider path completed a UB write and returned the original payload. This proves an RPC timeout remains
  suspect and does not hard-quarantine the provider as ERROR4.
- `WorkerOcServiceImplTest.WorkerWorkerServiceInheritsLocalWorkerAddress`: 1/1 passed, `3 ms` GTest and `0.057 s`
  process time. It prevents provider-local failures from being recorded against an empty generated-service address.
- `UrmaDisableFallbackTest.SelfHealthLeasePropagatesFailureAndRestartRecovery`: 1/1 passed, `8872 ms` GTest and
  `9.304 s` process time. A worker-to-worker batch RemoteGet receives CQE status 4, publishes the provider Worker's
  self-only lease as non-writable, then a Worker restart publishes a new writable incarnation.
- The batch completion path now preserves the raw CQE status before returning, adds structured provider failure
  detail to covered responses, and updates provider self-admission once per failed batch event. The existing
  `UrmaSendJettyFaultTest.WaitToFinishReturnsRawCqeStatus` Bazel contract passed in `11.5 s` test time.
- Bazel 7.4.1 used `/home/cache/bazel-ds` as `output_user_root`. Its first `--config=urma_mock` cache warm-up took
  `18:23.04` wall time and 2247 actions; this one-time build cost is recorded separately from test execution time.
- P23 has added 1 UT and 2 ST so far. Cumulative focused coverage is 87 UT plus 3 ST; both new ST cases remain below
  the `30 s` completion-spec limit. Timeout, self-health propagation, and restart recovery are covered; explicit
  public provider detail, client sender recovery, and migration recovery acceptance signals remain.
- Final exact-source CMake regression: 15/15 provider/address UT passed in `7 ms` GTest and `0.078 s` process time;
  both P23 ST passed together in `13271 ms` GTest and `14.008 s` process time. The self-health recovery case took
  `9186 ms` in this combined run.
- Final warm-cache Bazel 7.4.1 CQE contract passed in `10.2 s` test time and `49.115 s` wall time with 6 actions and
  `--jobs=16`.
- The self-health ST was strengthened without adding another long-running case. RED failed in `3277 ms` because the
  provider's explicit `K_URMA_ERROR` and `cqe status: 4` reached the requester Worker but the client-facing remote Get
  boundary folded the code to `K_RUNTIME_ERROR`. GREEN preserves `K_URMA_ERROR` and
  `K_URMA_DATA_WORKER_UNAVAILABLE` through that boundary.
- The strengthened ST passed 1/1 in `8416 ms` GTest time and `9 s` measured wall time. It verifies the first request
  returns recognizable ERROR4 evidence, the second request returns `K_URMA_DATA_WORKER_UNAVAILABLE`, and the URMA
  completion-check injection count remains exactly one, proving the quarantined read fails fast without another data
  operation. Restarting Worker 0 then publishes a new writable incarnation and restores the path.
- The cached CLion/CMake build used `/home/cache/ds-thirdparty-cache`; the changed Worker object compiled in `44.1818 s`
  and the ST executable relinked in `12.6567 s`. The host has no `/usr/bin/time`, and its CTest metadata currently
  references a missing generated UT include, so the test binary was run directly with the same GTest filter and a
  `30 s` per-case budget.
- Client-local sender recovery RED failed to compile in `27.009 s` because neither the dedicated
  `ProbeUbConnection` contract nor an injectable probe cooldown existed. GREEN reuses the transport reconcile thread:
  business Create/Set/MSet remain blocked, while a cooldown-triggered UB handshake probes recovery without
  carrying object data. Failed probes use capped exponential backoff.
- `DedicatedProbeRestoresClientLocalSenderWithoutBusinessRetry` passed in `100 ms`. It proves the first ERROR4 is
  returned, the second Set performs neither a UB write nor a recovery probe, and only the later dedicated probe
  reopens sender admission. Two consecutive post-recovery Sets also prove stale buffer failure evidence cannot
  immediately re-quarantine the sender.
- `GlobalSnapshotDenyKeepsClientLocalSenderQuarantinedUntilReadmitted` passed in `304 ms`. The first scheduled probe
  is rejected because the latest WorkerSnapshot excludes the endpoint; local quarantine remains closed. A newer
  snapshot readmits the endpoint, the next backoff probe succeeds, and writes resume.
- `DataPlaneManagerAdmissionTest.ProbeCommitAndSnapshotPostCheckAreAtomic` passed in `51 ms`. A concurrent deny
  snapshot is blocked until the successful recovery callback commits under the same snapshot read lock, closing the
  post-check-to-commit race.
- Review-driven RED then exposed three prior gaps: ERROR4 plus `K_URMA_NEED_CONNECT` still rebuilt UB on the business
  thread; the initial probe reset the shared transporter; and stale `ubFailureReportRc` re-quarantined a recovered
  buffer. GREEN returns the first explicit ERROR4, uses TCP only for reference cleanup, performs an independent
  handshake without resetting the shared transporter, and clears the per-attempt report slot before every Set.
- The focused Bazel target passed 1/1 in `1.5 s` test time and `25.574 s` elapsed time with seven hot-cache actions.
  Its full binary passed 17/17 cases in `517 ms` GTest time and `1.400 s` measured remote wall time. P23 now has 4
  new UT and 2 new ST, for 90 focused UT plus 3 ST cumulatively.
- Both IDE-indexed cached CMake paths compile the production client probe: ordinary `datasystem` plus `ds_ut_object`
  passed (`ds_ut_object` relink `11.9334 s`), and the URMA Mock `datasystem` target passed with `USE_URMA` enabled
  (shared-library link `5.4333 s`).
- `UrmaClientSenderRecoveryTest.ClientSenderProbeWaitsForUrmaDataPlaneRecovery` first produced a focused RED in
  `8352 ms`: the cooldown probe completed only the RPC handshake, reopened the sender, and let the next business Set
  observe injected CQE status 4. Review then exposed that a one-byte READ could not prove WRITE capability. GREEN
  advertises a dedicated non-business Worker byte in the handshake, performs a one-byte URMA WRITE, and waits up to
  `500 ms` for its CQE before committing recovery. The strengthened case passed 1/1 in `5643 ms` GTest and `5.764 s`
  process time: the first CQE 4 is returned, the immediate retry performs no UB operator call, a failed WRITE probe
  keeps the sender quarantined, and only a later successful WRITE probe restores a large UB Set.
- `TransportLayerAdmissionTest.RemovedFailureEndpointRecoversThroughAnotherAdmittedWorker` first failed in
  `1049 ms` because the sender retained a departed failure endpoint forever. GREEN selects one currently admitted
  Worker when that endpoint is absent, without O(N) probing. The case passed 1/1 in `293 ms` process time.
- The URMA Mock CMake `ds_ut` transport-admission aggregate now passes 12/12 in `654 ms` GTest and `0.794 s` process
  time. The strengthened ST initially exceeded its `30 s` budget because unrelated client warmup retried metadata
  until its 10-second object TTL expired; a GDB backtrace proved the main thread was in
  `DoWarmupClientWorkerConnection -> QueryWithRetry`. The focused recovery case now explicitly skips that unrelated
  warmup and remains below the completion-spec limit.
- The cached CLion-equivalent URMA Mock build completed in `238 s`, produced 1091 compile database entries, and all
  19 third-party cache checks completed in `1 s` using `/home/ds-thirdparty-cache` on the temporary host.
- `TransportLayerAdmissionTest.FailedRecoveryProbeRotatesAcrossAdmittedWorkers` first failed because every retry
  selected the same old Worker that returned `K_NOT_SUPPORTED`. GREEN keeps each probe O(1) while rotating across
  the sorted admitted snapshot on subsequent retries; the focused case passed in `108 ms`.
- `DataPlaneManagerAdmissionTest.ProbeRequiresPublishedWorkerSnapshot` first failed because an absent GlobalFact
  snapshot allowed recovery to commit. GREEN fails closed with `K_NOT_READY`; the case passed in `0 ms`, with zero
  data-plane probes and no recovery callback. The two review-driven cases passed together in `294 ms` process time.
- The updated transport-admission aggregate passes 14/14 in `667 ms` GTest time. A stable-product stress run repeated
  the complete aggregate 10 times for 140/140 passes; the earlier isolated failure occurred while two accidental
  builds were concurrently writing the same build tree and did not reproduce after the tree became quiescent.
- The sender-recovery ST now waits for the business Set to leave `K_URMA_WORKER_UNAVAILABLE`, rather than treating
  the probe post event as completion. It passed 1/1 in `5665 ms` GTest and `5.77 s` process time, proving admission
  reopens only after the WRITE CQE succeeds.
- The latest cached CLion-equivalent URMA Mock build completed in `173 s`, produced 1091 compile database entries,
  and all 19 third-party libraries were cache hits using `/home/ds-thirdparty-cache` on the temporary host.
- `WorkerSnapshotTest.ExposesOnlyActiveMembersForClientWriteRecoveryProbe` and
  `DataPlaneManagerAdmissionTest.ProbeRejectsMembershipWorkerDeniedByGlobalFact` close the client-probe GlobalFact
  gap: the transport snapshot still retains all membership states for connection reconciliation, while only ACTIVE
  members enter a pre-sorted WRITE-probe set. The focused behavior passed in `0 ms` and `62 ms` respectively.
- Probe rotation now sorts and indexes candidates once when publishing a snapshot. Each retry performs O(1) preferred
  lookup, next-index rotation, and writable post-check instead of copying and sorting all Workers.
- `TransportLayerAdmissionTest.ShutdownWaitsForAdmittedUbOperationsBeforeClosingSender` produced a `1 ms` RED because
  Shutdown completed while two admitted UB Sets still held sender read admission. GREEN serializes shutdown publication
  through the sender write lock; the case passed in `51 ms`, and new UB admission observes `K_SHUTTING_DOWN`.
- Review of `UrmaRecoveryProbeBufferTest.UnregistersDedicatedSegmentBeforeBackingMemoryRelease` exposed that a
  service-owned `MemMmap` also enters Remote-H2D registration under the supported NPU combination and that runtime
  URMA unregister failure could leave a stale registration. The replacement RED failed to compile on xqyun because
  `GetRecoveryProbeSegmentInfo` did not exist. GREEN makes `UrmaManager` own one stable 4 KiB anonymous segment,
  registers it only with URMA, and releases it only after segment-map destruction and URMA uninitialization. The
  service now publishes the manager-owned address without owning or unmapping its backing memory.
- The review-closure aggregate passed 21/21 in `773 ms` GTest and `0.90 s` process time; the prior source revision also
  repeated the full aggregate 10 times for 210/210 stable passes.
- A probe-specific CQE synchronization RED failed in `7454 ms` because the post-completion point did not exist and
  recovery committed before the assertion. GREEN pauses after `WaitFastTransportEvent` has consumed the probe's own
  successful event but before the recovery callback: business Set remains unavailable there, then resumes only after
  the pause is released. The strengthened ST passed in `6731 ms` GTest and `6.88 s` process time.
- This incremental RED/GREEN run used the cached CLion-equivalent CMake tree on `tiantiyun-80c128g` only because both
  xqyun aliases timed out during SSH banner exchange after the local reboot. It is development evidence, not the P24
  gate; all final CMake, Bazel 7.4.1, and GitCode-success evidence must still come from `xqyun-32c32g`.
- xqyun recovered on 2026-07-17. The repository CLion entrypoint
  `scripts/clion_remote_build.sh urma-mock-tests-index` completed with `JOBS=12`, 19/19 third-party cache hits from
  `/home/cache/ds-thirdparty-cache`, 1091 compile-database entries, and `1289 s` source-build time. The host had about
  `29 GiB` available before the build and did not exhibit memory pressure.
- The current-source xqyun aggregate passed 21/21 focused UT in `1.10 s` process time. Three effective P23 ST filters
  passed serially: client sender probe recovery `6578 ms` GTest / `6808 ms` process, provider RPC-timeout boundary
  `4360 ms` / `4871 ms`, and self-health lease failure plus restart recovery `9105 ms` / `9679 ms`. An initial typo in
  the RPC-timeout suite matched zero tests; it was corrected to `UrmaObjectClientTest` and the zero-case run is not
  counted.
- Public provider-detail RED proved that Worker 0's structured batch RemoteGet failure updated Worker 1 admission but
  was discarded before the client-facing `GetRspPb`. GREEN binds each trusted remote provider detail to its object,
  preserves it across protobuf response consumption, and attaches it only when the final status has the exact same
  code and message. The SDK status includes `failed_endpoint`, `operator_worker`,
  `failure_side=provider_local_ub_write`, and the available raw `cqe_status=4`; it deliberately does not invent an
  unavailable `provider_status`.
- The client-to-worker tracker accepts a forwarded provider failure only when `operator_worker` matches the RPC
  worker. The self-health ST additionally proves that a forwarded Worker 0 failure does not quarantine a healthy
  Worker 1 write path. Normal Get, split batch Get, and oversized chunk Get now share the same structured-failure
  decoder instead of folding the latter two paths to `K_NOT_FOUND`.
- `WorkerOcServiceGetUbAdmissionTest.GetRequestAttachesOnlyMatchingObjectProviderFailure` adds the deterministic
  object/status binding contract. The latest provider-detail aggregate passed 15/15 in `8 ms` GTest; the preceding
  measured focused run took `69 ms` process time. The client transport/admission aggregate also passed 21/21.
- Two new controlled-path ST cases cover the SDK split-batch and oversized-chunk paths. The same-process run passed
  2/2 in `8245 ms` GTest and `8962 ms` process time; explicit entry counters prove the oversized case enters both
  batch and chunk processing, while the split case enters batch processing without entering the chunk path.
- Independent review found and closed three P1 issues: cross-worker tracker attribution, response-detail/status
  mismatch, and structured-error loss in batch/chunk reads. Its fresh same-process verification passed the two new ST
  cases in `8232 ms` GTest and `8919 ms` process time, plus the focused matching-selection UT 1/1.
- The latest xqyun serial P23 regression passed 5/5 in `31.02 s`: RPC-timeout boundary `4.64 s`, provider-detail plus
  self-health restart recovery `10.19 s`, oversized chunk `4.85 s`, split batch `4.65 s`, and client sender recovery
  `6.48 s`. Every ST remains below the `30 s` per-case budget. A preceding zero-business-execution run failed during
  cluster setup because the non-interactive shell omitted the CLion tools path; adding
  `.clion-remote/tools/bin` restored the repository's expected test environment.
- `UrmaDisableFallbackTest.MigrationTargetRejectsUbFailureAndRecoversAfterRestart` closes the migration lifecycle
  signal with a real provider CQE status 4, an authenticated `MigrateDataProbe`, fail-fast
  `K_URMA_WORKER_UNAVAILABLE`, and successful admission after the target Worker restarts. The first effective RED
  failed during probe authentication in `9707 ms` GTest / `10.65 s` process time. GREEN passed in `8600 ms` /
  `8.77 s`; the final aggregate run took `8.93 s`.
- `KVClientTransportGetTest.DirectReadsSkipUnavailableSourceAndRecoverAfterRestart` exposed that routed requests used
  the heartbeat-backed `UbHealthFilter` while `ReplicaReader` consulted a separate empty admission cache. The
  effective RED returned `K_OK` instead of `K_URMA_DATA_WORKER_UNAVAILABLE` in `8569 ms` GTest / `8.87 s` process
  time. GREEN shares the incarnation/epoch-fenced filter across routing and direct-read source admission. The new
  recovery UT passed in `3 ms`; the ST passed in `14.03 s`, proving unary fail-fast, healthy batch-group progress,
  and readmission after Worker restart.
- Fresh exact-source xqyun verification passed 15/15 provider UT in `3 ms`, 28/28 client UT, and 7/7 serial ST in
  `55.05 s`: RPC timeout `5.23 s`, self-health/restart `9.25 s`, migration/restart `8.93 s`, oversized chunk `5.34 s`,
  split batch `4.23 s`, client sender recovery `7.85 s`, and direct/batch read recovery `14.03 s`. Every ST remains
  below the `30 s` per-case budget.
- Strict P23 signal status is 5/5: client sender recovery, RPC-timeout boundary, public provider detail,
  self-summary propagation/restart recovery, and migration-target rejection/recovery all pass. The direct-read ST
  additionally closes the shared local/global health-state path. All HTML UC-1 through UC-7 acceptance rows now have
  focused UT plus end-to-end evidence where the specification requires it.

## P24 Incremental Evidence

- Final xqyun Bazel 7.4.1 RED first found that the new public `TransportLayer` header referenced
  `IWorkerFilter` without a direct `client_transport` dependency on the routing target. After adding the dependency,
  compilation advanced to the URMA Mock public-header boundary.
- The second RED proved that `USE_URMA_MOCK` was a target-local `copts` value: consumers of `urma_manager.h` selected
  the mock target but still included the real SDK header. The fix uses transitive `defines` on the three public URMA
  header targets, preserving cache locality instead of adding a global `.bazelrc --copt` that invalidates every C++
  action.
- The third RED executed 24 tests and passed 23/24; `UrmaRecoveryProbeBufferTest` reported
  `URMA_MOCK provider is not linked`. The focused UT target now conditionally links the repository's existing
  `alwayslink` mock backend only under `enable_urma_mock`.
- GREEN passed 24/24 `p19_transport_admission_test` cases in `2.1 s`; its hot incremental Bazel run completed in
  `41.950 s` with 33 actions and `--jobs=8 --local_test_jobs=1`.
- Both changed manual ST targets, `kv_client_transport_get_test` and `urma_object_client_test`, compiled and linked
  under Bazel 7.4.1 with `--config=test --config=urma_mock --jobs=8`. The first complete build of that ST configuration
  took `801.804 s` and executed 441 actions without memory pressure. Runtime acceptance remains the exact CTest 7/7
  result above; the Bazel gate verifies metadata, public-header propagation, production compilation, and final links.
- PR !1422's first GitCode run exposed merge conflicts after `main/master` advanced, rather than a compile or test
  failure. The branch was rebased from `ce485a006` to `620289e01`; the conflict resolution retains the new mainline
  routing RPC ownership while sharing one `UbHealthFilter` between routed requests and direct reads.
- After rebasing to `main/master@9bc17ec95`, the CLion shell path completed the URMA Mock build, install, examples,
  and index refresh in `1165 s` with `--jobs=16`; source compilation took `1078 s`. All third-party inputs used
  `/home/cache/ds-thirdparty-cache`, and the refreshed local compile database contains 1107 entries.
- Fresh latest-master CMake evidence passed 43/43 focused UT: 15 provider-detail cases in `0.11 s`, 21 client
  transport/read cases in `0.94 s`, 3 heartbeat-summary cases in `0.08 s`, and 4 lease-sync cases in `0.08 s`.
  The exact 7/7 serial ST run completed in `55.79 s`: RPC timeout `5.77 s`, self-health/restart `12.32 s`,
  migration/restart `8.65 s`, oversized chunk `4.47 s`, split batch `3.65 s`, sender recovery `7.01 s`, and
  direct/batch read recovery `13.92 s`. Every case remains below the `30 s` budget.
- The latest topology keyspace requires sidecar observers to register the absolute ETCD prefix. The self-health ST
  first failed while reading a double-prefixed table, then passed in `9.35 s` after using
  `CreateTableWithExactPrefix`; the final 7/7 run above contains the corrected evidence.
- Latest-master commit `34c515c32` made every Bazel `ds_cc_test` unloadable by applying `select()` to the
  non-configurable `tags` attribute. Static `asan`, `tsan`, and `sanitizer` tags preserve sanitizer query selection
  and restored Bazel 7.4.1 package loading. The focused target then passed 24/24 in `2.1 s`; its configuration refresh
  completed in `372.159 s` with 1421 actions and `--jobs=16`.
- Both changed ST targets, `kv_client_transport_get_test` and `urma_object_client_test`, compiled and linked under
  Bazel 7.4.1 in `821.206 s` with 1790 actions and `--jobs=16`.
- P24 now waits only for observable GitCode success on the rebased and pushed PR head.

## Latest Verified Evidence

Command:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object && \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*'"
```

Result:

- 16 tests from 5 suites passed.
- Test process time: `0.075 total`.
- P7 new case `DataMigratorUbAdmissionTest.RedirectTargetSelectionSkipsUnavailableWorkers`: `0 ms`.
- P7 log evidence: migration skipped unavailable redirect target `<unavailable-worker-endpoint>`.

P8 command:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build-urma-mock && \
PATH=/home/worktrees/ub-fault-isolation-main/.clion-remote/tools/bin:\$PATH \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/st/ds_st_object_cache \
--gtest_filter='UrmaDisableFallbackTest.RemoteGetRecoversAfterContinuousUrmaUnavailable'"
```

P8 result:

- 1 ST from 1 suite passed.
- GTest time: `3853 ms`.
- Process time: `4.437 total`.
- The case injected `UrmaManager.UrmaWaitError` for three remote reads, observed
  `K_URMA_WAIT_TIMEOUT` while the worker URMA path was unavailable, cleared the injection, and verified the same
  remote get path recovered successfully.
- Build note: the ordinary CLion build has `BUILD_WITH_URMA_MOCK=off`, so URMA ST filters report `0 tests`.
  Use `scripts/clion_remote_build.sh urma-mock-index` or the `build-urma-mock` CMake tree for URMA Mock work.

P9 focused UT refresh:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object && \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*'"
```

- 16 focused isolation UT passed.
- GTest time: `4 ms`.
- Process time: `0.169 total`.

P9 TCP-only / fallback UT:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut \
--gtest_filter='TcpTransporterTest.GetUsesGetObjectRemoteAndPreservesPayload:TcpTransporterTest.SetCallsInvokeSet:TcpTransporterTest.MCreateAndMSetUseOneMultiPublishRpc'"
```

- 3 TCP transporter UT passed.
- GTest time: `1 ms`.
- Process time: `0.081 total`.
- Attempted ST `ObjectClientWithTCPTest.EndToEndSuccessWithNonShmSealSuccess` on xqyun ordinary build timed out before
  business assertions while waiting for `worker1/health`; residual processes were cleaned. It is not counted as passed.

P9 review-gap closure UT:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object; \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable'"
```

- 19 focused isolation UT from 6 suites passed.
- GTest time: `14 ms`.
- Process time: `0.098 total`.
- New P9 UT cases:
  - `MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable` covers ordinary and direct incoming
    migration rejecting local UB quarantine before writing data.
  - `WorkerOcServiceGetUbAdmissionTest.RemoteReadFailureMarksDataWorkerUnavailable` covers remote read UB failure
    learning into read-source admission.
  - `DataMigratorUbAdmissionTest.L2SlotRedirectSkipsUnavailableWorkers` covers L2 slot redirect retry using the
    shared quarantine-aware redirect selector.

P9 URMA Mock recovery refresh:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build-urma-mock; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target datasystem_worker_bin ds_st_object_cache; \
PATH=/home/worktrees/ub-fault-isolation-main/.clion-remote/tools/bin:\$PATH \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/st/ds_st_object_cache \
--gtest_filter='UrmaDisableFallbackTest.RemoteGetRecoversAfterContinuousUrmaUnavailable'"
```

- 1 URMA Mock ST from 1 suite passed.
- GTest time: `4117 ms`.
- Process time: `4.995 total`.
- Confirms remote-get failure learning still preserves timeout-only recovery: three injected `K_URMA_WAIT_TIMEOUT`
  reads fail while URMA is unavailable, then the same remote get succeeds after injection is cleared.

P10 migration-target recovery UT:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object; \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='DataMigratorUbAdmissionTest.*'"
```

- 6 DataMigrator UB admission UT passed.
- GTest time: `1 ms`.
- Process time: `0.096 total`.
- New P10 UT cases:
  - `DataMigratorUbAdmissionTest.RecoveryProbeSuccessRestoresQuarantinedMigrationTarget` covers an injected empty
    migration probe successfully moving a hard-quarantined target back to `AVAILABLE`.
  - `DataMigratorUbAdmissionTest.RecoveryProbeFailureKeepsMigrationTargetQuarantined` covers probe failure preserving
    quarantine and continuing to return `K_URMA_WORKER_UNAVAILABLE`.

P10 full focused isolation refresh:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object; \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable'"
```

- 21 focused isolation UT from 6 suites passed.
- GTest time: `16 ms`.
- Process time: `0.110 total`.

P11 local-worker recovery probe UT:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object; \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='WorkerOcServicePublishUbAdmissionTest.*:WorkerOcServiceMigrateUbAdmissionTest.LocalProbeSuccessRestoresIncomingMigrationAdmission'"
```

- 6 UT from 2 suites passed.
- GTest time: `7 ms`.
- Process time: `0.122 total`.
- Build note: this first green after the local recovery probe signature change rebuilt and linked `ds_ut_object`.
- New P11 UT cases:
  - `WorkerOcServicePublishUbAdmissionTest.LocalProbeSuccessRestoresPublishAdmission` covers local publish write
    admission using an injected recovery probe to move a hard-quarantined local worker back to `AVAILABLE`.
  - `WorkerOcServicePublishUbAdmissionTest.LocalProbeFailureKeepsPublishAdmissionBlocked` covers probe failure
    preserving local write quarantine and returning `K_URMA_WORKER_UNAVAILABLE`.
  - `WorkerOcServicePublishUbAdmissionTest.LocalProbeSuccessRestoresMultiPublishAdmission` covers multipublish sharing
    the same local recovery gate as publish.
  - `WorkerOcServiceMigrateUbAdmissionTest.LocalProbeSuccessRestoresIncomingMigrationAdmission` covers incoming
    migration admission recovering the local target worker before accepting migration writes.

P11 final focused isolation refresh:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object; \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable:WorkerOcServiceMigrateUbAdmissionTest.LocalProbeSuccessRestoresIncomingMigrationAdmission'"
```

- 25 focused isolation UT from 7 suites passed.
- GTest time: `10 ms`.
- Process time: `0.085 total`.
- Build note: the final verification rebuilt worker/object-cache dependents because publish, multipublish, and
  migration constructor signatures now accept the optional local recovery probe.

P12 client-put report discovery:

- Read-only code tracing found that client-side UB put failures currently do not update worker-side
  `PeerUbAdmission`.
- Evidence:
  - `UbTransporter::Set` captures `WritePayload(info)` failure, falls back to TCP payload, then invokes `Publish`; the
    original UB `writeRc` is not included in `PublishReqPb`.
  - `UbTransporter::ClassifyMSetPayload` handles per-object UB write failure by TCP fallback or local failed keys; it
    does not report a `UbOpOutcome` to the worker.
  - Worker-side `PeerUbAdmission::ReportOutcome` production callers are currently worker migration and worker remote-get
    learning paths, not client put.
  - `PublishReqPb` and `MultiPublishReqPb` do not yet carry UB outcome/failure fields.
- TDD RED evidence: adding publish/multipublish UB failure report tests first failed to compile because
  `PublishReqPb`/`MultiPublishReqPb` had no `ub_failure_report` field and `WorkerOCServiceImpl` had no
  `ApplyClientPutUbFailureReport` helper.

P12 client-put report UT:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object; \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='WorkerOcServicePublishUbAdmissionTest.*'"
```

- 7 publish admission UT from 1 suite passed.
- GTest time: `0 ms`.
- Process time: `0.069 total`.
- New P12 UT cases:
  - `WorkerOcServicePublishUbAdmissionTest.PublishUbFailureReportMarksLocalAdmissionUnavailable` covers a client
    publish UB write failure report moving local `CLIENT_PUT` admission to `UNAVAILABLE`.
  - `WorkerOcServicePublishUbAdmissionTest.MultiPublishUbFailureReportMarksLocalAdmissionUnavailable` covers the same
    learning path for multipublish.

P12 final focused isolation refresh:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object; \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable:WorkerOcServiceMigrateUbAdmissionTest.LocalProbeSuccessRestoresIncomingMigrationAdmission'"
```

- 27 focused isolation UT from 7 suites passed.
- GTest time: `23 ms`.
- Process time: `0.099 total`.

P12 URMA Mock recovery refresh:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build-urma-mock; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target datasystem_worker_bin ds_st_object_cache; \
PATH=/home/worktrees/ub-fault-isolation-main/.clion-remote/tools/bin:\$PATH \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/st/ds_st_object_cache \
--gtest_filter='UrmaDisableFallbackTest.RemoteGetRecoversAfterContinuousUrmaUnavailable'"
```

- 1 URMA Mock ST from 1 suite passed.
- GTest time: `4357 ms`.
- Process time: `5.009 total`.
- Build note: this refresh rebuilt the URMA Mock proto/client/worker/ST chain after P12 added
  `ub_failure_report` to publish and multipublish RPCs.
- Runtime evidence: the case injected three `K_URMA_WAIT_TIMEOUT` remote-read failures while URMA was unavailable,
  then cleared the injection and verified the same remote get path recovered successfully.

PR-readiness focused UT refresh:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object; \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable:WorkerOcServiceMigrateUbAdmissionTest.LocalProbeSuccessRestoresIncomingMigrationAdmission'"
```

- Rebased onto latest `main/master` at `2793d4f23` before this run.
- 27 focused isolation UT from 7 suites passed.
- GTest time: `9 ms`.
- Process time: `0.096 total`.

PR-readiness URMA Mock ST refresh:

```bash
ssh xqyun-32c32g "set -e; cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build-urma-mock; \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target datasystem_worker_bin ds_st_object_cache; \
PATH=/home/worktrees/ub-fault-isolation-main/.clion-remote/tools/bin:\$PATH \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/st/ds_st_object_cache \
--gtest_filter='UrmaDisableFallbackTest.RemoteGetRecoversAfterContinuousUrmaUnavailable'"
```

- Rebased onto latest `main/master` at `2793d4f23` before this run.
- 1 URMA Mock ST from 1 suite passed.
- GTest time: `3220 ms`.
- Process time: `3.782 total`.
- Runtime evidence: three injected `K_URMA_WAIT_TIMEOUT` remote reads failed while URMA was unavailable; after clearing
  injection, the same remote get path recovered successfully.

P13 Bazel target registration:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main; \
/home/cache/tools/bazel-7.4.1 --output_user_root=/home/cache/bazel-ds test --config=test --local_test_jobs=4 \
//tests/ut/common/object_cache:ub_failure_classifier_test \
//tests/ut/common/object_cache:peer_ub_admission_test \
//tests/ut/worker:data_migrator_ub_admission_test \
//tests/ut/worker:worker_oc_service_get_ub_admission_test \
//tests/ut/worker:worker_oc_service_publish_ub_admission_test"
```

- Added Bazel test targets for the 5 new UT files missing from BUILD metadata:
  - `//tests/ut/common/object_cache:ub_failure_classifier_test`
  - `//tests/ut/common/object_cache:peer_ub_admission_test`
  - `//tests/ut/worker:data_migrator_ub_admission_test`
  - `//tests/ut/worker:worker_oc_service_get_ub_admission_test`
  - `//tests/ut/worker:worker_oc_service_publish_ub_admission_test`
- xqyun Bazel binary:
  - Path: `/home/cache/tools/bazel-7.4.1`
  - Version: `bazel 7.4.1`
  - SHA256: `c97f02133adce63f0c28678ac1f21d65fa8255c80429b588aeeba8a1fac6202b`
- Final remote execution result:
  - `//tests/ut/common/object_cache:peer_ub_admission_test`: passed in `2.6s`
  - `//tests/ut/common/object_cache:ub_failure_classifier_test`: passed in `2.9s`
  - `//tests/ut/worker:data_migrator_ub_admission_test`: passed in `13.7s`
  - `//tests/ut/worker:worker_oc_service_get_ub_admission_test`: passed in `13.0s`
  - `//tests/ut/worker:worker_oc_service_publish_ub_admission_test`: passed in `13.1s`
  - Bazel elapsed time: `423.279s`
  - Executed `5 out of 5 tests: 5 tests pass.`
- Bazel metadata fixes made during execution:
  - `tests/st/cluster:st_cluster` now declares direct deps on `brpc_factory`, `rpc_stub_cache_mgr`, and
    `generic_service_brpc`, matching headers included by `base_cluster.h`.
  - `src/datasystem/protos:ut_object_brpc` models the `ut_object.brpc/irpc` generated headers used under
    `WITH_TESTS`.
  - `worker_oc_server` and `tests/st:st_common` now declare `ut_object_brpc` in their test-only deps.

P14 HTML coverage closure UT:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object && \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='PeerUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*'"
```

- 12 focused UT from 2 suites passed.
- GTest time: `2 ms`.
- Process time: `0.079 total`.
- First rebuild note: this run rebuilt `common_buffer`, worker object-cache dependents, and `ds_ut_object` after
  adding public health-summary state, so wall-clock build time was dominated by relink rather than gtest runtime.
- New P14 UT cases:
  - `PeerUbAdmissionTest.HealthSummaryCarriesOnlyObservedPeerState` covers the O(N) summary model for locally observed
    UB path state without carrying a peer matrix.
  - `PeerUbAdmissionTest.ProbeFailureIncreasesBackoffLevelUntilSuccess` covers probe failure backoff visibility and
    reset after a successful recovery probe.
  - `WorkerOcServiceGetUbAdmissionTest.RemoteReadRpcTimeoutDoesNotHardQuarantineDataWorker` covers the HTML boundary
    that ordinary RPC timeout must not be synthesized into a hard UB ERROR 4 quarantine.

P15 final xqyun focused CMake refresh:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object && \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable:WorkerOcServiceMigrateUbAdmissionTest.LocalProbeSuccessRestoresIncomingMigrationAdmission'"
```

- 30 focused isolation UT from 7 suites passed.
- GTest time: `14 ms`.
- Process time: `0.317 total`.
- This refresh ran after syncing the formatted C++ changes, documentation, and Bazel metadata fixes to xqyun.

P16 GitCode review closure:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object && \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='PeerUbAdmissionTest.*'"
```

- 10 `PeerUbAdmissionTest` UT passed.
- GTest time: `0 ms`.
- Process time: `0.069 total`.
- New P16 UT case:
  - `PeerUbAdmissionTest.ProbeStartDoesNotOverwriteAlreadyRecoveredPeer` covers the review-reported race where a stale
    recovery probe start must not overwrite a peer already restored to `AVAILABLE`.

P16 final focused CMake refresh:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut_object && \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut_object \
--gtest_filter='UbFailureClassifierTest.*:PeerUbAdmissionTest.*:DataMigratorUbAdmissionTest.*:WorkerOcServiceGetUbAdmissionTest.*:WorkerOcServicePublishUbAdmissionTest.*:MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable:WorkerOcServiceMigrateUbAdmissionTest.LocalProbeSuccessRestoresIncomingMigrationAdmission'"
```

- 31 focused isolation UT from 7 suites passed.
- GTest time: `11 ms`.
- Process time: `0.100 total`.

P16 Bazel 7.4.1 refresh:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main && \
/home/cache/tools/bazel-7.4.1 --output_user_root=/home/cache/bazel-ds test --config=test --local_test_jobs=4 \
//tests/ut/common/object_cache:ub_failure_classifier_test \
//tests/ut/common/object_cache:peer_ub_admission_test \
//tests/ut/worker:data_migrator_ub_admission_test \
//tests/ut/worker:worker_oc_service_get_ub_admission_test \
//tests/ut/worker:worker_oc_service_publish_ub_admission_test"
```

- Bazel binary: `/home/cache/tools/bazel-7.4.1`.
- Bazel cache: `/home/cache/bazel-ds`.
- Executed `5 out of 5 tests: 5 tests pass.`
- Bazel elapsed time: `21.570s`.
- Per-target times:
  - `//tests/ut/common/object_cache:peer_ub_admission_test`: passed in `1.2s`
  - `//tests/ut/common/object_cache:ub_failure_classifier_test`: passed in `1.1s`
  - `//tests/ut/worker:data_migrator_ub_admission_test`: passed in `15.1s`
  - `//tests/ut/worker:worker_oc_service_get_ub_admission_test`: passed in `14.2s`
  - `//tests/ut/worker:worker_oc_service_publish_ub_admission_test`: passed in `14.8s`

P17 CLion rewrite script check:

```bash
tmpdir=$(mktemp -d /tmp/ub-clion-rewrite.XXXXXX) && mkdir -p "$tmpdir/custom-build" "$tmpdir/local-cache" && \
printf '[{"directory":"/repo","command":"c++ -I/home/remote-cache/include a.cpp"}]\n' \
  > "$tmpdir/custom-build/compile_commands.json.remote" && \
(cd "$tmpdir" && BUILD_DIR=custom-build REMOTE_THIRDPARTY=/home/remote-cache LOCAL_THIRDPARTY=local-cache \
  python3 /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ub-fault-isolation-main/scripts/rewrite_clion_compile_commands.py)
```

- Result: `compile_commands entries: 1`.
- Rewritten command: `c++ -I/tmp/ub-clion-rewrite.7STSvl/local-cache/include a.cpp`.
- This closes the `urma-mock-index`/custom build-dir review gap: the rewrite script now follows `BUILD_DIR` instead of
  hard-coding `.clion-remote/build`.

P17 CMake client transport UT:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache cmake --build . -j32 --target ds_ut && \
TIMEFORMAT='elapsed_seconds=%R'; time ./tests/ut/ds_ut \
--gtest_filter='UbTransporterMSetFailureReportTest.*:UbTransporterTest.MSetReportsHardUbFailureOverEarlierTimeout'"
```

- 2 UT from 2 suites passed.
- GTest time: `2 ms`.
- Process time: `0.107 total`.
- New P17 UT case:
  - `UbTransporterMSetFailureReportTest.ReportsHardUbFailureOverEarlierTimeout` covers MSet preserving the hard
    `K_URMA_ERROR` UB failure report even when an earlier object only saw `K_URMA_WAIT_TIMEOUT`.
- Existing `UbTransporterTest.MSetReportsHardUbFailureOverEarlierTimeout` in the broader client transport suite passed
  in the same CMake run.

P17 Bazel 7.4.1 focused target:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main && \
/home/cache/tools/bazel-7.4.1 --output_user_root=/home/cache/bazel-ds test --config=test --local_test_jobs=4 \
//tests/ut/client:ub_transporter_mset_failure_report_test"
```

- `//tests/ut/client:ub_transporter_mset_failure_report_test` passed in `1.3s`.
- Bazel elapsed time: `27.744s`.

P17 Bazel 7.4.1 final 6-target refresh:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main && \
/home/cache/tools/bazel-7.4.1 --output_user_root=/home/cache/bazel-ds test --config=test --local_test_jobs=4 \
//tests/ut/common/object_cache:ub_failure_classifier_test \
//tests/ut/common/object_cache:peer_ub_admission_test \
//tests/ut/worker:data_migrator_ub_admission_test \
//tests/ut/worker:worker_oc_service_get_ub_admission_test \
//tests/ut/worker:worker_oc_service_publish_ub_admission_test \
//tests/ut/client:ub_transporter_mset_failure_report_test"
```

- Executed `3 out of 6 tests: 6 tests pass`; the other 3 were cached.
- Bazel elapsed time: `21.574s`.
- Per-target times:
  - `//tests/ut/client:ub_transporter_mset_failure_report_test`: cached pass in `1.3s`
  - `//tests/ut/common/object_cache:peer_ub_admission_test`: cached pass in `1.2s`
  - `//tests/ut/common/object_cache:ub_failure_classifier_test`: cached pass in `1.1s`
  - `//tests/ut/worker:data_migrator_ub_admission_test`: passed in `15.0s`
  - `//tests/ut/worker:worker_oc_service_get_ub_admission_test`: passed in `12.2s`
  - `//tests/ut/worker:worker_oc_service_publish_ub_admission_test`: passed in `12.2s`

P18 latest-main CLion/CMake baseline:

```bash
REMOTE_HOST=xqyun-32c32g \
REMOTE_DIR=/home/worktrees/ub-fault-isolation-main \
REMOTE_THIRDPARTY=/home/cache/ds-thirdparty-cache \
JOBS=32 scripts/clion_remote_build.sh tests-index
```

- Rebased onto `main/master` commit `ce485a006`.
- All third-party dependencies were found in `/home/cache/ds-thirdparty-cache`; third-party build time was `0s`.
- CLion/CMake build and index completed in `1493s` and produced `1048` compile-command entries.
- Object-side focused baseline: `31/31` UT passed in `0.119s` process time.
- Client transport baseline: `2/2` UT passed in `0.114s` process time.
- URMA Mock exposed one reproducible build regression: DataSystem's non-chainable `CHECK_EQ` macro was active while a
  later `brpc/channel.h` template used `CHECK_EQ(...) << ...`. Preloading the complete brpc header before DataSystem
  headers in `urma_manager.cpp` confines the fix to that translation unit. The `common_rdma` target then passed in
  `26.76s`.
- The rebuilt `UrmaDisableFallbackTest.RemoteGetRecoversAfterContinuousUrmaUnavailable` ST passed `1/1`: gtest
  `4.606s`, process `5.190s`. It observed three injected `K_URMA_WAIT_TIMEOUT` failures and successful recovery after
  clearing the injection.

P19 client-local sender and direct-read admission:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
TIMEFMT='PROCESS_ELAPSED=%E'; time ./tests/ut/ds_ut \
--gtest_filter='ReplicaReaderAdmissionTest.*:TransportLayerAdmissionTest.*'"
```

- 14 new client UT from 2 suites passed: 8 client-local sender admission cases and 6 unary/batch direct-read cases.
- Latest GTest time: `56 ms`; the longest case is the bounded concurrent admission case at `50 ms`.
- A hard UB sender failure blocks later Set/MSet and remote allocation in the same `TransportLayer`; another client
  remains independent, and TCP failures do not trip the UB sender gate.
- Unary read rejects an unavailable source before endpoint execution and advances to a healthy replica when present.
  Batch read checks one worker endpoint once per scheduling wave, advances each item to its next replica, and continues
  healthy endpoint groups.
- Independent review added four TDD cases for the sender admission TOCTOU boundary, unary and batch alternate-replica
  continuation, and UB-transporter business errors without dedicated local-write evidence. All four failed before the
  production fix and pass afterward.
- Two cleanup TDD cases verify a retry rejected by newly published sender quarantine still releases the Set allocation
  or every MSet allocation; both execute in `0 ms` after failing against the early-return implementation.

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
TIMEFMT='PROCESS_ELAPSED=%E'; time ./tests/ut/ds_ut_object \
--gtest_filter='WorkerOcServicePublishUbAdmissionTest.PublishUbFailureReportDoesNotQuarantineWorkerSelf:WorkerOcServicePublishUbAdmissionTest.MultiPublishUbFailureReportDoesNotQuarantineWorkerSelf'"
```

- 2 new worker UT passed; GTest time `0 ms`, process time `0.07s`.
- Client-local Publish/MultiPublish UB reports remain wire compatible but no longer quarantine worker-self admission.

P19 Bazel 7.4.1 target:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main && \
/home/cache/tools/bazel-7.4.1 --output_user_root=/home/cache/bazel-ds test \
//tests/ut/client:p19_transport_admission_test --nocache_test_results --test_output=errors \
--jobs=32 --local_test_jobs=1"
```

- The final focused target passed in `1.0s` test time and `21.579s` Bazel elapsed time with test cache disabled after
  the review and cleanup fixes; the earlier public-header validation was `0.9s` test / `25.961s` elapsed.
- The first focused build after splitting the test source used 8 actions and completed in `23.448s`; the old whole-file
  experiment passed but required `996.310s` cold build and `12.6s` test time, so it was replaced rather than retained.
- Final client transport/read regression: `47/47`, GTest `72 ms`.
- Final peer/worker/fallback admission regression: `24/24`, GTest `2 ms`.
- CLion `tests-index` refresh used `/home/cache/ds-thirdparty-cache`: all 19 third-party dependencies hit cache in
  `0s`, total refresh `262s`, and `compile_commands.json` now contains `1049` entries.

P20 structured provider failure protocol and consumer:

```bash
ssh xqyun-32c32g "cd /home/worktrees/ub-fault-isolation-main/.clion-remote/build && \
cmake --build . -j32 --target ds_ut_object && \
tests/ut/ds_ut_object --gtest_filter='WorkerOcServiceGetUbAdmissionTest.*'"
```

- 7 new P20 UT plus 3 existing admission boundary UT passed: `10/10`, GTest `2 ms`.
- Schema RED failed in `13.75s` because neither response exposed `provider_ub_failure_detail`.
- The first schema GREEN found that the repo ZMQ generator rejects proto3 `optional` in `1.99s`; explicit `has_*`
  presence bits preserve the same wire semantics and then passed both response schema cases at `0 ms` each.
- Shared codec/consumer RED failed in `0.22s` because the helper did not exist. GREEN covers full detail encoding,
  legacy/untrusted rejection, Worker unary learning, per-subresponse batch learning, and client-writeback decoding.
- Two provider integration RED cases failed in `20.53s` because the real recording entry points did not exist. Both
  GREEN cases execute in `0 ms`: Worker-to-Client fills detail before TCP fallback in `GetRequest::UbWriteHelper`, and
  Worker-to-Worker fills detail immediately after the URMA write failure in `WriteViaFastTransport`.
- Client Get decodes only explicit provider detail and records hard classifier outcomes as UB data-plane failures;
  timeout detail remains suspect and legacy responses are not upgraded by the new path.
- Raw status extraction is now typed rather than parsed from `Status` text: post failures preserve the provider return
  code and blocking completion failures preserve the CQE status in `UrmaWriteFailure`. Worker-to-Client and
  Worker-to-Worker recording helpers propagate both optional fields into `ProviderUbFailureDetailPb`.
- Raw-CQE TDD RED compiled the URMA Mock target in `9.367s` and failed because `UrmaWriteFailure` and the typed
  `WaitToFinish` output did not exist. A real-URMA validation attempt was blocked after `366.442s` by the unavailable
  SDK header `ub/umdk/urma/urma_api.h`; it is recorded as environment evidence, not a behavioral result.
- The first `--config=urma_mock` attempt took `672.893s` and exposed that this standalone target did not define
  `USE_URMA`; target-specific mock copts now make the URMA code path executable. The first full GREEN attempt then
  exposed an invalid null-lane fixture after `131.473s` (`10.0s` test stage). Replacing only the fixture with a direct
  event-table entry preserved the production `WaitToFinish` path.
- Final URMA Mock target result: `1/1` Bazel target passed, test time `9.5s`, elapsed `23.571s`, four hot-cache actions.
  The new `WaitToFinishReturnsRawCqeStatus` case passed alone in `1ms` gtest time. Four hardware-dependent cases remain
  skipped without `DS_URMA_DEV_NAME`, as expected for this host.
- Final P19-P20 regression: client admission `14/14` in `59ms`; peer/worker/fallback admission `40/40` in `11ms`;
  combined `54/54` passed with `1.267s` remote wall time.
- CLion/CMake verification used `/home/cache/ds-thirdparty-cache`. A direct configure without `DS_OPENSOURCE_DIR`
  briefly selected `/tmp` and was stopped before rebuilding dependencies; the build tree was reconfigured with the
  cache and the final `ds_ut_object` target completed successfully.

P21 GlobalFact role matrix and first business gate:

- Role-matrix TDD RED failed because `DataPlaneAdmissionRole` and `CheckTopologyDataPlaneAdmission` did not exist;
  the first compile error appeared in `2.70s`, and the parallel build exited after `65.50s`.
- The new role-matrix UT covers six topology member states across six data-plane roles (36 decisions): ordinary local
  write, incoming migration, outgoing migration, and redirect require `ACTIVE`; a validated topology migration source
  may also be `PRE_LEAVING` or `LEAVING`; a validated topology migration target may also be `JOINING`.
- `ObjectEndpointPolicyTest.DataPlaneAdmissionEnforcesRoleSpecificGlobalFact` passed `1/1` in `0ms` gtest time
  (`0.87s` remote wall time).
- Publish integration RED returned `K_OK` for a locally `FAILED` topology member even when UB admission was available.
  The minimal GREEN checks `LOCAL_WRITE` GlobalFact before UB recovery, so a denied member neither probes nor writes.
- MultiPublish RED also returned `K_OK` for local `FAILED`; GREEN applies the same `LOCAL_WRITE` GlobalFact before UB
  recovery. Incoming migration RED returned `K_OK` in `3ms`; GREEN checks `INCOMING_MIGRATION` before incrementing the
  admission count whenever a membership Snapshot is configured.
- Combined write/incoming suite passed `11/11` in `4ms`: Publish `8/8` after its first slice, then Publish/MultiPublish
  plus incoming migration `11/11`. Outgoing migration, redirect selection, and post-probe topology revalidation remain
  in the next P21 slices.
- Guarded migration recovery RED proved that a successful UB probe could overwrite a target that changed from
  `ACTIVE` to `FAILED`: the method returned `K_OK` and committed `AVAILABLE`. GREEN checks target GlobalFact both before
  and after the probe; a denied post-check is recorded as probe failure and leaves the target `UNAVAILABLE`.
- `DataMigratorUbAdmissionTest.RecoveryProbeSuccessDoesNotOverrideGlobalFactDeny` passed in `0ms`; the combined P21
  Worker suites passed `18/18` in `10ms`. Redirect GlobalFact filtering and local Publish/MultiPublish post-probe
  revalidation remain.

## HTML Design Coverage

Source spec: `https://yche.me/design/ds-worker-isolation-ub-tcp-boundary-20260716.html`.

### Use Case Coverage

| HTML Use Case | Current coverage | Evidence | Status |
| --- | --- | --- | --- |
| UC-1 Client writes Worker | A hard UB sender failure is owned by one `TransportLayer`; admission is linearized across concurrent operations, later UB Set/MSet and allocation fail fast, another client remains independent, and worker-self admission is not poisoned. A dedicated background connection probe observes cooldown/backoff and WorkerSnapshot pre/post gates before reopening sender admission. | `TransportLayerAdmissionTest.*`, `WorkerOcServicePublishUbAdmissionTest.*DoesNotQuarantineWorkerSelf`, `UrmaClientSenderRecoveryTest.ClientSenderProbeWaitsForUrmaDataPlaneRecovery` | P23 covered through guarded WRITE-probe recovery |
| UC-2 Client Get written back by Worker | Worker-to-Client UB write failure fills structured status, raw provider/CQE status, failed endpoint, failure side, and operator worker before TCP fallback; Client Get consumes explicit hard detail while timeout remains suspect. | `WorkerOcServiceGetUbAdmissionTest.*Client*`, `GetRequestRecordsWorkerToClientProviderFailure`, `UrmaSendJettyFaultTest.WaitToFinishReturnsRawCqeStatus`, `UrmaDisableFallbackTest.SelfHealthLeasePropagatesFailureAndRestartRecovery` | Public structured failure and independent timeout boundary accepted in P23 |
| UC-3 Client Direct Read / Batch Direct Read | Unary and batch direct read check the same heartbeat-backed source filter used by routing; unavailable sources are skipped without transport execution, unrelated healthy batch groups continue, and a restarted incarnation is readmitted. | `ReplicaReaderAdmissionTest.*`, `WorkerServiceUbHealthTest.*`, `UbHealthLeaseSyncTest.*`, `KVClientTransportGetTest.DirectReadsSkipUnavailableSourceAndRecoverAfterRestart` | P23 covered end to end |
| UC-4 Worker RemoteGet | Provider fills structured detail before fallback; unary and batch requester paths share one decoder and consume per-response detail, while generic timeout remains non-hard. Async batch CQE4 now updates provider self-health, reaches the public Get as `K_URMA_ERROR`, and subsequent reads fail fast as unavailable without another operator call. | `WorkerOcServiceGetUbAdmissionTest.ExplicitRemoteGetDetailMarksProviderUnavailable`, `BatchRemoteGetUsesPerResponseFailureDetail`, `RemoteGetProviderRecordsWorkerToWorkerFailure`, `UrmaSendJettyFaultTest.WaitToFinishReturnsRawCqeStatus`, `UrmaDisableFallbackTest.SelfHealthLeasePropagatesFailureAndRestartRecovery` | P23 covered end to end, including restart recovery |
| UC-5 Migrate / Move / Rebalance | Migration target admission rejects quarantined targets before remote API/data send; failed migration results are learned; redirect skips unavailable targets; a real migration probe stays blocked while the target is faulted and succeeds after restart. | `DataMigratorUbAdmissionTest.BlocksMigrationTargetBeforeCreatingRemoteApi`, `FailedMigrationResultMarksTargetUnavailable`, `RedirectTargetSelectionSkipsUnavailableWorkers`, `L2SlotRedirectSkipsUnavailableWorkers`, `MigrateDataServiceTest.IncomingMigrationRejectsWhenLocalUbUnavailable`, `UrmaDisableFallbackTest.MigrationTargetRejectsUbFailureAndRecoversAfterRestart` | P23 covered end to end |
| UC-6 Scale / drain / recovery | Role-specific membership GlobalFact is ANDed with local/remote UB admission. Guarded recovery performs pre/post topology fencing for publish, migration, redirect, and incoming migration paths; restarted incarnations recover without stale-state overwrite. | P21 role matrix and guarded-recovery admission suites, `UrmaDisableFallbackTest.MigrationTargetRejectsUbFailureAndRecoversAfterRestart`, `KVClientTransportGetTest.DirectReadsSkipUnavailableSourceAndRecoverAfterRestart` | P23 covered through migration and restarted-incarnation recovery |
| UC-7 Destination Worker self UB fault | Heartbeat and lease propagation both carry one self-only summary; candidate caches enforce epoch/incarnation fencing and lease expiry removes global quarantine without clearing local evidence. | `WorkerServiceUbHealthTest.*`, `RoutingTest.UbHealthSummaryFiltersCandidateUntilFencedRecovery`, `UbHealthLeaseSyncTest.*`, `PeerUbAdmissionTest.LeaseSnapshot*`, `UrmaDisableFallbackTest.SelfHealthLeasePropagatesFailureAndRestartRecovery` | P23 covered through non-writable lease and restarted incarnation recovery |

### Branch Self-Verification Matrix

| HTML branch item | Current coverage | Status |
| --- | --- | --- |
| `CheckWriteTarget` hits local `UNAVAILABLE` | Publish/multipublish/migration target UT assert fail-fast before write/migration | Covered |
| `CheckReadSource` hits unavailable data worker | Read-source admission UT returns `K_URMA_DATA_WORKER_UNAVAILABLE` | Covered |
| Client-side URMA operator returns ERROR 4 | Client-local sender admission blocks the same client without changing worker-self admission | Covered by P19 UT and P23 recovery ST |
| Client-side URMA operator returns ERROR 9 | Classifier/admission tests keep timeout as `SUSPECT`; URMA Mock ST injects repeated wait timeout and then recovery | Covered for suspect/recovery behavior |
| Provider URMA write failure returns structured RPC status | Explicit detail carries status, optional raw provider/CQE status, `failed_endpoint`, `failure_side`, and `operator_worker`; consumers reject legacy or untrusted detail | Covered through public async batch acceptance in P23 |
| RPC timeout/failure without explicit URMA status | Timeout remains suspect and does not hard quarantine in classifier/admission or worker remote-read learning | Covered by UT semantics |
| Fallback default closed | Client put failure is reported instead of being hidden; TCP-only transporter UT guards non-URMA path | Partial; full fallback-off ST pending |
| Fallback enabled and object <= 1MB | Existing `UrmaFallbackTcpLimiter` UT covers small fallback limiter behavior, but fallback success is not connected to UB health clearing | Partial |
| Fallback enabled but object >1MB or migration | Migration path has no TCP fallback and rejects unavailable targets | Covered for migration |
| `CheckMigrationTarget` rejects stale/unavailable target | `BlocksMigrationTargetBeforeCreatingRemoteApi` and redirect UT | Covered |
| Worker `LocalUbHealth` heartbeat summary | Heartbeat and lease sidecar publish one self-only fenced summary per Worker | Covered by P22 UT and P23 ST |
| Probe success recovery | Peer, migration target, local publish/multipublish, and incoming migration recovery probe UT | Covered |
| Probe failure exponential/backoff | Failed probes keep admission unavailable, advance observable backoff, and scheduled recovery retries only after cooldown | Covered by P14/P19 UT and P23 ST |
| Global Fact rejects writes even if UB available | Role matrix covers all member states; publish, migration, redirect, and guarded recovery retain membership deny | Covered by P21 UT |
| Thousand-node summary O(N), no peer matrix | Heartbeat and lease snapshots store exactly one self-only record per Worker; 1000 Workers produce 1000 records | Covered by P22 UT |

### Acceptance Case Coverage

| HTML acceptance case | Current evidence | Status |
| --- | --- | --- |
| UC-1 client write ERROR 4 then repeated write fail-fast | Isolation, zero-business-retry probe, snapshot-deny, backoff, and readmission recovery cases in `TransportLayerAdmissionTest` plus the WRITE-probe ST | Covered by UT + P23 ST |
| UC-2 provider writeback ERROR 4 and separate RPC timeout | Public structured endpoint/operator/raw-status propagation and a separate timeout-that-recovers case | Covered by UT + P23 ST |
| UC-3 batch direct read grouped fail-fast | ReplicaReader admission UT plus a heartbeat-backed ST that blocks the unavailable source, preserves a healthy batch group, and recovers after restart | Covered by UT + P23 ST |
| UC-4 worker RemoteGet provider ERROR 4 | Worker get admission UT plus URMA Mock first-error, fail-fast, no-repeat-operator, and restart-recovery assertions | Covered by UT + one strengthened ST |
| UC-5 migrate/rebalance target unavailable | Migration target, failure learning, redirect, and incoming migration UT plus real CQE4 target rejection/restart recovery | Covered by UT + P23 ST |
| UC-6 new/exiting/recovered worker probe gating | Role matrix and pre/post recovery fencing cover membership transitions; migration and direct-read ST cover restarted-incarnation recovery | Covered by UT + P23 ST |
| UC-7 worker self UB fault heartbeat summary | Self-only heartbeat and lease propagation, O(N) storage, TTL removal, fencing, non-writable publication, and restarted incarnation recovery | Covered by P22 UT + P23 ST |

## Remaining PR Readiness Risks

- P10 closes production recovery for outbound migration targets: a blocked target can now run an empty
  `MigrateDataProbe`; success marks the target available before data migration is resumed, and failure preserves
  `K_URMA_WORKER_UNAVAILABLE`.
- P11 closes the local-worker hard-quarantine recovery adapter for publish, multipublish, and incoming migration gates:
  an injected local probe can restore the local peer to `AVAILABLE` before writes/migration resume, while absent or
  failed probes preserve the conservative `K_URMA_WORKER_UNAVAILABLE` behavior.
- Production default constructors still pass no local probe, so the default path remains conservative until a real
  same-process UB probe source is wired by the caller. This keeps fail-fast isolation safe while leaving an explicit
  hook for recoverability.
- P19 corrects P12 ownership: client put/multiput UB failure remains observable on the wire, but worker publish no
  longer applies it to worker-self admission. P23 now gives the originating `TransportLayer` a dedicated guarded
  recovery scheduler; URMA Mock end-to-end acceptance remains.
- P14 closes two HTML coverage gaps at the admission boundary: the worker can now materialize an O(N) UB health summary
  from local observations without a peer matrix, and repeated probe failures expose increasing backoff level until a
  successful probe restores availability. The heartbeat propagation and wall-clock scheduler remain explicit follow-up
  integration work.
- P16 closes the GitCode review race: `MarkProbeStart` now no-ops when the peer is already `AVAILABLE`, so a stale
  probe-start callback cannot regress a recovered local or remote UB path back to `PROBING`.
- P17 closes two additional GitCode review items: CLion compile database rewriting now respects custom `BUILD_DIR`
  values used by URMA Mock index profiles, and client MSet UB failure reporting now prefers hard path/connect failures
  over earlier timeout-suspect outcomes before sending the worker `ub_failure_report`.

## Commit Chain

```text
8b98b4c48 build(ub): generate clion cmake index with cached thirdparty
4289882cb feat(ub): add peer admission for fail-fast isolation
60b72fc96 feat(ub): block migration to quarantined workers
8272731c8 feat(ub): share admission with worker migrations
755c1211d feat(ub): learn migration failures into admission
e04d31246 feat(ub): fail fast reads from quarantined data workers
c460e90a4 feat(ub): block writes on quarantined local workers
13f404dc4 feat(ub): skip quarantined migration redirect targets
36543ce07 docs(ub): track isolation implementation progress
2fbb69657 test(ub): cover urma mock recovery path
50fc1aefe docs(ub): record p9 validation progress
806a0b5a5 docs(ub): update validation commit chain
712769de6 feat(ub): close migration and read admission gaps
e35cba85e docs(ub): record p9 gap closure validation
b6bf05dd7 docs(ub): track hard quarantine recovery gap
27d35ebc2 feat(ub): probe quarantined migration targets
ed6cfa6fc feat(ub): probe local write quarantine gates
09c81b066 docs(ub): record client put report gap
e1aba371b feat(ub): report client put ub failures
0f26c30f1 docs(ub): refresh urma mock validation
a0671fc60 docs(ub): record pr readiness validation
7d7d7d0c5 test(ub): close html coverage and bazel validation gaps
0a4d3c629 fix(ub): preserve recovered admission during probe race
ba6e4e1be fix(ub): restore latest-master validation gates
293aeacbb fix(ub): close downstream build gaps
pending fix(ub): keep ub health sidecar outside topology root
```
