# PR1405 unresolved P1 exact-head triage

- Review time: 2026-07-30
- File-name note: the file name keeps the user-requested `2026-07-31` prefix, but all conclusions below are for the exact-head state reviewed on 2026-07-30.
- Worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730`
- Exact HEAD: `5104aca078b2820b586bac51a7fa05f0e843e53a`
- Base used for `main/master...HEAD`: `refs/remotes/main/master` = `7507f139fdb1222b29eb77542223a68dea6cd19f`
- Uncommitted worktree content intentionally ignored: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`, `src/datasystem/worker/object_cache/worker_oc_service_impl.h`, `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp`
- Non-goals: no source edit, no push, no PR mutation, no retest

## Method and evidence boundary

1. CodeGraph discovery first, exact source second.
   - Commands used:
     - `/home/t14s/.local/bin/codegraph query "WorkerOCServer OR workerIsolationCoordinator_ OR ObjectMetadataCoordinationReader OR CentralMetadataAddressResolver OR HandleMembershipRecovery OR RequestObjectCacheRecoveryEvidenceIfNeeded"`
     - `/home/t14s/.local/bin/codegraph query "WorkerOCServer"`
     - `/home/t14s/.local/bin/codegraph query "NotifyRemoteGet"`
     - `/home/t14s/.local/bin/codegraph query "ObjectTable"`
     - `/home/t14s/.local/bin/codegraph query "WorkerRuntimeFacade"`
     - `/home/t14s/.local/bin/codegraph query "ObjectCacheRecoveryState"`
     - `/home/t14s/.local/bin/codegraph query "HandleMembershipRecovery"`
   - Discovery result:
     - `NotifyRemoteGet` and `WorkerOCServer` had useful hits.
     - some queries returned no hit.
     - the shared `.codegraph` also surfaced historical `.worktrees` paths, so it was treated as discovery-only and every conclusion below was re-checked against `git show HEAD:` / exact `rg` / BUILD / CMake.
2. Exact-head source boundary:
   - `git show HEAD:<path>`
   - `git diff refs/remotes/main/master...HEAD`
   - `rg --line-number` on exact-head worktree files
   - BUILD/CMake ownership from `src/.../BUILD.bazel`, `src/.../CMakeLists.txt`, `tests/ut/worker/BUILD.bazel`, `tests/ut/CMakeLists.txt`

## Executive summary

- Exact-head classification:
  - `OPEN`: 12
  - `ADDRESSED`: 1
  - `OBSOLETE`: 0
- Highest-priority fix order:
  1. coordinator self-healing null dereference
  2. DS metadata table bootstrap gap
  3. PushMeta batch error swallowing plus deferred-retry incident lifecycle plus recovered ACK persistence
  4. membership-recovery address loss plus stale callback holding `in-flight` plus local-recovery no-retry
  5. QueryAndGet admission miss
  6. NotifyRemoteGet silent-skip keys plus ClearObjects null `SafeObject`
  7. rolling-upgrade `event_type`
  8. clear-data budget: no new fix needed on exact HEAD

## Per-comment triage

### 1) coordinator self-healing coordinator null

- Status: `OPEN`
- Exact evidence:
  - DS backend path only builds `coordinatorServiceProxy_` and `metadataCoordinationBackend_`; it does not construct `workerIsolationCoordinator_`: `src/datasystem/worker/worker_oc_server.cpp:971-982`
  - ETCD path does construct `workerIsolationCoordinator_`: `src/datasystem/worker/worker_oc_server.cpp:1007-1028`
  - topology builder unconditionally dereferences `workerIsolationCoordinator_` in local-isolation and local-recovery handlers: `src/datasystem/worker/worker_oc_server.cpp:1136-1152`
- Existing coverage boundary:
  - `tests/ut/cluster/topology_engine_test.cpp:1126-1158` only proves the builder can register callbacks; it does not prove DS-backend composition in `WorkerOCServer`.
- Minimal TDD fix boundary:
  - `src/datasystem/worker/worker_oc_server.cpp`
  - add one exact-head UT that constructs the DS coordination path and proves local-isolation/local-recovery handlers do not dereference null
- Suggested priority: `1`

### 2) DS metadata table not registered

- Status: `OPEN`
- Exact evidence:
  - metadata reader hard-codes logical table `ETCD_META_TABLE_PREFIX + ETCD_HASH_SUFFIX`: `src/datasystem/worker/object_cache/object_metadata_coordination_reader.cpp:34-37`
  - query path directly does backend `Get(table, key, ...)`: `src/datasystem/worker/object_cache/object_metadata_coordination_reader.cpp:50-58`
  - ETCD path only ensures the central-master-address table: `src/datasystem/worker/worker_oc_server.cpp:1032-1034` plus `src/datasystem/worker/object_cache/central_metadata_address_resolver.cpp:31-45`
  - DS path does neither central metadata table bootstrap nor metadata logical-table bootstrap: `src/datasystem/worker/worker_oc_server.cpp:971-982`
- Existing coverage boundary:
  - `tests/ut/worker/object_cache/migrate_data_service_test.cpp:1322,1351` proves the reader uses the logical key and rejects unavailable backend, but not that the DS bootstrap path registers the table before use.
- Minimal TDD fix boundary:
  - keep the change inside DS coordination bootstrap plus one exact-head UT for bootstrap availability
  - avoid broader metadata-reader redesign
- Suggested priority: `2`

### 3) PushMeta batch errors swallowed

- Status: `OPEN`
- Exact evidence:
  - master loops over every pushed meta, logs per-object failure, and still returns `Status::OK()` unless `GIncreaseRef` fails: `src/datasystem/master/object_cache/oc_metadata_manager.cpp:4150-4167`
  - response proto is empty, so worker has no per-object signal: `src/datasystem/protos/master_object.proto:452-453`
  - worker sender treats RPC-level OK as whole-batch recovered and increments recovered count: `src/datasystem/worker/object_cache/metadata_recovery_manager.cpp:551-560`
- Minimal TDD fix boundary:
  - `oc_metadata_manager.cpp`
  - `metadata_recovery_manager.cpp`
  - `master_object.proto`
  - add master-side UT for partial batch failure and worker-side UT for failed-id propagation
- Suggested priority: `3`

### 4) deferred retry incident completed early

- Status: `OPEN`
- Exact evidence:
  - deferred retry tasks live only in in-memory queue and drainer state: `src/datasystem/worker/object_cache/slot_recovery/slot_recovery_manager.cpp:1278-1319`
  - drainer pops the queue item immediately and later logs `deferred_meta_retry_finish`: `src/datasystem/worker/object_cache/slot_recovery/slot_recovery_manager.cpp:1322-1343`
  - retry success clears `failedIds` in memory only: `src/datasystem/worker/object_cache/slot_recovery/slot_recovery_manager.cpp:1358-1363`
  - current UTs prove retry draining semantics, not incident non-terminal persistence until deferred retry is resolved: `tests/ut/worker/object_cache/slot_recovery_manager_test.cpp:1330-1440`
- Minimal TDD fix boundary:
  - `slot_recovery_manager.cpp` plus store/incident completion boundary only
  - add UT that keeps incident non-terminal across first deferred enqueue and finishes it only after retry drain result is known
- Suggested priority: `3`

### 5) ACK location not persisted

- Status: `OPEN`
- Exact evidence:
  - recovered meta merges `locations[workerAddr] = ACK` into memory: `src/datasystem/master/object_cache/oc_metadata_manager.cpp:4133-4135`
  - final persistence only serializes metadata blob via `CreateOrUpdateMeta(...)`: `src/datasystem/master/object_cache/oc_metadata_manager.cpp:4140-4146`
  - no normal location-table persistence path is called for recovered ACK locations in `RecoveryMetaFromWorker()`
- Minimal TDD fix boundary:
  - `oc_metadata_manager.cpp` only
  - add UT that recovers meta from worker, restarts/reloads via object-store boundary, and verifies ACK location survives persistence
- Suggested priority: `3`

### 6) QueryAndGet admission miss

- Status: `OPEN`
- Exact evidence:
  - local inline-data fast path returns the original `K_NOT_READY` when the worker says `Worker is not accepting`: `src/datasystem/master/object_cache/oc_metadata_manager.cpp:4786-4791`
  - caller treats that as RPC failure rather than per-key inline miss fallback: `src/datasystem/master/object_cache/oc_metadata_manager.cpp:2683-2718`
  - client already understands "no `data_result` means per-key fast-path miss and phase two fallback": `src/datasystem/client/transport/metadata/object_metadata_client.cpp:355-357`
- Minimal TDD fix boundary:
  - master-side `QueryAndGet` path only; client fallback is already present
  - add UT that forces local admission close and verifies metadata RPC stays successful while the object falls back to phase two
- Suggested priority: `4`

### 7) membership recovery loses address

- Status: `OPEN`
- Exact evidence:
  - topology builder passes `(address, timestamp)` into recovery handler: `src/datasystem/worker/worker_oc_server.cpp:1148-1150`
  - `HandleMembershipRecovery()` drops both parameters with `(void)address; (void)timestamp;`: `src/datasystem/worker/worker_oc_server.cpp:1388-1391`
  - recovery ownership reconcile is then invoked without preserving the authoritative recovered member identity: `src/datasystem/worker/worker_oc_server.cpp:1392-1400`
- Minimal TDD fix boundary:
  - `worker_oc_server.cpp` only
  - add UT that feeds a non-local recovered address and verifies the reconcile request uses that address/timestamp instead of silently collapsing to local-only semantics
- Suggested priority: `4`

### 8) stale callback occupies in-flight

- Status: `OPEN`
- Exact evidence:
  - request path sets `objectCacheRecoveryRequestInFlight_ = true` before dispatch: `src/datasystem/worker/worker_oc_server.cpp:1354-1358`
  - stale callback branch returns early when generation drift is detected and does not clear `objectCacheRecoveryRequestInFlight_`: `src/datasystem/worker/worker_oc_server.cpp:1374-1379`
  - current exact-head tests cover current-generation retry and reattach paths, but not stale-generation callback release: `tests/ut/worker/worker_oc_server_recovery_evidence_test.cpp:208-254,324-340`
- Minimal TDD fix boundary:
  - `worker_oc_server.cpp`
  - extend `worker_oc_server_recovery_evidence_test.cpp` with one stale-generation callback case
- Suggested priority: `4`

### 9) local recovery void callback does not retry

- Status: `OPEN`
- Exact evidence:
  - `WorkerIsolationCoordinator::OnLocalRecovery()` is `void`: `src/datasystem/worker/runtime/worker_isolation_coordinator.cpp:46-75`
  - every side effect is best-effort `LOG_IF_ERROR(...)`; there is no retry, escalation, or caller-visible failure: `src/datasystem/worker/runtime/worker_isolation_coordinator.cpp:56-74`
  - current UT only proves happy-path phase/order transitions: `tests/ut/worker/worker_isolation_coordinator_test.cpp:67-89`
- Minimal TDD fix boundary:
  - keep the change inside `worker_isolation_coordinator.cpp` and its UT
  - add one failure-injected UT that proves local-recovery failures either schedule retry or surface a retriable status to the caller boundary
- Suggested priority: `4`

### 10) NotifyRemoteGet silent-skip keys

- Status: `OPEN`
- Exact evidence:
  - version-mismatch path logs `skip migration` and `continue`s without appending explicit success/failure/skipped signal into `rsp`: `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp:3495-3500`
  - exact HEAD already tightened confirmed-copy handling after transfer:
    - `tests/ut/worker/object_cache/migrate_data_service_test.cpp:1528,1575,1602,1669,1696`
  - so the remaining gap is specifically the silent version-mismatch skip path, not the whole NotifyRemoteGet confirmation flow
- Minimal TDD fix boundary:
  - `worker_oc_service_get_impl.cpp`
  - extend existing NotifyRemoteGet UTs with one version-mismatch case that must surface an explicit skipped/failed key contract
- Suggested priority: `5`

### 11) ClearObjects null SafeObject

- Status: `OPEN`
- Exact evidence:
  - after `ReserveGetAndLock(...)` succeeds, exact HEAD dereferences `entry->Get()->GetCreateTime()` without null guard: `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp:477-500`
  - `ObjectKV objectKV(objectKey, *entry);` also assumes non-null `SafeObject`: `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp:500`
- Existing coverage boundary:
  - retry-budget UTs exist, but there is no null-`SafeObject` UT in current exact-head `worker_oc_service_impl_test.cpp:3116-3235`
- Minimal TDD fix boundary:
  - `worker_oc_service_clear_data_flow.cpp`
  - add one UT that injects a locked entry with null inner object and expects unresolved accounting instead of dereference
- Suggested priority: `5`

### 12) clear-data budget

- Status: `ADDRESSED`
- Exact evidence:
  - retry budget is bounded by `CLEAR_DATA_MAX_RETRY_ROUNDS`: `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp:288-300`
  - exact-head UTs already pin the budget behavior:
    - `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp:3116-3151`
    - `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp:3153-3185`
    - `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp:3187-3235`
- Residual note:
  - this does not fix the null-`SafeObject` path above; the budget fix and the null-dereference fix should stay separate
- Suggested priority: `8`

### 13) rolling upgrade event_type

- Status: `OPEN`
- Exact evidence:
  - worker-side request writes `ClusterEventType` directly into `ReconciliationQueryPb::event_type`: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp:2931-2935`
  - worker-side enum ordinals are unrelated to protobuf enum ordinals:
    - `src/datasystem/worker/cluster_event_type.h:31-47`
    - e.g. `NODE_NETWORK_RECOVERY` is ordinal `4`, `NODE_RESTART` is ordinal `8`
  - protobuf expects `RESTART = 0`, `LOCAL_ISOLATION = 1`, `NETWORK_RECOVERY = 2`: `src/datasystem/protos/master_object.proto:455-463`
  - master consumes the proto enum literally:
    - `LOCAL_ISOLATION` branch: `src/datasystem/master/object_cache/master_oc_service_impl.cpp:810-816`
    - `NETWORK_RECOVERY` branch: `src/datasystem/master/object_cache/master_oc_service_impl.cpp:818-825`
    - everything else falls through to restart: `src/datasystem/master/object_cache/master_oc_service_impl.cpp:827-832`
- Conclusion:
  - this is stronger than a style concern; exact HEAD serializes numerically incompatible enum domains and therefore is not rolling-upgrade safe by construction
- Minimal TDD fix boundary:
  - add an explicit translation function at the request assembly boundary
  - UT must pin numeric mapping for all sent reconciliation event types
- Suggested priority: `6`

## Suggested landing order

1. `worker_oc_server.cpp`: coordinator construction, DS bootstrap, membership-recovery address, stale callback
2. `worker_isolation_coordinator.cpp`: local-recovery retry/error contract
3. `oc_metadata_manager.cpp` plus `metadata_recovery_manager.cpp` plus proto: PushMeta partial failure and ACK persistence
4. `slot_recovery_manager.cpp`: deferred incident completion boundary
5. `oc_metadata_manager.cpp`: QueryAndGet admission miss degradation
6. `worker_oc_service_get_impl.cpp`: NotifyRemoteGet skipped-key contract
7. `worker_oc_service_clear_data_flow.cpp`: null `SafeObject`
8. `worker_oc_service_impl.cpp`: explicit reconciliation event translation

## Final call

- `OPEN`: 12 exact-head issues remain.
- `ADDRESSED`: only `clear-data budget` is convincingly closed on exact HEAD.
- `OBSOLETE`: none of the listed 2026-07-30 comments are obsolete on exact HEAD.
- Most leverage-per-line next step is to take `worker_oc_server.cpp` as the first TDD slice, because it closes four separate open comments with one narrow ownership boundary.
