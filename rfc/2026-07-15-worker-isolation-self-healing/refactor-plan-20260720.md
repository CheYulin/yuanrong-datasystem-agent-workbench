# Worker Self-Healing Cohesion Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PR !1405's worker self-healing path more cohesive without large changes to existing topology, metadata,
slot, stream, or KV flows.

**Architecture:** Use thin worker-local abstractions around the current implementation. Keep `ICoordinationBackend` and
`TopologyController` as the cluster boundary; worker self-healing only closes local admission and consumes cluster-owned
evidence. Reuse existing `WorkerRuntimeStateManager`, `WorkerRecoveryController`, `WorkerServiceAdmission`, and
`WorkerRecoveryEvidenceAdapter` instead of replacing them.

**Tech Stack:** C++17, CMake, Bazel 7.4.1 remote build, gtest/gmock, existing DataSystem worker/cluster test
infrastructure, URMA mock enabled for UT/ST runs that need URMA paths.

## Global Constraints

- Use the existing worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/worker-self-healing-main-20260716`.
- Rebase latest `main/master` before implementation and again before final regression.
- Minimize implementation changes to `src/datasystem/worker` and focused worker tests.
- Cross-module cluster information access must go through `ICoordinationBackend`; modules outside a concrete backend
  implementation must not directly call or depend on that backend's internal store/client/session/watch classes.
- Any cluster interaction must go through `ICoordinationBackend` interfaces or worker-owned callbacks; do not make the
  worker self-healing plan depend on direct `EtcdStore` or ETCD adapter internals.
- Worker self-healing code must not directly call `TopologyController`, `TopologyEngine` control internals,
  `EtcdStore`, `EtcdKeepAlive`, or other kvstore/backend implementation classes. It may only consume cluster state and
  backend health through `ICoordinationBackend` public interfaces, public immutable topology evidence exposed by that
  interface, or callbacks registered through that interface.
- `ICoordinationBackend` must provide the worker-facing callback/evidence boundary for local control-backend isolation
  and recovery. Implementations may map these callbacks to ETCD keepalive events, coordinator-service observations, or
  metastore-backed evidence internally, but worker code must not know which backend path produced the event.
- Do not directly write `ClusterTopologyPb`, cluster node table, hash ring membership, topology stamp, or master
  metadata from worker self-healing code.
- Do not count disabled tests as acceptance coverage.
- Report every new UT/ST case name and observed execution time.
- Use `clang-format`/`clang-tidy` only on changed source files; avoid format-only noise.
- Keep `docs/superpowers` out of tracked source changes; planning docs live in this workbench RFC directory.

---

## File Structure

Create:

- `src/datasystem/worker/worker_recovery_evidence_tracker.h`: generation-aware holder for recovery evidence freshness.
- `src/datasystem/worker/worker_recovery_evidence_tracker.cpp`: tracker implementation.
- `tests/ut/worker/worker_recovery_evidence_tracker_test.cpp`: focused tracker UT.
- `src/datasystem/worker/runtime/worker_runtime.h`: stable worker-local runtime facade for state, admission, and
  topology-availability mapping.
- `src/datasystem/worker/runtime/worker_runtime.cpp`: facade implementation that owns runtime state and delegates to
  admission/evidence helpers.
- `src/datasystem/worker/worker_isolation_coordinator.h`: thin coordinator for local isolation/recovery callback actions.
- `src/datasystem/worker/worker_isolation_coordinator.cpp`: coordinator implementation.
- `tests/ut/worker/worker_isolation_coordinator_test.cpp`: focused coordinator UT.
- `src/datasystem/worker/worker_admission_facade.h`: named admission methods and guard acquisition for normal/recovery paths.
- `src/datasystem/worker/worker_admission_facade.cpp`: facade implementation.
- `tests/ut/worker/worker_admission_facade_test.cpp`: focused facade UT.

Modify:

- `src/datasystem/worker/CMakeLists.txt`: add the three thin libraries and link them only where used.
- `src/datasystem/worker/BUILD.bazel`: add matching `ds_cc_library` targets.
- `tests/ut/worker/BUILD.bazel`: add three focused test targets and include them in `all_worker_tests`.
- `src/datasystem/worker/object_cache/worker_recovery_evidence_adapter.h`: keep the existing aggregation API unchanged.
- `src/datasystem/worker/object_cache/worker_recovery_evidence_adapter.cpp`: keep existing aggregation behavior unchanged.
- `src/datasystem/worker/object_cache/worker_oc_service_impl.h`: store the tracker or accept active generation for evidence report building.
- `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`: invalidate evidence on recovery start and publish only matching-generation reports.
- `src/datasystem/worker/worker_oc_server.h`: add a `std::unique_ptr<WorkerIsolationCoordinator>` member.
- `src/datasystem/worker/worker_oc_server.cpp`: replace inline local isolation/recovery lambdas with coordinator calls.
- `src/datasystem/cluster/coordination_backend/coordination_backend.h`: document the member/controller callback ownership
  contract only if the interface lacks enough wording for review.
- `tests/ut/cluster/coordination_backend_contract_test.cpp`: optional contract test for `ICoordinationBackend` role
  separation, implemented with fake backends rather than ETCD internals.
- Existing hot-path files such as `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`,
  `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp`, and
  `src/datasystem/worker/worker_service_impl.cpp`.

## Worker Runtime Module Boundary

Use `worker/runtime` as the long-term module name instead of `worker/self_healing`. The module name describes the
worker-local responsibility rather than the story name.

### Files Owned By `worker/runtime`

- `worker_runtime.h/.cpp`: public facade. This is the preferred include for `worker_oc_server` and other worker entry
  points.
- `worker_runtime_state.h/.cpp`: local service mode, isolation reason, evidence snapshot, and state transition rules.
- `worker_service_admission.h/.cpp`: admission matrix for `WorkerAdmissionKind`.
- `worker_recovery_controller.h/.cpp`: common recovery evidence model and builder.
- `worker_recovery_evidence_tracker.h/.cpp`: optional generation-aware evidence freshness tracker when the generic
  tracker is shared by OC/KV/Stream paths.
- `worker_topology_availability_admission.h/.cpp`: adapter from cluster topology availability evidence to local runtime
  state/admission.
- `control_backend_failure_scope.h/.cpp`: private runtime helper that classifies a keepalive/control-backend failure as
  local isolation, global outage, or inconclusive. This replaces the PR-internal root-level
  `worker_control_backend_scope.*` placement.

Do not move object-cache-local recovery ownership into `worker/runtime`. Keep `ObjectTable`,
`worker_recovery_evidence_adapter`, `ObjectCacheRecoveryEvidenceTracker`, and `ObjectCacheOwnershipReconciler` in
`worker/object_cache` because they know metadata, slot, resource, and object ownership details.

### Public Facade

The stable public facade should stay small:

```cpp
class WorkerRuntime {
public:
    WorkerRuntimeSnapshot GetSnapshot() const;

    void MarkStarting(std::string detail = {});
    void MarkJoining(std::string detail = {});
    void MarkRunning(WorkerRecoveryEvidenceReport report);
    void MarkRecovering(WorkerIsolationReason reason, std::string detail,
                        WorkerRecoveryEvidenceReport report = {});
    void MarkLocalIsolated(WorkerIsolationReason reason, std::string detail = {});
    void MarkDraining(WorkerIsolationReason reason, std::string detail = {});
    void MarkOutOfMemory(std::string detail = {});
    void MarkStopping(WorkerIsolationReason reason, std::string detail = {});

    Status CheckAdmission(WorkerAdmissionKind kind) const;
    std::optional<WorkerRuntimeReadGuard> TryAcquireReadGuard(WorkerAdmissionKind kind) const;

    void ApplyTopologyAvailability(cluster::TopologyAvailabilityLevel level,
                                   const WorkerRecoveryEvidenceReport *report = nullptr);
    bool ShouldOpenTopologyServingAdmission(cluster::TopologyAvailabilityLevel level) const;
};
```

The facade may expose these stable types:

- `WorkerServiceMode`
- `WorkerIsolationReason`
- `WorkerAdmissionKind`
- `WorkerRunningEvidence`
- `WorkerRecoveryEvidenceReport`
- `WorkerRuntimeSnapshot`
- `WorkerRecoveryEvidenceBuilder`
- `WorkerRuntime`

### Hidden Details

Callers must not depend on:

- the internal admission matrix;
- `UpdateLocked` or other state-manager internals;
- how evidence mask values are computed for metrics/protos;
- how topology availability maps to `RUNNING`, `RECOVERING`, `LOCAL_ISOLATED`, `DRAINING`, or `OUT_OF_MEMORY`;
- how recovery generations are incremented or stale evidence is rejected;
- concrete coordination backend internals such as `EtcdStore`.

### Dependency Direction

- `worker_oc_server` may depend on `worker/runtime` and pass object-cache evidence reports into it.
- `worker/object_cache`, `worker/kv`, and `worker/stream` may consume runtime admission and evidence types.
- `worker/runtime` may consume cluster public evidence such as `TopologyAvailabilityLevel`.
- `worker/runtime` must not depend on object-cache internals, master metadata internals, hash-ring mutation details, or
  concrete backend implementation classes.
- `worker/runtime` must not directly call `TopologyController`, `TopologyEngine` control/mutation methods, `EtcdStore`,
  `EtcdKeepAlive`, or kvstore backend internals. Runtime code should receive topology/backend observations from
  `ICoordinationBackend` or from a narrow adapter implemented inside the coordination backend boundary.
- Runtime/worker callback integration must be covered across backend paths, not only ETCD:
  pure ETCD keepalive isolation/recovery, coordinator-service availability changes, metastore-backed startup/recovery
  when applicable, and the no-external-backend or unsupported-backend fallback path. Pure ETCD backend and Coordinator
  backend are both required coverage paths. Unsupported paths must fail closed with explicit
  `INCONCLUSIVE`/`K_NOT_SUPPORTED` behavior rather than silently opening serving admission.

### Runtime Interface Usage Rules

- Components outside `worker/runtime` should include `datasystem/worker/runtime/worker_runtime.h` by default.
- Components outside `worker/runtime` may include dedicated stable model headers only when necessary:
  `worker_runtime_state.h` for `WorkerRuntimeSnapshot`/mode/reason types,
  `worker_service_admission.h` for `WorkerAdmissionKind`, and
  `worker_recovery_controller.h` for evidence report/builder types.
- Components outside `worker/runtime` must not include or depend on implementation-detail headers once the facade exists:
  `worker_recovery_evidence_tracker.h`, internal state transition helpers, admission matrix helpers, or topology
  mapping internals. If a caller needs one of these, first add a narrow method to `WorkerRuntime`.
- Components outside `worker/runtime` must not include `control_backend_failure_scope.h` or call its classifier directly.
  Backend failure-scope classification is an implementation detail behind `WorkerRuntime` or `worker_oc_server` callback
  orchestration during the transition.
- Callers must not inspect `WorkerServiceMode` and reimplement the admission matrix locally. Use
  `WorkerRuntime::CheckAdmission()` or `WorkerRuntime::TryAcquireReadGuard()`.
- Worker common services such as `WorkerServiceImpl` must depend on the runtime facade directly. They must not store
  `WorkerRuntimeStateManager *` or construct `WorkerServiceAdmission` themselves. Use the facade method, for example
  `runtimeFacade.CheckAdmission(WorkerAdmissionKind::CLIENT_REGISTRATION_RPC, "RegisterClient")`.
- Callers must not directly update evidence bits, evidence masks, or recovery generations. Build a
  `WorkerRecoveryEvidenceReport` from local business evidence and pass it to `WorkerRuntime`.
- Callers must not directly call topology availability helper functions to decide health-file or serving-admission state.
  Use `WorkerRuntime::ApplyTopologyAvailability()` and `WorkerRuntime::ShouldOpenTopologyServingAdmission()`.
- `worker/runtime` implementation files may include the private helper headers; public headers must not expose private
  helper types in method signatures.
- Build rules should expose one public `worker_runtime` target and keep helper targets private or narrowly depended on by
  the facade target. New OC/KV/Stream code should depend on the public target, not helper targets.
- Keep the runtime/recovery metrics set minimal for PR !1405. Target 8 metrics only:
  `worker_service_mode`, `worker_service_reason`, `worker_recovery_phase`, `worker_recovery_evidence_mask`,
  `worker_admission_reject_total`, `worker_admission_reject_latency`, `worker_object_table_lock_hold_latency`, and
  `worker_metadata_recovery_batch_latency`. Do not keep per-mode admission reject counters,
  `worker_mode_transition_latency`, `worker_recovery_candidate_count`, or `worker_cleanup_batch_latency`.
- The current `worker_control_backend_scope.*` implementation uses object-cache worker-worker RPCs as its peer probe
  transport. When moving under `worker/runtime`, hide that dependency behind a narrow probe interface so the public
  runtime API does not expose object-cache transport classes.
- Keep the current `worker/object_cache/worker_worker_peer_state_codec.*` behavior for PR !1405, including backend
  observation refresh and UUID string/bytes conversion. In the follow-up refactor, wrap it behind a runtime-private
  `IControlBackendPeerProbe` interface so `control_backend_failure_scope` depends on a probe abstraction, not directly
  on object-cache worker-worker RPC codec/transport classes.

## PR-Internal ObjectCache Abstraction Follow-Up

This subsection records the current PR-internal `src/datasystem/worker/object_cache` cohesion review. It is scoped to
the diff between `main/master` and PR !1405, not to unrelated mainline cleanup.

### Current Shape

- `ObjectTable` is a real object-cache-local wrapper that replaces the previous
  `using ObjectTable = SafeTable<ImmutableString, ObjectInterface>` alias. Most `Insert`, `Reserve*`, `Get`, `Erase`,
  `begin/end`, and `GetSize` methods are compatibility surface copied from the former `SafeTable` usage pattern so
  existing object-cache callers do not need a broad rewrite.
- The new `ObjectTable` behavior is the recovery snapshot index: 64 sharded key/generation indexes,
  `BeginRecoverySnapshot`, `NextRecoverySnapshotBatch`, and `GetRecoverySnapshotObject`. This index is a lightweight
  key/generation side table, not a copy of object payloads.
- `ObjectTable` consistency depends on it being the only mutation entry for object-cache-local table content. Insert,
  reserve, and erase paths update or roll back the recovery index under the shard lock. New code must not bypass
  `ObjectTable` and mutate the embedded `SafeTable` directly.
- The largest remaining scatter is not `ObjectTable`; it is self-healing state spread across
  `WorkerOCServiceImpl`, `SlotRecoveryManager`, `NodeSelector`, `WorkerOCEvictionManager`, and
  `worker_recovery_evidence_adapter`.

### Refactor Direction

- **First priority: `ObjectCacheRecoveryEvidenceTracker`.** Keep this object-cache-local if the generic
  `WorkerRecoveryEvidenceTracker` is not enough to own metadata, slot, resource, and ownership evidence together.
  It should invalidate stale generation evidence, merge reports from current modules, and make
  `BuildObjectCacheRecoveryEvidenceReport` auditable from one place.
- **Second priority: `ObjectCacheOwnershipReconciler`.** Extract `ReconcileMembershipChange`,
  `ReconcileLocalIsolationOwnership`, `ReconcileNetworkRecoveryOwnership`, and `ScheduleReconciliationRequest` from
  `WorkerOCServiceImpl` behind `Status Reconcile(ObjectCacheReconciliationReason reason)`.
- **Third priority: `ObjectCacheResourceRecoveryCoordinator`.** Only introduce this if resource readiness continues to
  spread across `NodeSelector`, `WorkerOCServiceImpl`, `WorkerOCEvictionManager`, and `WorkerOCServer` callbacks. It
  should own memory/disk recovery-required flags, resource generation, and resource evidence publication.
- **Fourth priority: `ObjectCacheAdmissionGate`.** Wrap object-cache-specific local read/write, peer read, and migration
  target admission checks while still consuming the shared `WorkerRuntimeStateManager`/`WorkerServiceAdmission` policy.
- **Do not expand `ObjectTable`.** Keep it focused on local object table access plus recovery snapshot indexing. Do not
  add admission, cluster, topology, or ownership-reconciliation responsibilities to it.

### TDD Acceptance For The Follow-Up

- Add focused UTs before refactoring each abstraction.
- For `ObjectCacheRecoveryEvidenceTracker`, include stale-generation rejection, incomplete default evidence, metadata
  evidence update, slot evidence update, resource readiness update, and complete-report merge cases.
- For `ObjectCacheOwnershipReconciler`, include restart, local-isolation, and network-recovery reason mapping tests with
  fake master APIs; verify no direct dependency on ETCD internals.
- For `ObjectTable`, keep the current snapshot consistency tests as regression coverage and add only targeted tests if
  the mutation API changes.
- Report added case count and elapsed time for every new UT/ST group.

## Master ObjectCache Reconciliation Boundary

PR !1405 also changes master-side object-cache metadata recovery. This code is not part of `worker/runtime`; it belongs
to `src/datasystem/master/object_cache` because it owns master metadata, primary-copy ownership, and worker notification
semantics.

### Event-Triggered Operations

- `ReconciliationQueryPb::LOCAL_ISOLATION` should trigger local-isolation metadata fencing:
  mark the worker faulted, reconcile primary copies whose primary is the isolated worker, persist
  `PRIMARY_COPY_INVALID` for the old primary, and keep objects on the isolated worker when no acknowledged replacement
  exists.
- `ReconciliationQueryPb::NETWORK_RECOVERY` should trigger recovery replay:
  remove the fault mark, reconcile primary copies, push pending worker operations, and push metadata/grefs back to the
  recovering worker. If replay fails, restore the fault mark.
- Restart reconciliation should remain a separate flow:
  remove worker-owned metadata, clear async worker ops, and push restart metadata when reconciliation is enabled.

### Decoupling Shape

Do not let `MasterOCServiceImpl::IfNeedTriggerReconciliationImpl` directly know every metadata operation. Route the
event through a small master-OC event handler that depends on injected action hooks:

```cpp
enum class OCReconciliationEvent {
    RESTART,
    LOCAL_ISOLATION,
    NETWORK_RECOVERY,
};

struct OCReconciliationRequest {
    OCReconciliationEvent event;
    std::string workerAddr;
    int64_t eventTimestamp{ 0 };
    bool isOffline{ false };
};

class IOCReconciliationActions {
public:
    virtual ~IOCReconciliationActions() = default;
    virtual Status MarkWorkerFaulted(const std::string &workerAddr) = 0;
    virtual Status ClearWorkerFaulted(const std::string &workerAddr) = 0;
    virtual Status ReconcilePrimaryOwnership(const std::string &workerAddr,
                                             bool requireAcknowledgedReplacement) = 0;
    virtual Status RemoveWorkerMetadata(const std::string &workerAddr) = 0;
    virtual Status ClearWorkerAsyncOps(const std::string &workerAddr) = 0;
    virtual Status NotifyPendingWorkerOps(const std::string &workerAddr, int64_t timestamp) = 0;
    virtual Status PushWorkerMetadata(const std::string &workerAddr, int64_t timestamp, bool isRestart) = 0;
};

class OCReconciliationEventHandler {
public:
    explicit OCReconciliationEventHandler(IOCReconciliationActions &actions);
    Status Handle(const OCReconciliationRequest &request);
};
```

The production `IOCReconciliationActions` implementation should live in `master/object_cache` and delegate to
`OCMetadataManager`, `OCNotifyWorkerManager`, and object-store persistence. Tests should inject fake actions to verify
event ordering, fault-mark rollback, and error propagation without constructing full master metadata state.

### Primary Ownership Hook

Primary-copy promotion/fencing should also be isolated behind a narrower hook or component:

```cpp
class IOCPrimaryOwnershipReconciler {
public:
    virtual ~IOCPrimaryOwnershipReconciler() = default;
    virtual Status ReconcileWorkerPrimaryOwnership(const std::string &workerAddr,
                                                   bool requireAcknowledgedReplacement) = 0;
};
```

This component owns candidate collection, acknowledged-copy selection, `SendChangePrimaryCopy`, metadata primary commit,
and persisted `PRIMARY_COPY_INVALID` fencing. `OCMetadataManager` should eventually delegate these steps instead of
embedding them inline.

### Boundary Rules

- `master/object_cache` must not depend on `worker/runtime`, `WorkerServiceMode`, or runtime admission details.
- `worker/runtime` must not call master metadata recovery methods.
- `MasterOCServiceImpl` should translate proto event type to `OCReconciliationEvent` and call the event handler; it
  should not directly sequence metadata cleanup, primary promotion, and worker notification.
- Event handler tests must assert operation order for LOCAL_ISOLATION, NETWORK_RECOVERY, and RESTART, including failure
  rollback for NETWORK_RECOVERY.
- Before final PR update, reduce format-only noise in `master/object_cache`, especially `oc_metadata_manager.cpp` and
  `oc_metadata_manager.h`. Keep behavior hunks for metadata recovery, primary ownership promotion/fencing, event
  handling, and notify error propagation; avoid whole-file clang-format or unrelated signature/whitespace churn.

## Story Build Use-Case Coverage And Missing Follow-Ups

This section is the acceptance map against the story HTML `#build` / `#8` self-verification cases. A PR is not ready for
merge until every case is either covered by an enabled UT/ST/CI regression or explicitly split into a follow-up accepted
by reviewers.

### Required Acceptance Cases

| Story case | Required coverage | Current judgement | Plan action |
| --- | --- | --- | --- |
| `EtcdKeepAliveIsolationTest.ConfirmedLocalIsolationPublishesDeleteAndIsolationCallbackOnce` | Local keepalive isolation must not self-kill; callback is once-only; normal admission closes. | Partially covered by ETCD-path implementation, but must be rechecked after moving interaction behind `ICoordinationBackend`. | Keep existing case and add backend-interface assertion that worker code receives the event via `ICoordinationBackend`, not `EtcdStore`. |
| `EtcdKeepAliveIsolationTest.GlobalEtcdOutageDoesNotPublishDeleteOrCloseAdmission` | Global backend outage is not local isolation; no delete event, no admission close. | Partially covered on pure ETCD path. | Keep as regression and add coordinator-backend variant that returns `INCONCLUSIVE`/global unavailable. |
| `HashRingSelfPassiveScaleDownDoesNotKill` | Local worker missing from ring or in `del_node_info` must enter `LOCAL_ISOLATED`, not `SIGKILL`; voluntary path remains exit. | Missing or not independently acceptable yet. | Add enabled UT around local passive scale-down decision with death/kill hook faked; assert no kill and runtime state transition. |
| `VoluntaryScaleDownStillStopsAfterDrain` | Admin/voluntary scale-down remains `DRAINING -> STOPPING`; self-healing must not reopen service. | Covered conceptually by state/admission tests, but needs end-to-end regression after runtime facade refactor. | Add focused runtime/service test for voluntary deletion path and keep existing scale-down ST in regression set. |
| `RecoveredOldPrimaryDoesNotOverrideMasterPrimary` | Old primary on recovered worker must obey master-confirmed primary and downgrade/clear. | Partially covered by master/object-cache reconciliation changes. | Keep current logic; add event-handler fake-action ordering test and one object-cache ST with old primary losing ownership. |
| `OrphanLocalDataRequiresRecoveryOrClearDataWithoutMeta` | Master meta cleared but local data remains: recovery enabled recovers provable data, disabled clears or keeps invisible. | Partially covered by clear-data flow and metadata recovery tests. | Add explicit two-configuration ST/UT pair for `enable_metadata_recovery=true/false`. |
| `OtherWorkersRecoverMetadataBeforeClearingDataWithoutMetadata` | Other workers first try metadata recovery, then clear only failed/unrecoverable entries. | Partially covered by existing clear-data-without-meta path; retry/failure matrix incomplete. | Add fake master recovery summary test covering success, retryable failure, hard failure, and recovery disabled. |
| `IsolatedWorkerMetaCleanupAllowsNewOwnerRebuild` | Isolated worker old meta cleanup allows new owner rebuild/update; isolated worker data remains invisible. | Partially covered by master meta cleanup path. | Add ST or integration UT verifying new owner rebuild/update after local isolation cleanup event. |
| `RecoverableLocalDataRebuildsOrUpdatesMetadata` | Isolated worker with provably owned local data rebuilds/updates metadata and reports success/failed summary. | Gap for isolated worker E2E; lower-level recovery exists. | Add isolated-worker recovery ST that drives runtime `RECOVERING`, metadata recovery summary, and evidence acceptance. |
| `RecoveredCoordinationEntersRecoveringBeforeRunning` | Coordination recovery must enter `RECOVERING` first; ordinary read/write stays closed until all evidence passes. | Partially covered by runtime state/evidence tests. | Keep existing runtime tests and add server callback test through `RuntimeFacade`. |
| `WorkerServiceAdmissionRejectsReadWriteDuringIsolation` | Object/KV/Stream ordinary read/write must fail fast in `LOCAL_ISOLATED` and `RECOVERING`. | Object path is better covered than KV/Stream; KV/Stream remain gaps. | Add KV Get/Set and Stream Publish/Subscribe admission tests; do not rely only on indirect lifecycle tests. |
| `MigrationTargetFiltersIsolatedWorker` | Rebalance/migration target selection filters `LOCAL_ISOLATED`, `RECOVERING`, and `DRAINING`; target RPC rejects too. | Partially covered by rebalance guard. | Add candidate-selection UT plus target-RPC admission regression. |
| `RecoveringWorkerFallsBackToLocalIsolatedOnDisconnect` | A second control-backend disconnect during recovery must not half-open ordinary service. | Missing. | Add recovery-controller test that injects backend disconnect while metadata/slot recovery is running; expect `RECOVERING` remains closed or transitions to `LOCAL_ISOLATED`. |
| `MetadataRecoveryBestEffortRetryDoesNotBlockAvailability` | Failed metadata rebuild entries have finite retry budget; success entries and other workers proceed. | Missing/incomplete. | Add retry-budget UT using fake `RecoverMetadataWithSummary`; assert failed entries do not block successful evidence or other worker availability. |
| `MetadataRecoveryDoesNotHoldObjectTableLockDuringFullScan` | Recovery scans by candidate/shard/snapshot batch; no long write-lock full-table traversal. | Partially covered by `ObjectTable` snapshot index, but needs performance-oriented regression. | Add focused ObjectTable/concurrent scan UT with bounded batch size and lock-hold metric assertion where feasible. |
| topology/metadata/slot/notify-worker UT + KV/Object ST regression | Existing recovery capability must not regress; legal recovery RPCs must not be blocked by admission. | Needs final full regression after rebase. | Run and record focused CMake/Bazel UT/ST plus CI retest; report case count and elapsed time for new cases. |

### Missing Case Implementation Tasks

- [ ] Add `HashRingSelfPassiveScaleDownDoesNotKill` as an enabled UT. The test must fake the process-kill hook and prove
  non-voluntary local passive removal calls `RuntimeFacade::MarkLocalIsolated` without raising `SIGKILL`; voluntary local
  removal must still reach `DRAINING -> STOPPING`.
- [ ] Add pure ETCD and coordinator-backend callback coverage. Both paths must deliver local isolation/recovery through
  `ICoordinationBackend` callback/evidence APIs. Worker code must not include or call `EtcdStore`, `EtcdKeepAlive`,
  `TopologyController`, or `TopologyEngine` internals.
- [ ] Add `RecoveredCoordinationEntersRecoveringBeforeRunning` through `RuntimeFacade`, not direct state-manager calls.
  The test must show ordinary admission remains closed until membership, topology/ring, metadata, slot, ownership, and
  resource evidence are accepted.
- [ ] Add `WorkerServiceAdmissionRejectsReadWriteDuringIsolation` for Object, KV, and Stream ordinary APIs. Each test
  must assert fail-fast status includes mode/reason and no metadata/data write path is entered.
- [ ] Add `MigrationTargetFiltersIsolatedWorker` for candidate filtering and target RPC rejection. Cover
  `LOCAL_ISOLATED`, `RECOVERING`, and `DRAINING`.
- [ ] Add `RecoveringWorkerFallsBackToLocalIsolatedOnDisconnect`. Inject a second coordination/backend disconnect during
  metadata or slot recovery. Assert service never becomes half-open and phase/reason remains observable.
- [ ] Add `MetadataRecoveryBestEffortRetryDoesNotBlockAvailability`. Use fake metadata recovery results with success,
  retryable, and hard-failed entries. Assert retry budget is finite, successful objects produce evidence, failed objects
  remain invisible or enter cleanup, and other workers are not blocked.
- [ ] Add isolated-worker E2E coverage for `RecoverableLocalDataRebuildsOrUpdatesMetadata`. The test must drive
  `LOCAL_ISOLATED -> RECOVERING`, recover or update metadata for provably owned local data, record success/failed
  summary, and open admission only after evidence passes.
- [ ] Add overlap coverage for scale/fault combinations: local isolation during rebalance, voluntary scale-down while
  recovery is pending, and recovery while another worker is being removed. These cases are follow-up scope but must stay
  tracked until covered or explicitly waived.
- [ ] Add performance regression for recovery snapshot scanning. The case must verify batch/shard/snapshot scanning and
  avoid whole-table write-lock traversal. Record batch size, object count, and elapsed time.

### Case Reporting Rules

- Every new UT/ST group must report: case name, added case count, command, elapsed wall time, and whether URMA mock was
  enabled.
- New cases should be kept focused. Target budget: single UT group under 30 seconds locally; single ST group under
  5 minutes unless it is explicitly tagged as full regression.
- Do not count disabled tests, skipped backend variants, or CI-only theoretical coverage as completion.
- Final acceptance summary must include the denominator from this table: 16 story self-verification rows plus the
  scale/fault overlap follow-up group.

## Task 1: Recovery Evidence Tracker

**Files:**

- Create: `src/datasystem/worker/worker_recovery_evidence_tracker.h`
- Create: `src/datasystem/worker/worker_recovery_evidence_tracker.cpp`
- Create: `tests/ut/worker/worker_recovery_evidence_tracker_test.cpp`
- Modify: `src/datasystem/worker/CMakeLists.txt`
- Modify: `src/datasystem/worker/BUILD.bazel`
- Modify: `tests/ut/worker/BUILD.bazel`

**Interfaces:**

- Consumes: `datasystem::worker::WorkerRunningEvidence` and `WorkerRecoveryEvidenceReport`.
- Produces:
  - `using WorkerRecoveryGeneration = uint64_t;`
  - `struct GenerationedWorkerRecoveryEvidenceReport { WorkerRecoveryGeneration generation; WorkerRecoveryEvidenceReport report; };`
  - `class WorkerRecoveryEvidenceTracker`
  - `WorkerRecoveryGeneration BeginRecovery(std::string detail);`
  - `WorkerRecoveryGeneration CurrentGeneration() const;`
  - `void ResetEvidence(WorkerRecoveryGeneration generation, std::string detail);`
  - `bool UpdateEvidence(WorkerRecoveryGeneration generation, WorkerRecoveryEvidenceReport report);`
  - `std::optional<GenerationedWorkerRecoveryEvidenceReport> GetEvidence(WorkerRecoveryGeneration generation) const;`
  - `bool IsComplete(WorkerRecoveryGeneration generation) const;`

- [ ] **Step 1: Write failing tracker UT**

Add tests:

```cpp
TEST(WorkerRecoveryEvidenceTrackerTest, OldGenerationEvidenceIsRejected)
{
    WorkerRecoveryEvidenceTracker tracker;
    auto oldGeneration = tracker.BeginRecovery("first");
    WorkerRecoveryEvidenceBuilder builder;
    ASSERT_TRUE(tracker.UpdateEvidence(oldGeneration, builder.MarkMembershipReady().MarkTopologyReady()
                                                       .MarkMetadataReady().MarkSlotReady()
                                                       .MarkOwnershipReady().MarkResourceReady().BuildReport()));
    ASSERT_TRUE(tracker.IsComplete(oldGeneration));

    auto newGeneration = tracker.BeginRecovery("second");
    EXPECT_FALSE(tracker.IsComplete(newGeneration));
    EXPECT_FALSE(tracker.UpdateEvidence(oldGeneration, builder.BuildReport("stale")));
    EXPECT_FALSE(tracker.GetEvidence(oldGeneration).has_value());
    EXPECT_FALSE(tracker.IsComplete(newGeneration));
}

TEST(WorkerRecoveryEvidenceTrackerTest, EmptyEvidenceDoesNotCompleteNewRecovery)
{
    WorkerRecoveryEvidenceTracker tracker;
    auto generation = tracker.BeginRecovery("isolation");
    WorkerRecoveryEvidenceBuilder builder;
    ASSERT_TRUE(tracker.UpdateEvidence(generation, builder.MarkMembershipReady().BuildReport("partial")));

    EXPECT_FALSE(tracker.IsComplete(generation));
    auto evidence = tracker.GetEvidence(generation);
    ASSERT_TRUE(evidence.has_value());
    EXPECT_EQ(evidence->generation, generation);
    EXPECT_FALSE(evidence->report.evidence.metadataReady);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/worker-self-healing-main-20260716
cmake --build build --target ds_ut -j 40
./build/tests/ut/ds_ut --gtest_filter='WorkerRecoveryEvidenceTrackerTest.*'
```

Expected: build fails because `WorkerRecoveryEvidenceTracker` does not exist.

- [ ] **Step 3: Implement minimal tracker**

Add a mutex-protected tracker. `BeginRecovery()` increments generation, clears report, and records detail. `UpdateEvidence()`
returns false if the passed generation does not equal current generation. `GetEvidence()` returns empty for stale
generations. `IsComplete()` delegates to `IsComplete(report.evidence)`.

- [ ] **Step 4: Wire build targets**

CMake:

```cmake
add_library(worker_recovery_evidence_tracker STATIC worker_recovery_evidence_tracker.cpp)
target_include_directories(worker_recovery_evidence_tracker PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(worker_recovery_evidence_tracker PUBLIC worker_recovery_controller)
```

Bazel:

```python
ds_cc_library(
    name = "worker_recovery_evidence_tracker",
    srcs = ["worker_recovery_evidence_tracker.cpp"],
    hdrs = ["worker_recovery_evidence_tracker.h"],
    deps = [
        ":worker_recovery_controller",
    ],
)
```

- [ ] **Step 5: Run focused UT and record time**

Run:

```bash
time ./build/tests/ut/ds_ut --gtest_filter='WorkerRecoveryEvidenceTrackerTest.*'
```

Expected: PASS. Record elapsed time and case count.

- [ ] **Step 6: Commit**

```bash
git add src/datasystem/worker/worker_recovery_evidence_tracker.* \
        src/datasystem/worker/CMakeLists.txt src/datasystem/worker/BUILD.bazel \
        tests/ut/worker/worker_recovery_evidence_tracker_test.cpp tests/ut/worker/BUILD.bazel
git commit -m "feat(worker): track recovery evidence generation"
```

## Task 2: Apply Evidence Generation to Object Recovery Reports

**Files:**

- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.h`
- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`
- Modify: `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp`
- Modify: `src/datasystem/worker/object_cache/BUILD.bazel`
- Modify: `src/datasystem/worker/object_cache/CMakeLists.txt`

**Interfaces:**

- Consumes: `WorkerRecoveryEvidenceTracker` from Task 1.
- Produces:
  - `worker::WorkerRecoveryGeneration BeginRecoveryEvidenceGeneration(std::string detail);`
  - `worker::WorkerRecoveryEvidenceReport BuildObjectCacheRecoveryEvidenceReport(worker::WorkerRecoveryGeneration generation);`
  - Existing pointer-based resource generation overload remains temporarily for compatibility, but it must not bypass the
    recovery evidence tracker for local-isolation recovery.

- [ ] **Step 1: Write failing OC service UT**

Add tests beside current recovery evidence tests:

```cpp
TEST_F(WorkerOCServiceImplTest, NewRecoveryGenerationInvalidatesOldCompleteEvidence)
{
    auto oldGeneration = impl_->BeginRecoveryEvidenceGeneration("old");
    auto oldReport = impl_->BuildObjectCacheRecoveryEvidenceReport(oldGeneration);
    EXPECT_FALSE(oldReport.evidence.metadataReady);

    auto newGeneration = impl_->BeginRecoveryEvidenceGeneration("new");
    EXPECT_NE(oldGeneration, newGeneration);
    auto staleReport = impl_->BuildObjectCacheRecoveryEvidenceReport(oldGeneration);
    EXPECT_FALSE(staleReport.evidence.membershipReady);
    EXPECT_FALSE(staleReport.evidence.metadataReady);

    auto currentReport = impl_->BuildObjectCacheRecoveryEvidenceReport(newGeneration);
    EXPECT_FALSE(currentReport.evidence.metadataReady);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
time ./build/tests/ut/ds_ut_object --gtest_filter='WorkerOCServiceImplTest.NewRecoveryGenerationInvalidatesOldCompleteEvidence'
```

Expected: compile failure or test failure until service methods are added.

- [ ] **Step 3: Implement minimal OC integration**

Add `WorkerRecoveryEvidenceTracker recoveryEvidenceTracker_;` to `WorkerOCServiceImpl`. Call `BeginRecovery()` when
network recovery reconciliation starts. Make generation-specific `BuildObjectCacheRecoveryEvidenceReport()` return an
empty or incomplete report for stale generations.

- [ ] **Step 4: Run focused UT and record time**

Run:

```bash
time ./build/tests/ut/ds_ut_object --gtest_filter='WorkerOCServiceImplTest.*RecoveryEvidence*'
```

Expected: PASS. Record elapsed time and added case count.

- [ ] **Step 5: Commit**

```bash
git add src/datasystem/worker/object_cache/worker_oc_service_impl.* \
        src/datasystem/worker/object_cache/BUILD.bazel src/datasystem/worker/object_cache/CMakeLists.txt \
        tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp
git commit -m "fix(worker): bind object recovery evidence to generation"
```

## Task 3: Worker Isolation Coordinator

**Files:**

- Create: `src/datasystem/worker/worker_isolation_coordinator.h`
- Create: `src/datasystem/worker/worker_isolation_coordinator.cpp`
- Create: `tests/ut/worker/worker_isolation_coordinator_test.cpp`
- Modify: `src/datasystem/worker/worker_oc_server.h`
- Modify: `src/datasystem/worker/worker_oc_server.cpp`
- Modify: `src/datasystem/worker/CMakeLists.txt`
- Modify: `src/datasystem/worker/BUILD.bazel`
- Modify: `tests/ut/worker/BUILD.bazel`

**Interfaces:**

- Consumes: `WorkerRuntimeStateManager`, `WorkerRecoveryController`, `TopologyServingAdmission` callback, object-cache
  ownership reconciliation callback, and recovery evidence report callback.
- Produces:
  - `struct WorkerIsolationCoordinatorCallbacks`
  - `class WorkerIsolationCoordinator`
  - `void OnLocalIsolation(const Status &status);`
  - `void OnLocalRecovery();`

- [ ] **Step 1: Write failing coordinator UT**

Add tests:

```cpp
TEST(WorkerIsolationCoordinatorTest, LocalIsolationClosesAdmissionAndDoesNotMarkRunning)
{
    WorkerRuntimeStateManager state;
    bool admissionClosed = false;
    WorkerIsolationCoordinator coordinator(state, WorkerIsolationCoordinatorCallbacks{
        .setTopologyServingAdmission = [&](bool open) { admissionClosed = !open; return Status::OK(); },
        .reconcileOwnership = [] { return Status::OK(); },
        .requestRecoveryReconciliation = [] { return Status::OK(); },
        .buildRecoveryEvidence = [] { return WorkerRecoveryEvidenceBuilder().BuildReport("empty"); },
    });

    coordinator.OnLocalIsolation(Status(K_RUNTIME_ERROR, "local isolation"));
    auto snapshot = state.GetSnapshot();
    EXPECT_EQ(snapshot.mode, WorkerServiceMode::LOCAL_ISOLATED);
    EXPECT_TRUE(admissionClosed);
}

TEST(WorkerIsolationCoordinatorTest, RecoveryDoesNotMarkRunningWithIncompleteEvidence)
{
    WorkerRuntimeStateManager state;
    bool admissionOpen = false;
    WorkerIsolationCoordinator coordinator(state, WorkerIsolationCoordinatorCallbacks{
        .setTopologyServingAdmission = [&](bool open) { admissionOpen = open; return Status::OK(); },
        .reconcileOwnership = [] { return Status::OK(); },
        .requestRecoveryReconciliation = [] { return Status::OK(); },
        .buildRecoveryEvidence = [] { return WorkerRecoveryEvidenceBuilder().MarkMembershipReady().BuildReport("partial"); },
    });

    coordinator.OnLocalRecovery();
    auto snapshot = state.GetSnapshot();
    EXPECT_EQ(snapshot.mode, WorkerServiceMode::RECOVERING);
    EXPECT_FALSE(admissionOpen);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
time ./build/tests/ut/ds_ut --gtest_filter='WorkerIsolationCoordinatorTest.*'
```

Expected: build fails because coordinator does not exist.

- [ ] **Step 3: Implement coordinator and replace lambdas**

Move only the action sequence out of `WorkerOCServer::InitCoordinationBackend()`:

- local isolation: mark `LOCAL_ISOLATED`, close admission, keep process alive;
- local recovery: mark `RECOVERING`, keep admission closed, run ownership reconciliation, request recovery reconciliation,
  build generation-aware evidence, complete recovery only when evidence is complete.

Do not move topology engine ownership or cluster topology writes into the coordinator.

- [ ] **Step 4: Run focused UT and record time**

Run:

```bash
time ./build/tests/ut/ds_ut --gtest_filter='WorkerIsolationCoordinatorTest.*:WorkerRecoveryControllerTest.*:WorkerRuntimeStateTest.*'
```

Expected: PASS. Record elapsed time and added case count.

- [ ] **Step 5: Commit**

```bash
git add src/datasystem/worker/worker_isolation_coordinator.* \
        src/datasystem/worker/worker_oc_server.* \
        src/datasystem/worker/CMakeLists.txt src/datasystem/worker/BUILD.bazel \
        tests/ut/worker/worker_isolation_coordinator_test.cpp tests/ut/worker/BUILD.bazel
git commit -m "refactor(worker): centralize local isolation recovery actions"
```

## Task 4: ICoordinationBackend Role Boundary Audit

**Files:**

- Modify: `src/datasystem/cluster/coordination_backend/coordination_backend.h` only if contract comments need to state
  callback ownership explicitly.
- Modify: `tests/ut/cluster/coordination_backend_contract_test.cpp` only if a backend-agnostic fake contract test is
  needed.
- Do not modify `src/datasystem/cluster/coordination_backend/etcd_coordination_backend.*`.
- Do not modify `src/datasystem/common/kvstore/etcd/etcd_store.*`.

**Interfaces:**

- Consumes: `ICoordinationBackend::ShutdownEventSources()`, `ICoordinationBackend::Shutdown()`,
  `ICoordinationBackend::SetEventHandler()`, `ICoordinationBackend::InitKeepAlive()`, and worker-owned local isolation
  callbacks registered outside the topology controller path.
- Produces:
  - written contract statement: member/backend ownership of keepalive local-isolation callbacks is separate from
    controller topology watch ownership;
  - worker self-healing code must not clear or mutate callbacks by reaching into backend implementation classes;
  - backend implementation fixes, if required, are split into a separate PR owned by the coordination backend module.

- [ ] **Step 1: Audit current `ICoordinationBackend` usage**

Run:

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/worker-self-healing-main-20260716
rg -n "ICoordinationBackend|ShutdownEventSources|InitKeepAlive|SetEventHandler|SetLocalIsolationHandler|SetLocalRecoveryHandler" \
  src/datasystem/cluster src/datasystem/worker src/datasystem/common/kvstore/etcd tests/ut/cluster tests/ut/worker
```

Expected: a short evidence list showing which module owns member keepalive callbacks and which module owns controller
watch callbacks.

- [ ] **Step 2: Update contract wording or fake contract test**

If source comments are enough, do not change DataSystem source. If contract wording is ambiguous, add this wording to
`coordination_backend.h` near `ShutdownEventSources()`:

```cpp
/**
 * @brief Idempotently stop asynchronous event sources owned by this backend instance.
 *
 * A controller/topology backend must not clear worker/member keepalive local-isolation callbacks that it does not own.
 * Worker self-healing must interact through the backend contract or worker-owned callbacks, not backend internals.
 */
virtual Status ShutdownEventSources() = 0;
```

If a test is needed, write it against a fake `ICoordinationBackend` in `coordination_backend_contract_test.cpp` that
tracks shutdown ownership at the interface level. The fake must not include or instantiate `EtcdStore`.

- [ ] **Step 3: Record backend implementation risk as follow-up**

Update `cluster-boundary-review-20260720.md` CB-03 with:

- current Plan A does not directly modify ETCD backend or kvstore internals;
- the required boundary is `ICoordinationBackend` role ownership;
- any ETCD-specific callback lifecycle fix must be a separate coordination-backend change if the interface audit proves
  current implementation violates the contract.

- [ ] **Step 4: Run focused cluster contract UT only if changed**

Run:

```bash
time ./build/tests/ut/cluster_topology_contract_ut --gtest_filter='*CoordinationBackend*:*TopologyRuntimeComposition*'
```

Expected: PASS if `coordination_backend.h` or contract tests changed. If only RFC docs changed, record "not run,
documentation-only".

- [ ] **Step 5: Commit**

```bash
git add src/datasystem/cluster/coordination_backend/coordination_backend.h \
        tests/ut/cluster/coordination_backend_contract_test.cpp
git commit -m "docs(cluster): clarify coordination backend callback ownership"
```

If no DataSystem source/test file changed, skip this DataSystem commit and commit only the workbench RFC update.

## Task 5: Admission Facade and Hot-Path Linearization

**Files:**

- Create: `src/datasystem/worker/worker_admission_facade.h`
- Create: `src/datasystem/worker/worker_admission_facade.cpp`
- Create: `tests/ut/worker/worker_admission_facade_test.cpp`
- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`
- Modify: `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp`
- Modify: `src/datasystem/worker/worker_service_impl.cpp`
- Modify: `src/datasystem/worker/CMakeLists.txt`
- Modify: `src/datasystem/worker/BUILD.bazel`
- Modify: `tests/ut/worker/BUILD.bazel`

**Interfaces:**

- Consumes: `WorkerRuntimeStateManager` and `WorkerServiceAdmission`.
- Produces:
  - `Status CheckNormalRead(const std::string &operation) const;`
  - `Status CheckNormalWrite(const std::string &operation) const;`
  - `Status CheckMigrationTarget(const std::string &operation) const;`
  - `Status CheckRecoveryRpc(const std::string &operation) const;`
  - `std::optional<WorkerRuntimeStateReadGuard> TryAcquireNormalGuard(const std::string &operation) const;`

- [x] **Step 1: Write failing facade UT**

Add tests:

```cpp
TEST(WorkerAdmissionFacadeTest, NormalGuardRejectsPendingTransition)
{
    WorkerRuntimeStateManager state;
    WorkerRunningEvidence evidence{ true, true, true, true, true, true };
    ASSERT_TRUE(state.TryMarkRunning(evidence, "ready"));
    WorkerAdmissionFacade facade(state);

    state.MarkLocalIsolated(WorkerIsolationReason::CONTROL_BACKEND_LOCAL_ISOLATION, "local");
    EXPECT_FALSE(facade.TryAcquireNormalGuard("Put").has_value());
    EXPECT_FALSE(facade.CheckRecoveryRpc("RecoverMetadata").IsOk());
}

TEST(WorkerAdmissionFacadeTest, RecoveryRpcAllowedOnlyInRecovering)
{
    WorkerRuntimeStateManager state;
    WorkerAdmissionFacade facade(state);
    state.MarkRecovering(WorkerIsolationReason::CONTROL_BACKEND_LOCAL_ISOLATION, "recovering");
    EXPECT_TRUE(facade.CheckRecoveryRpc("RecoverMetadata").IsOk());
    EXPECT_FALSE(facade.CheckNormalWrite("Put").IsOk());
}
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
time ./build/tests/ut/ds_ut --gtest_filter='WorkerAdmissionFacadeTest.*'
```

Expected: build fails because facade does not exist.

Observed RED evidence:

- Command: `REMOTE_HTTP_PROXY=http://127.0.0.1:17897 REMOTE_HTTPS_PROXY=http://127.0.0.1:17897 JOBS=80 TEST_JOBS=20 bash scripts/clion_remote_build.sh tests-index`
- Result: CMake configure failed because `worker_admission_facade.cpp` did not exist yet.
- Elapsed: 9.3s.

- [x] **Step 3: Implement facade**

Wrap existing `WorkerServiceAdmission`. `TryAcquireNormalGuard()` first acquires `WorkerRuntimeStateReadGuard`, then
runs `WorkerServiceAdmission::Check(snapshot, WorkerAdmissionKind::NORMAL_WRITE, operation)` against the guarded
snapshot. Return empty optional on failure.

- [x] **Step 4: Integrate lowest-risk object hot paths**

Replace scattered snapshot-only checks only where they guard Object and base Worker critical business sections. Keep
operation behavior unchanged and do not modify stream/KV source files in this task.

- [x] **Step 5: Run focused UT and record time**

Run:

```bash
time ./build/tests/ut/ds_ut --gtest_filter='WorkerAdmissionFacadeTest.*:WorkerServiceAdmissionTest.*:WorkerRuntimeStateTest.*'
time ./build/tests/ut/ds_ut_object --gtest_filter='WorkerOCServiceImplTest.*Admission*:WorkerWorkerOCServiceImplTest.*Admission*'
```

Expected: PASS. Record elapsed time and added case count.

Observed GREEN evidence:

- CLion/CMake with URMA mock and remote proxy:
  `REMOTE_HTTP_PROXY=http://127.0.0.1:17897 REMOTE_HTTPS_PROXY=http://127.0.0.1:17897 JOBS=80 TEST_JOBS=20 bash scripts/clion_remote_build.sh tests-index`
  passed; source build 66s, total 180s, `compile_commands.json` entries 1127.
- New facade UT:
  `.clion-remote/worker-self-healing-main-20260716/build/tests/ut/ds_ut --gtest_filter="WorkerAdmissionFacadeTest.*" --gtest_color=no`
  passed 2/2 cases; gtest time 0 ms; wall time 0.05s.
- Runtime/admission focused group:
  `ds_ut --gtest_filter="WorkerAdmissionFacadeTest.*:WorkerServiceAdmissionTest.*:WorkerRuntimeStateTest.*"`
  passed 24/24 cases; gtest time 258 ms; wall time 0.31s.
- Object admission group:
  `ds_ut_object --gtest_filter="*Admission*"`
  passed 5/5 cases; gtest time 121 ms; wall time 0.17s. The initial narrower object filter matched 0 tests and was not counted.
- Bazel 7.4.1 build with URMA mock, distdir, and proxy:
  `bazel-7.4.1 --output_user_root=/home/bazel-output/worker-self-healing-bazel-proxy --batch build --distdir=/home/ds-bazel-distdir --config=debug --config=urma_mock --config=test --jobs=80 //src/datasystem/worker:worker_admission_facade //src/datasystem/worker:worker_service_impl //src/datasystem/worker/object_cache:worker_oc_service_impl //src/datasystem/worker/object_cache:worker_worker_oc_service_impl`
  passed 4 targets; elapsed 1:25.66.
- Bazel 7.4.1 new UT:
  `bazel-7.4.1 --output_user_root=/home/bazel-output/worker-self-healing-bazel-proxy --batch test --distdir=/home/ds-bazel-distdir --config=debug --config=urma_mock --config=test --jobs=80 --test_output=errors //tests/ut/worker:worker_admission_facade_test`
  passed 1 test target; test runtime 0.5s; elapsed 1:12.41.
- Formatting/noise checks:
  `git diff --check` on Task 5 files passed with no output.
  `clang-format-diff` on Task 5 C++ hunks produced no output.
- Mainline freshness:
  `git fetch main master`; `main/master=34bbc3df5`, `HEAD=343e54be4` before Task 5 commit, merge-base `34bbc3df5`,
  so the branch was based on latest `main/master` at this validation point.
- Added new test cases: 2 (`WorkerAdmissionFacadeTest.NormalGuardRejectsPendingTransition`,
  `WorkerAdmissionFacadeTest.RecoveryRpcAllowedOnlyInRecovering`). Additional regression cases re-run: 24 runtime/admission
  cases and 5 object-admission cases.

- [x] **Step 6: Commit**

```bash
git add src/datasystem/worker/worker_admission_facade.* \
        src/datasystem/worker/object_cache/worker_oc_service_impl.cpp \
        src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp \
        src/datasystem/worker/worker_service_impl.cpp \
        src/datasystem/worker/CMakeLists.txt src/datasystem/worker/BUILD.bazel \
        tests/ut/worker/worker_admission_facade_test.cpp tests/ut/worker/BUILD.bazel
git commit -m "refactor(worker): add admission facade for self-healing paths"
```

Observed commit: `14fc34f3d refactor(worker): add admission facade for self-healing paths`.

## Task 6: Scope Stream/KV Admission and Update Acceptance Matrix

**Files:**

- Modify: `rfc/2026-07-15-worker-isolation-self-healing/cluster-boundary-review-20260720.md`
- Modify: `rfc/2026-07-15-worker-isolation-self-healing/scale-fault-overlap-followups.md`
- Do not modify DataSystem stream/KV source files in this task.

**Interfaces:**

- Consumes: `WorkerAdmissionFacade` from Task 5.
- Produces: explicit PR-scope decision in the RFC:
  - `Stream/KV admission deferred from Plan A cohesion refactor`
  - named follow-up cases `CB-06-SC-01`, `CB-06-SC-02`, `CB-06-KV-01`, and `CB-06-KV-02`
  - risk note that PR !1405 must not claim full Stream/KV admission closure until those follow-ups have active tests.

- [x] **Step 1: Audit stream/KV worker-side entry points**

Run:

```bash
rg -n "CheckRuntimeAdmission|WorkerServiceAdmission|RegisterService|Status .*\\(" src/datasystem/worker/stream_cache src/datasystem/worker src/datasystem/common/kvstore
```

Expected: a short list of worker-side stream/KV business entry points and current admission coverage.

Observed audit:

- The first broad `rg` command produced too much `Status` noise and was narrowed to runtime/admission keywords.
- `src/datasystem/worker/stream_cache` client-facing entries include `ClientWorkerSCServiceImpl::Subscribe`,
  `GetDataPage`, `DeleteStream`, `GetLastAppendCursor`, reset/resume, and worker-worker push paths. No
  `WorkerRuntimeStateManager`, `WorkerServiceAdmission`, or `WorkerAdmissionFacade` usage was found in stream_cache.
- There is no separate `src/datasystem/worker/kv_cache` directory in this tree. KV-style ordinary Get/Set acceptance must
  be named against the object-cache/KV-facing API path instead of being counted implicitly.
- Object/migration paths are partially linearized by Task 5; stream/KV source changes are deferred from Plan A.

- [x] **Step 2: Pick one scope decision**

Update `cluster-boundary-review-20260720.md` with these follow-up IDs:

- `CB-06-SC-01`: Stream worker client-facing business RPC rejects normal traffic during `LOCAL_ISOLATED`.
- `CB-06-SC-02`: Stream worker client-facing business RPC rejects normal traffic during `RECOVERING`.
- `CB-06-KV-01`: KV worker-facing normal request rejects during `LOCAL_ISOLATED`.
- `CB-06-KV-02`: KV worker-facing normal request rejects during `RECOVERING`.

- [x] **Step 3: Run docs/self-check**

Run:

```bash
rg -n "CB-06|Stream|KV|admission" /home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-07-15-worker-isolation-self-healing
```

Expected: CB-06 is explicit and no longer ambiguous.

Observed: self-check found `CB-06`, `SF-11`, `SF-12`, Stream, KV, and admission entries across the RFC directory.

- [ ] **Step 4: Commit**

```bash
git -C /home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench add \
        rfc/2026-07-15-worker-isolation-self-healing/cluster-boundary-review-20260720.md \
        rfc/2026-07-15-worker-isolation-self-healing/scale-fault-overlap-followups.md
git -C /home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench commit -m "docs(worker): clarify stream kv self-healing admission scope"
```

## Final Verification

- [ ] **Step 1: Rebase latest main/master**

```bash
git fetch main master
git rebase main/master
```

- [ ] **Step 2: Run focused CMake UT with URMA mock enabled**

Use the existing remote/cached flow:

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench
BUILD_WITH_URMA_MOCK=on BUILD_BACKEND=cmake \
  bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/worker-self-healing-main-20260716 \
  --target ds_ut --gtest_filter='WorkerRecoveryEvidenceTrackerTest.*:WorkerIsolationCoordinatorTest.*:WorkerAdmissionFacadeTest.*:WorkerRecoveryControllerTest.*:WorkerRuntimeStateTest.*:WorkerServiceAdmissionTest.*'
```

Expected: PASS. Record elapsed time and case count.

- [ ] **Step 3: Run object focused CMake UT**

```bash
BUILD_WITH_URMA_MOCK=on BUILD_BACKEND=cmake \
  bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/worker-self-healing-main-20260716 \
  --target ds_ut_object --gtest_filter='WorkerOCServiceImplTest.*RecoveryEvidence*:WorkerOCServiceImplTest.*Admission*:WorkerWorkerOCServiceImplTest.*Admission*'
```

Expected: PASS. Record elapsed time and case count.

- [ ] **Step 4: Run cluster focused CMake UT**

```bash
BUILD_WITH_URMA_MOCK=on BUILD_BACKEND=cmake \
  bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/worker-self-healing-main-20260716 \
  --target cluster_topology_contract_ut --gtest_filter='*CoordinationBackend*:*TopologyRuntimeComposition*'
```

Expected: PASS. Record elapsed time and case count.

- [ ] **Step 5: Run Bazel 7.4.1 focused targets**

```bash
USE_BAZEL_VERSION=7.4.1 BUILD_WITH_URMA_MOCK=on BUILD_BACKEND=bazel \
  bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/worker-self-healing-main-20260716 \
  --bazel-targets='//tests/ut/worker:worker_recovery_evidence_tracker_test,//tests/ut/worker:worker_isolation_coordinator_test,//tests/ut/worker:worker_admission_facade_test,//tests/ut/worker:worker_recovery_controller_test,//tests/ut/worker:worker_service_admission_test'
```

Expected: PASS with Bazel 7.4.1. Record elapsed time.

- [ ] **Step 6: Run formatting/tidy on changed files only**

```bash
git diff --name-only main/master...HEAD -- '*.cpp' '*.h' | xargs -r clang-format -i
git diff --name-only main/master...HEAD -- '*.cpp' '*.h' | xargs -r clang-tidy --quiet
```

Expected: no clang-format-only unrelated files and no new clang-tidy findings in changed files.

- [ ] **Step 7: Prepare PR review bundle**

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/worker-self-healing-main-20260716
.skills/ds-pr-review/scripts/review_pr.py prepare 1405
```

Expected: prepare succeeds with no sensitive-scan warnings. If scan flags real secrets, redact; if it flags a false
positive, narrow the scanner fix and avoid broad bypass.

## Acceptance Summary

This plan closes the cohesion gaps as follows:

- `LocalIsolationDetector` design gap is addressed by `WorkerIsolationCoordinator`, a thin local action coordinator
  instead of a new detector subsystem.
- `WorkerMetadataReconciler` design gap is partially addressed by `WorkerRecoveryEvidenceTracker`; ownership/meta/data
  checks stay in existing OC recovery modules but become generation-aware and auditable.
- HashRing/topology boundary stays unchanged; worker code remains a consumer of `ICoordinationBackend` and topology
  evidence.
- Stream/KV admission is made explicit through Task 6 instead of silently claiming complete coverage.
- Scale/fault overlap remains tracked in `scale-fault-overlap-followups.md`; it is not treated as closed by this
  cohesion refactor.

## Execution Progress Log

### 2026-07-21 Task 1 Recovery Evidence Tracker

- Commit: `437836241 feat(worker): track recovery evidence generation`, pushed to MR !1405 branch
  `feat/worker-self-healing-main-20260716`.
- TDD RED: `JOBS=80 TEST_JOBS=20 bash scripts/clion_remote_build.sh tests-index` failed after 22.4s because
  `datasystem/worker/worker_recovery_evidence_tracker.h` did not exist. This confirmed the new UT was active in the
  CMake `ds_ut` target.
- TDD GREEN build/index: same CLion script passed with URMA mock enabled, third-party cache hit, total use time 139s,
  `compile_commands` entries 1122.
- New cases added: 2 UT cases.
  - `WorkerRecoveryEvidenceTrackerTest.OldGenerationEvidenceIsRejected`
  - `WorkerRecoveryEvidenceTrackerTest.EmptyEvidenceDoesNotCompleteNewRecovery`
- Focused CMake UT command:
  `tests/ut/ds_ut --gtest_filter="WorkerRecoveryEvidenceTrackerTest.*" --gtest_color=no`
  Result: 2/2 passed, wall time 0.06s.
- Bazel focused validation status: not completed. On `tiantiyun-80c128g`, `/usr/local/bin/bazel --version` hung and
  `timeout 20s /usr/local/bin/bazel --version` returned 124. No `bazel-7.4.1` alternate binary was found under
  `/usr/local/bin`, `/usr/bin`, `/opt`, or `/home` within the checked depth. Do not claim Bazel coverage for Task 1
  until the remote Bazel 7.4.1 path is repaired or CI returns the target result.

### 2026-07-21 Runtime/Coordination Boundary Review

Read-only sub-agent review found the current largest coupling points:

- `worker_oc_server.cpp` still directly includes `worker_control_backend_scope.h`, configures ETCD keepalive
  isolation/recovery callbacks on `EtcdStore`, consumes `TopologyEngine` availability directly, and calls
  `ClassifyControlBackendFailureScope` from worker server code.
- `worker_service_impl` exposes `SetRuntimeStateManager(const WorkerRuntimeStateManager *)` and constructs
  `WorkerServiceAdmission` directly.
- `worker/object_cache` still passes `EtcdStore *` and `TopologyEngine *` through `WorkerOCServiceImpl`, uses
  `WorkerServiceAdmission` directly in several RPC paths, and has `NodeSelector` reading runtime snapshots directly.
- `slot_recovery_store.cpp` still uses `EtcdStore` for table/KV/CAS operations and should move to
  `ICoordinationBackend` or a narrower slot recovery coordination store.

Recommended refactor order:

1. Add `RuntimeFacade`/admission facade first and migrate direct `WorkerRuntimeStateManager`/`WorkerServiceAdmission`
   consumers in worker services and object-cache peer/master-worker service paths.
2. Then narrow object-cache topology dependencies behind a worker-local topology/runtime facade.
3. Then convert slot recovery storage to `ICoordinationBackend` or a narrow coordination-store abstraction.
4. Finally move WorkerOCServer keepalive isolation/recovery, control-backend scope classification, and topology
   availability callbacks behind coordination/runtime boundaries.

Mandatory `ICoordinationBackend` boundary items remain:

- Local isolation/recovery/check-backend-state callbacks must be exposed through `ICoordinationBackend`, not directly
  through `EtcdStore`.
- Slot recovery table/KV/CAS operations must not expose `EtcdStore` in object-cache public constructors/signatures.
- Control-backend failure scope classification should become backend/topology observation consumed by worker runtime,
  not a worker server dependency on backend internals.

### 2026-07-21 Task 2 Object Recovery Evidence Generation

- Commit: `1d177baa7 fix(worker): bind object recovery evidence to generation`, pushed to MR !1405 branch
  `feat/worker-self-healing-main-20260716`. Scope is limited to `worker/object_cache` service evidence reporting plus
  build/test deps.
- TDD RED: `JOBS=80 TEST_JOBS=20 bash scripts/clion_remote_build.sh tests-index` failed after 27.5s because
  `WorkerOCServiceImpl` did not yet expose `BeginRecoveryEvidenceGeneration`. This confirmed the new UT was active in
  the CMake `ds_ut_object` target before implementation.
- TDD GREEN build/index: same CLion script passed with URMA mock enabled, third-party cache hit, total use time 216s,
  `compile_commands` entries 1122.
- New cases added: 1 UT case.
  - `WorkerOcServiceImplTest.NewRecoveryGenerationInvalidatesOldCompleteEvidence`
- Focused new-case command:
  `tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.NewRecoveryGenerationInvalidatesOldCompleteEvidence" --gtest_color=no`
  Result: 1/1 passed, wall time 0.05s.
- Focused recovery-evidence group command:
  `tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.*RecoveryEvidence*:WorkerOcServiceImplTest.NewRecoveryGenerationInvalidatesOldCompleteEvidence" --gtest_color=no`
  Result: 3/3 passed, wall time 0.05s.
- `git diff --check` passed locally.
- Bazel focused validation status: not completed for this task yet. Previous remote Bazel 7.4.1 lookup remained blocked
  by `/usr/local/bin/bazel --version` hanging; rerun Bazel once the remote Bazel 7.4.1 path is available or CI returns
  target results.

### 2026-07-21 Task 3 Worker Isolation Coordinator

- Commit: `0617e49fb refactor(worker): coordinate local isolation recovery actions`, pushed to MR !1405 branch
  `feat/worker-self-healing-main-20260716`. Scope is limited to adding `WorkerIsolationCoordinator`, moving the local
  isolation/recovery action sequence out of `WorkerOCServer::InitCoordinationBackend()`, and adding focused UT/build
  entries.
- Correct-base check: `git rebase main/master` reported the branch was up to date. An earlier attempt against
  `origin/master` was aborted because that ref was older and tried to replay 676 historical commits.
- TDD RED: `JOBS=80 TEST_JOBS=20 bash scripts/clion_remote_build.sh tests-index` failed in the new
  `WorkerIsolationCoordinatorTest` path after the test was added, first on missing/default hook initialization. This
  confirmed the new UT was in the default CMake `ds_ut` build path.
- TDD GREEN build/index: same CLion script passed with URMA mock enabled, third-party cache hit, total use time 169s,
  `compile_commands` entries 1124.
- New cases added: 2 UT cases.
  - `WorkerIsolationCoordinatorTest.LocalIsolationClosesAdmissionAndKeepsProcessAlive`
  - `WorkerIsolationCoordinatorTest.LocalRecoveryStartsRecoveringBeforeTopologyReconciliation`
- Focused new-case command:
  `tests/ut/ds_ut --gtest_filter="WorkerIsolationCoordinatorTest.*" --gtest_color=no`
  Result: 2/2 passed, wall time 0.06s.
- Focused runtime/recovery/admission group command:
  `tests/ut/ds_ut --gtest_filter="WorkerIsolationCoordinatorTest.*:WorkerRecoveryControllerTest.*:WorkerRuntimeStateTest.*:WorkerServiceAdmissionTest.*:WorkerTopologyAvailabilityAdmissionTest.*" --gtest_color=no`
  Result: 38/38 passed, wall time 0.32s.
- ETCD keepalive ST command:
  `tests/st/ds_st --gtest_filter="EtcdStoreTest.TestKeepAliveFailedDueToNetworkerFailure:EtcdStoreTest.TestKeepAliveGlobalEtcdFailureDoesNotReportLocalIsolation" --gtest_color=no`
  Result: 2/2 passed, wall time 18.03s.
- Worker keepalive/object-cache ST command:
  `tests/st/ds_st_object_cache --gtest_filter="WorkerPushMetaTest.LEVEL1_TestGlobalBackendOutageDoesNotSelfIsolateWorkers:WorkerPushMetaTest.LEVEL1_TestKeepAliveLocalIsolationRecoversThroughEvidenceGate" --gtest_color=no`
  Result: 2/2 passed, wall time 40.30s.
- `git diff --check` and changed-file `clang-format --dry-run --Werror` passed locally after formatting only changed
  hunks.
- Bazel focused validation status: not completed for this task yet because the remote Bazel 7.4.1 path is still not
  repaired in the local Tiantiyun shell.

### 2026-07-21 Task 4 ICoordinationBackend Local Callback Boundary

- Scope: moved worker local isolation/recovery callback registration behind `ICoordinationBackend` instead of direct
  `EtcdStore` callback registration from worker code. `EtcdStore` callback calls now remain concrete-backend internals
  inside `EtcdCoordinationBackend`; coordinator-service mode maps keepalive local-isolation/recovery events through
  `DsCoordinationBackend`.
- Boundary result: worker code no longer calls `etcdStore_->SetLocalIsolationHandler` or
  `etcdStore_->SetLocalRecoveryHandler`. `WorkerOCServer` registers callbacks on `TopologyEngine::Builder`, which
  installs them on the member-role `ICoordinationBackend`.
- TDD RED: `JOBS=80 TEST_JOBS=20 bash scripts/clion_remote_build.sh tests-index` failed after about 1 minute because
  the new contract test expected `ICoordinationBackend::SetLocalIsolationHandler` and
  `ICoordinationBackend::SetLocalRecoveryHandler`, but the interface did not yet expose them.
- TDD GREEN build/index: same CLion script passed with URMA mock enabled, third-party cache hit, total use time 121s,
  `compile_commands` entries 1125. CLion CompDB indexing is Ready at
  `.clion-remote/worker-self-healing-main-20260716/build/compile_commands.json`.
- New cases added: 1 UT case.
  - `CoordinationBackendContractTest.ShutdownEventSourcesDoesNotClearLocalMembershipCallbacks`
- Focused new-case command:
  `cluster_topology_contract_ut --gtest_filter="CoordinationBackendContractTest.ShutdownEventSourcesDoesNotClearLocalMembershipCallbacks" --gtest_color=no`
  Result: 1/1 passed, GoogleTest time 0 ms, command wall time 0.04s.
- Focused cluster boundary group command:
  `cluster_topology_contract_ut --gtest_filter="*CoordinationBackend*:*TopologyRuntimeComposition*" --gtest_color=no`
  Result: 18/18 passed, GoogleTest time 1 ms, command wall time 0.04s.
- Bazel 7.4.1 root cause/fix: `/usr/local/bin/bazel` on the remote is a Bazelisk-style launcher and can hang on
  downloads. Copied/used `/usr/local/bin/bazel-7.4.1`, exposed the local proxy to the remote with SSH reverse tunnel
  `127.0.0.1:17897`, and used `/home/ds-bazel-distdir` for cached external archives.
- Bazel focused build command:
  `bazel-7.4.1 build --distdir=/home/ds-bazel-distdir --config=debug --config=urma_mock --config=test --jobs=80 //src/datasystem/cluster/coordination_backend:coordination_backend //src/datasystem/cluster:cluster_topology //src/datasystem/worker:datasystem_worker_shared`
  Result: 3/3 targets built, wall time 1:35.18 after syncing the local BUILD.bazel dependency fix to the remote.
- Bazel focused test command:
  `bazel-7.4.1 test --distdir=/home/ds-bazel-distdir --config=debug --config=urma_mock --config=test --jobs=80 --test_output=errors //tests/ut/worker:worker_isolation_coordinator_test`
  Result: 1/1 test target passed, test runtime 0.5s, command wall time 1:29.04.
- Formatting/static checks:
  - `git diff --check` passed.
  - `clang-format-diff` over modified hunks produced no remaining diff.
  - Full-file `clang-format --dry-run --Werror` still reports pre-existing formatting issues in touched files, so full
    file formatting was intentionally not applied to avoid format-only noise.
  - `clang-tidy` completed on `ds_coordination_backend.cpp`, `etcd_coordination_backend.cpp`, and
    `topology_engine.cpp` with only existing/non-blocking warnings after suppressing compile-command linker flag and
    `std::result_of` infrastructure noise. `worker_oc_server.cpp` generated warnings but did not exit after more than
    5 minutes and was interrupted; do not count it as a completed tidy check yet.
- Remaining gap after Task 4: branch is currently behind latest `main/master` by 2 commits. Rebase latest main/master
  and rerun focused CMake/Bazel checks before committing/pushing this task.

### 2026-07-21 Task 4 Post-Rebase Validation And Proxy Build Support

- Rebase status: fetched latest `main/master` and rebased `feat/worker-self-healing-main-20260716` successfully. No
  conflict remained after the rebase.
- Commit after rebase:
  - `13e5615de refactor(cluster): route isolation callbacks through backend`
  - `343e54be4 build: support remote proxy for clion build`
- CLion remote build/index command:
  `REMOTE_HTTP_PROXY=http://127.0.0.1:17897 REMOTE_HTTPS_PROXY=http://127.0.0.1:17897 JOBS=80 TEST_JOBS=20 bash scripts/clion_remote_build.sh tests-index`
  Result: passed with URMA mock enabled, build source 525s, total 619s, `compile_commands` entries 1125. CLion CompDB
  project is ready at `.clion-remote/worker-self-healing-main-20260716/build/compile_commands.json`.
- Third-party cache note: latest `main/master` upgraded brpc/braft cache keys, so the first CMake remote run rebuilt
  and installed `brpc_a1b1f1f1...` and `braft_66330b...` into `/home/ds-thirdparty-cache`. The remote proxy tunnel
  `127.0.0.1:17897` was used to avoid direct GitHub HTTP/2 failures; later runs should hit the cache.
- New cases added in Task 4: 1 UT case.
  - `CoordinationBackendContractTest.ShutdownEventSourcesDoesNotClearLocalMembershipCallbacks`
- Focused new-case command:
  `cluster_topology_contract_ut --gtest_filter="CoordinationBackendContractTest.ShutdownEventSourcesDoesNotClearLocalMembershipCallbacks" --gtest_color=no`
  Result: 1/1 passed, GoogleTest time 0 ms, command wall time 0.03s.
- Focused cluster boundary group command:
  `cluster_topology_contract_ut --gtest_filter="*CoordinationBackend*:*TopologyRuntimeComposition*" --gtest_color=no`
  Result: 18/18 passed, GoogleTest time 2 ms, command wall time 0.03s.
- Bazel 7.4.1 focused build command:
  `bazel-7.4.1 build --distdir=/home/ds-bazel-distdir --config=debug --config=urma_mock --config=test --jobs=80 //src/datasystem/cluster/coordination_backend:coordination_backend //src/datasystem/cluster:cluster_topology //src/datasystem/worker:datasystem_worker_shared`
  Result: 3/3 targets built, command wall time 1:17.58. The command used remote proxy environment variables.
- Bazel 7.4.1 focused test command:
  `bazel-7.4.1 test --distdir=/home/ds-bazel-distdir --config=debug --config=urma_mock --config=test --jobs=80 --test_output=errors //tests/ut/worker:worker_isolation_coordinator_test`
  Result: 1/1 test target passed from Bazel cache, test runtime 0.5s, command wall time 1:14.63. The command used
  remote proxy environment variables.
- Local checks:
  - `bash -n scripts/clion_remote_build.sh` passed.
  - `git diff --check` passed.
- Scope control: the proxy support is limited to `scripts/clion_remote_build.sh` environment propagation and does not
  change the default remote build behavior when proxy variables are unset.
