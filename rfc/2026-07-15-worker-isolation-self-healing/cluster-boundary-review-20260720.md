# Worker Self-Healing Cluster Boundary Review Plan

Date: 2026-07-20

Scope: PR !1405 `feat/worker-self-healing-main-20260716`, story design
`ds-worker-isolation-self-healing-20260716.html#design`, local topology design docs, and current source review.

Method: SDD review of the story/design boundary, CodeGraph symbol/call relationship check, source inspection, and
`ds-pr-review` inline comments for high-confidence line findings.

## Overall Judgment

The implementation direction is mostly correct: worker self-healing does not directly write `ClusterTopologyPb` or
bypass `TopologyController` for topology commits. Runtime callers of `CompareAndSwapTopology()` remain concentrated in
`TopologyController::EnsureTopologyAuthority` and `TopologyController::CommitAndReadBack`; the worker self-healing path
does not directly call that topology CAS path.

The correct cluster boundary is `ICoordinationBackend` plus the existing topology controller/repository path. Worker
local isolation may close only this worker's business admission and may consume cluster, ring, membership, metadata, and
slot evidence. It must not directly rewrite the cluster node table, hash ring, topology stamp, or master metadata to
make itself visible again.

However, the current PR is not yet boundary-clean enough to call the design fully closed. The main remaining risk is
not "worker directly mutates topology"; it is "worker becomes visible or keeps serving before the cluster-owned evidence
is complete, fresh, and role-isolated."

## Boundary Invariants

| ID | Invariant | Current Judgment | Plan Action |
| --- | --- | --- | --- |
| CB-I01 | Worker local isolation can only narrow local service admission; it cannot directly change cluster topology authority. | Mostly satisfied. No direct self-healing topology CAS path was found. | Keep this as a regression check during review and rebase. |
| CB-I02 | `READY`, `ACTIVE`, and `RUNNING` are separate views; recovery must finish before a worker is visible for normal service. | Gap. Current recovery callback can call `MarkReady()` before recovery evidence is complete. | Fix CB-01 before merge. |
| CB-I03 | Recovery evidence must be fresh for the current isolation/recovery generation. | Gap. Old or empty metadata evidence can be reused across recovery cycles. | Fix CB-02 before merge. |
| CB-I04 | Worker-role and controller-role coordination backends must not share lifecycle that can clear each other's event callbacks. | Gap. ETCD mode currently shares the Worker-owned `EtcdStore` between member and controller backend paths. | Fix CB-03 before merge or explicitly split into a blocking follow-up. |
| CB-I05 | Unknown or inconclusive backend evidence must fail closed for this worker's normal admission. | Gap. `INCONCLUSIVE` does not clearly drive local admission closure. | Fix CB-04 before merge or record an explicit staged risk decision. |
| CB-I06 | Normal object, stream, KV, migration, and recovery RPC admission must all be covered consistently. | Partial. Object/migration coverage is stronger than stream/KV; some hot paths still need linearization. | Fix CB-05/CB-06 or mark scope limits in PR. |
| CB-I07 | Scale-in/scale-out overlap with fault/recovery must be explicit acceptance coverage, not implied by separate tests. | Open. Base self-healing coverage exists; overlap matrix remains incomplete. | Track in `scale-fault-overlap-followups.md`. |

## Refactoring Strategy

The accepted implementation direction is Plan A: thin abstractions and gradual cohesion.

The goal is not to rename the current code until it looks exactly like the original story design. The goal is to make
the current PR easier to audit while preserving the existing topology, metadata recovery, slot recovery, stream, and KV
flows.

Planned thin abstractions:

- `WorkerRecoveryEvidenceTracker`: owns recovery generation and freshness. Existing metadata/slot/resource/ownership
  evidence producers remain where they are, but stale evidence can no longer complete a new recovery cycle.
- `WorkerIsolationCoordinator`: owns the local isolation/recovery action sequence currently embedded in
  `WorkerOCServer` lambdas. It may close local admission and drive recovery callbacks, but it must not write topology or
  master metadata.
- `WorkerAdmissionFacade`: wraps `WorkerServiceAdmission` with named operations and read-guard acquisition so hot paths
  can use the same admission vocabulary without duplicating mode checks.

Implementation plan: `refactor-plan-20260720.md`.

## Required Plan Items

### CB-01: Delay READY Until Recovery Evidence Completes

Problem: `WorkerOCServer::InitCoordinationBackend()` currently marks runtime as recovering, closes topology serving
admission, and then can publish `READY` membership before `metadata/slot/ownership/resource` evidence has completed.

Required behavior:

- Keep membership in a recovering or non-ready state while local recovery evidence is incomplete.
- Run `ReconcileNetworkRecoveryOwnership()` and `RequestRecoveryReconciliation(...)` before normal visibility.
- Only publish `READY` and reopen normal service admission after the recovery controller has matching-generation
  evidence that membership, metadata, slot, and ownership checks are complete.

Acceptance cases:

- Pause recovery at `WorkerRecoveryController.BeforeMarkRunning`; verify member lifecycle is not `READY` and normal
  object service admission stays closed.
- Release recovery; verify `READY` is published once, admission opens once, and data remains readable from the correct
  owner.

### CB-02: Bind Recovery Evidence to an Isolation Generation

Problem: `WorkerOCServiceImpl` builds recovery evidence from last reports and slot/resource snapshots, but entering
`LOCAL_ISOLATED` or `RECOVERING` does not invalidate old metadata/slot/ownership evidence or bind reports to a new
generation.

Required behavior:

- Add an isolation/recovery generation or timestamp owned by the runtime/recovery controller.
- Invalidate previous metadata, slot, ownership, and resource evidence on `MarkLocalIsolated()` or recovery start.
- Accept only evidence that matches the active recovery generation.

Acceptance cases:

- Seed old complete metadata evidence, trigger a new isolation/recovery cycle, and verify admission cannot reopen until
  new-generation reconciliation finishes.
- Seed empty default evidence, trigger recovery, and verify empty evidence is not interpreted as this cycle's success.

### CB-03: Separate Worker and Controller Coordination Backend Lifecycles

Problem: ETCD mode currently creates member and controller coordination backends from the same Worker-owned `EtcdStore`.
`EtcdCoordinationBackend::ShutdownEventSources()` clears local isolation/recovery handlers, so controller runtime
shutdown can remove Worker self-healing callbacks.

Required behavior:

- Use distinct `ICoordinationBackend`/`EtcdStore` instances or explicit role-scoped callback ownership for Worker
  membership and Controller topology event sources.
- Only the Worker/member backend may own keepalive local-isolation and local-recovery callbacks.
- Controller backend shutdown must not clear Worker keepalive callbacks or change Worker business admission.

Acceptance cases:

- Start Worker and Controller roles, register local isolation/recovery handlers, stop only the controller runtime, and
  verify Worker keepalive callbacks remain installed.
- Trigger local isolation after controller shutdown and verify admission still closes through the Worker path.

### CB-04: Fail Closed on Inconclusive Backend Scope

Problem: backend-scope classification can return `INCONCLUSIVE` when peer evidence is missing, stale, mismatched, or
partially unavailable. The current keepalive path only treats `LOCAL_ISOLATION` as a positive self-isolation result.

Required behavior:

- When this worker's local backend is unavailable and peer evidence is inconclusive, close normal local admission or
  enter a conservative local-isolation-compatible state.
- Preserve partial successful peer observations instead of overwriting them with later empty probe batches.
- Emit diagnostics that distinguish true global outage, local isolation, and inconclusive fail-closed cases.

Acceptance cases:

- Peer A reports backend available while Peer B probe times out; verify local admission closes and the successful
  observation is retained.
- Local topology stamp lags while a peer reports newer available backend evidence; verify the worker does not keep
  serving ordinary traffic.

### CB-05: Linearize Object Hot-Path Admission

Problem: some object service paths validate `WorkerServiceAdmission::Check()` as a snapshot but do not hold an admission
read guard across the critical section.

Required behavior:

- Use `TryAcquireReadGuard()` or an equivalent epoch guard for normal read/write/migration target paths that depend on
  worker service mode.
- Keep recovery RPC paths separate and explicitly allowed only when `CanServeRecoveryRpc()` is true.

Acceptance cases:

- Inject a transition from `RUNNING` to `LOCAL_ISOLATED` after validation but before the operation body; verify the
  operation is rejected or fenced.
- Verify recovery RPC remains allowed during `RECOVERING` when ordinary object traffic is rejected.

### CB-06: Complete Stream and KV Admission Coverage Decision

Problem: the story design expects Object/Stream/KV/migration admission coverage, but current implementation emphasis is
Object and migration. Stream/KV paths need either implementation closure or an explicit PR-scope decision.

Required behavior:

- In Plan A, keep stream and KV source changes out of the cohesion refactor to avoid expanding the PR blast radius.
- Record stream and KV admission as explicit follow-up acceptance cases, and do not claim full Stream/KV closure in
  PR !1405 until those cases have active tests.

Acceptance cases:

- `CB-06-SC-01`: Stream worker client-facing business RPC rejects normal traffic during `LOCAL_ISOLATED`.
- `CB-06-SC-02`: Stream worker client-facing business RPC rejects normal traffic during `RECOVERING`.
- `CB-06-KV-01`: KV worker-facing normal request rejects during `LOCAL_ISOLATED`.
- `CB-06-KV-02`: KV worker-facing normal request rejects during `RECOVERING`.

### CB-07: Close Scale/Fault Overlap Matrix

Problem: scale-in/scale-out and recovery overlap scenarios are not proven by base local-isolation tests.

Required behavior:

- Treat the SF-01 through SF-10 matrix as separate acceptance coverage.
- Do not count disabled tests or separate non-overlap tests as completion for overlap cases.
- Keep every new overlap case bounded in runtime and report its observed execution time.

Acceptance cases:

- See `scale-fault-overlap-followups.md` for the current closure order and required ST/UT evidence.

## Inline Review Comments Already Published

| Priority | File | Finding |
| --- | --- | --- |
| P1 | `src/datasystem/worker/worker_oc_server.cpp` | Recovery evidence should complete before publishing `READY` membership. |
| P1 | `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp` | Old metadata recovery evidence may let a new recovery cycle reopen too early. |
| P1 | `src/datasystem/cluster/coordination_backend/etcd_coordination_backend.cpp` | Controller backend shutdown can clear Worker keepalive self-healing callbacks. |

## Completion Gate

This boundary plan is complete for PR !1405 only when:

- CB-01 and CB-02 have active UT/ST coverage and passing evidence.
- CB-03 is fixed or explicitly accepted as a blocking follow-up with a named owner and risk note.
- CB-04 is fixed or explicitly accepted as a staged fail-closed policy decision.
- CB-05 and CB-06 either have active admission coverage or are clearly scoped out of the PR with follow-up cases.
- CB-07 has an active acceptance matrix update with per-case test names and execution time.
