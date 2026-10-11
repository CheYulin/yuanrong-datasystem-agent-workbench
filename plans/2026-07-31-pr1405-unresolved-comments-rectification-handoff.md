# PR1405 Unresolved Review Comments Rectification Handoff

> **For agentic workers:** This is a handoff for the PR1405 central control thread. Use the existing PR1405 source branch and worktree discipline. After each confirmed fix, reply to the matching GitCode discussion and mark it resolved with `ds-pr-comment-proc`; do not leave fixed issues open.

## Objective

Close the currently unresolved PR1405 review comments by first reducing accidental scope and then fixing correctness, reliability, availability, and performance blockers. The goal is a reviewable worker-isolation self-healing PR, not a larger mixed-purpose branch.

## Hard Constraints

- PR: `https://gitcode.com/openeuler/yuanrong-datasystem/pull/1405`
- Latest observed fork/source head: `5fc48fea76614758481fa336858329aaa493c9ea`
- Latest observed target/master: `c23f3a5d2bcbdb62270ab91d5e4000fd42777c42`
- Never push to `openeuler/yuanrong-datasystem` or remote `main`.
- Before any authorized push, run `git remote -v`; only a verified `yche` / `yche-huawei` fork branch is allowed.
- Do not issue `/retest`, remove WIP, or change PR state unless explicitly authorized after self-verification.
- Do not close GitCode discussions before the code fix and local verification evidence exist.
- Replies must be sanitized: no private host, IP, port, username, local path, remote log path, token, AK/SK, or raw private payload.
- Use three-dot PR semantics against the target branch. Do not use endpoint diff as the file-count authority.

## Current Comment State

Fetched with:

```bash
python3 .skills/ds-pr-comment-proc/scripts/pr_comment_proc.py list --pr 1405 --unresolved --with-replies --json
```

Current unresolved count: **87 discussions**.

Important caveat: the local review worktree had `bc320f89f4ff8f51d1dde87676fe2f6b67ed4d73`, but the PR source branch had advanced to `5fc48fea76614758481fa336858329aaa493c9ea`. Re-verify from `refs/tmp/pr1405-latest-head` or a fresh exact-head worktree before editing.

Latest observed head delta from `bc320f89` to `5fc48fea`:

- 67 changed files
- about `+9141/-1783`
- mostly coordinator election / raft / runtime documentation and tests

This is a major scope signal. Confirm whether those coordinator-election changes belong in PR1405. If not, remove them first; otherwise many "non-essential change" and "unclear responsibility" comments will remain valid.

## Triage Summary

| Bucket | Count | Recommendation |
| --- | ---: | --- |
| Concrete severe / P1 defects | 26 | Must fix before considering comments resolved |
| Test / build acceptance gaps | 5 | Fix after or alongside related code changes |
| Slimming / non-essential / formatting / small files | 21 | Close through scope cleanup and minimal-diff rewrite |
| Responsibility / module boundary | 6 | Needs architecture-aligned cleanup, not cosmetic replies |
| Timeout / parameter / lifecycle | 6 | Mostly quick to medium fixes; treat as reliability blockers |
| Clarify intent | 13 | Either remove the change or reply with evidence-backed necessity |
| Already replied but still open | 4 | Re-verify; only resolve if code actually matches the reply |
| Other | 10 | Mix of copyright, logs, ObjectTable, comments |

## P0: Scope And Low-Risk Fixes

Do first. These are cheap and reduce review noise without altering the core self-healing design.

1. Confirm whether coordinator election / raft changes belong in PR1405.
   - Latest head added a large coordinator-election slice after `bc320f89`.
   - If unrelated to worker isolation, remove it from the PR source branch.
   - Do not resolve scope comments until this is settled.

2. Restore busy-heal rate limiter timing semantics.
   - Comment: `182555198`
   - Current latest-head evidence:
     - `src/datasystem/worker/object_cache/data_migrator/handler/migrate_data_handler.h:44`
     - `BUSY_HEAL_INITIAL_SLEEP_MS = 100`
     - `BUSY_HEAL_MAX_SLEEP_MS = 800`
   - Fix direction: restore the previous 1-second sliding-window-safe values and explanatory comment, expected `200ms` initial and `1000ms` max unless exact master says otherwise.
   - Verification: focused test or deterministic unit path proving the third probe lands after the 1-second limiter window.

3. Stop using failure-detection timeout as recovery RPC deadline.
   - Comment: `182555200`
   - Current latest-head evidence:
     - `src/datasystem/master/object_cache/master_oc_service_impl.cpp:797`
     - `timeoutDuration.Init(static_cast<int64_t>(FLAGS_node_dead_timeout_s) * 1000)`
   - Fix direction: preserve caller/request deadline and use a distinct bounded recovery budget. Effective deadline should not be extended by `node_dead_timeout_s`.
   - Verification: test where caller deadline is shorter than `node_dead_timeout_s`; assert child operation uses the shorter deadline.

4. Restore CMake direct proto dependency consistency.
   - Comment: `182522894`
   - Fix direction: if `share_memory.proto` still imports `meta_transport.proto`, restore the CMake direct deps so CMake and Bazel semantics do not diverge.
   - Verification: CMake configure/build target that owns `share_memory_protos`, plus Bazel package load if practical.

5. Fix obvious hygiene comments.
   - Comments include: `182503498`, `182501962`, `182447389`
   - Fix direction: add missing Apache copyright, restore useful log deletion if it was accidental, revert files that were touched by mistake.
   - Verification: diff inspection plus `git diff --check`.

6. Restore `zero timeout` first-observation failure semantics or explicitly tighten API.
   - Comment: `182522931`
   - Fix direction: preserve old `timeout == 0` behavior unless the API contract is intentionally changed with tests and documentation.
   - Verification: restore or add `ZeroTimeoutConfirmsOnFirstSuccessfulAbsenceObservation`.

7. Fix centralized local-master device metadata cleanup.
   - Comment: `182522797`
   - Current latest-head evidence:
     - `WorkerTopologyMetadataActions::CleanupDeviceMetadata()` returns immediately on `centralizedMetadata_`.
   - Fix direction: use `const bool localMetadata = !centralizedMetadata_ || localMetadataMaster_`, consistent with nearby recovery/cleanup methods.
   - Verification: focused UT for centralized metadata + local metadata master.

## P1: Admission Correctness

This is core worker-isolation semantics. Do not close these with explanation-only replies.

1. Make `MIGRATION_TARGET` admission linearized.
   - Comment: `181414271`
   - Current latest-head evidence still has `CheckAdmission(MIGRATION_TARGET)` at:
     - `worker_worker_oc_service_impl.cpp:974`
     - `worker_worker_oc_service_impl.cpp:987`
     - `worker_worker_oc_service_impl.cpp:1337`
     - `worker_oc_service_migrate_impl.cpp:154`
     - `worker_oc_service_migrate_impl.cpp:179`
     - `master_worker_oc_service_impl.cpp:163`
     - `master_worker_oc_service_impl.cpp:180`
     - `master_worker_oc_service_impl.cpp:401`
   - Fix direction: use a guard or equivalent generation/epoch ticket for migration critical sections. Avoid holding a blocking read lock across long async work unless the async result semantics are explicitly designed.
   - Verification: inject isolation after admission check and before operation commit; assert post-isolation migration writes do not land.

2. Fix `AcquireAdmissionGuard` hot-path cost.
   - Comment: `181414282`
   - Current latest-head evidence: `AcquireAdmissionGuard()` still calls `AcquireAdmissionReadGuard()` before a true fast guard path.
   - Fix direction: ordinary RUNNING non-complete-reconciliation operations should hit lock-free fast path before trying `TryAcquireReadGuard()`. Only operations that require linearized transition coverage should take the heavier guard.
   - Verification: focused UT plus simple micro/loop evidence that RUNNING normal read/write returns without constructing the heavy state guard.

3. Fix `PublishMetaAsync` false success.
   - Comment: `182498336`
   - Fix direction: if async execution rechecks admission and fails, the caller must observe failure or the operation must not have been accepted as complete. Do not hide second-stage rejection.
   - Verification: inject admission rejection in worker-pool execution and assert caller/result records failure.

4. Add runtime admission to P2P master-forwarding write entry.
   - Comment: `182498342`
   - Fix direction: forwarded write paths must not bypass local runtime admission.
   - Verification: isolated/recovering worker rejects P2P forwarded write consistently with direct write.

## P1: Recovery Correctness And Generation Fencing

1. Do not execute external side effects under generation lock.
   - Comment: `182498358`
   - Current latest-head evidence:
     - `ObjectCacheRecoveryState::PublishRecoveryCompletionIfCurrent()` calls `publish()` while holding `recoveryEvidenceGenerationMutex_`.
   - Fix direction: validate generation under lock, copy/mark needed state, release lock, then publish. Revalidate if needed before final state commit.
   - Verification: concurrency UT with blocking publish callback; recovery state operations must not deadlock or block unrelated generation checks.

2. Fix stale evidence in-flight handling.
   - Comment: `182522763`
   - Current latest-head evidence:
     - stale generation path can return before clearing `objectCacheRecoveryRequestInFlight_`.
   - Fix direction: bind in-flight to generation or clear it when the stale callback cannot correspond to the current active fanout.
   - Verification: stale terminal callback followed by fresh recovery request must schedule successfully.

3. Preserve membership recovery address.
   - Comment: `182522749`
   - Current latest-head evidence:
     - `WorkerOCServer::HandleMembershipRecovery()` still has `(void)address`.
   - Fix direction: only run local object-cache network recovery when `address == localAddress_`, or pass the recovered peer address/timestamp through the recovery request.
   - Verification: peer RECOVERING event must not trigger local worker recovery evidence.

4. Fix failed recovery being treated as consumed.
   - Comments: `182504189`, `182522659`, `182522700`, `182522721`
   - Fix direction: PushMeta batch failures, deferred retry enqueue, and recovered ACK persistence must all feed an incomplete/failed evidence state until durably finished.
   - Verification: inject partial batch failure and deferred retry; runtime must not report Complete until retry and persistent ACK succeed.

5. Protect data correctness in migration and cleanup.
   - Comments: `182504181`, `182504202`, `182504207`, `182498353`
   - Fix direction:
     - remote NX conflicts must not be registered as ACK locations;
     - `NotifyRemoteGet` must not make source release keys that were not actually migrated;
     - version-check cleanup must not dereference null `SafeObject`;
     - failed primary must not become executor without data evidence.
   - Verification: focused UTs for each injected failure path.

## P2: Responsibility And Slimming

These items control reviewability and long-term maintainability. They can follow the P1 correctness fixes, but should be addressed before final ready signal.

1. Remove worker-side coordination backend flying wires.
   - Comments: `182317260`, `182439693`
   - Current evidence includes:
     - `worker_coordination_backend_factory`
     - `WorkerOCServer::metadataCoordinationBackend_`
     - `ObjectMetadataCoordinationReader`
     - `CentralMetadataAddressResolver`
   - Fix direction: TopologyEngine / cluster layer should own topology and backend facts. Worker should consume topology/recovery events or a narrow metadata reader capability, not construct backend adapters directly.
   - Short-term acceptable step: if full ownership move is too large, document the remaining gap and do not claim the comments are resolved.

2. Inline control backend scope classifier into TopologyEngine private scope.
   - Comment: `182540005`
   - Fix direction: delete the public small files, keep the algorithm as an anonymous-namespace helper or private method in `topology_engine.cpp`.
   - Verification: existing classifier tests should move to TopologyEngine behavior tests or be deleted if redundant.

3. Reduce ObjectTable recovery-index hot-path cost.
   - Comments: `182328501`, `182504220`
   - Fix direction: recovery snapshot should not add extra ordinary Insert/Get/Contains lock/key-copy tax. Prefer recovery-owned snapshot/scan path.
   - Verification: ordinary Insert/Get/Contains tests plus recovery snapshot test.

4. Continue small-file and format-only cleanup.
   - Comments include: `180739580`, `180753368`, `180756299`, `180831247`, `181015299`, `182312804`, `182313914`, `182315112`, `182317467`, `182317764`, `182319189`, `182319331`, `182319503`, `182320663`, `182457699`, `182457927`, `182458419`, `182466774`, `182501798`, `182539121`
   - Fix direction: remove or revert non-essential files and pure formatting changes. Keep only abstractions that carry real ownership or cross-module contract.
   - Verification: range diff should show reduced production-source surface and fewer build targets.

## P3: Acceptance And Build Coverage

1. Add S2/S7 end-to-end gate coverage.
   - Comment: `182328495`
   - Required scenarios:
     - ordinary Object/KV service opens before full reconciliation;
     - `MIGRATION_TARGET` remains blocked until Complete evidence;
     - migration allowed after Complete.

2. Connect admission UTs to Bazel worker test aggregation.
   - Comment: `182498369`
   - Fix direction: ensure startup admission UT is included in the intended Bazel `all_worker_tests` path.

3. Restore no-replication URMA rebalance data-safety coverage or equivalent.
   - Comment: `182522945`
   - Required scenario: `enable_data_replication=false`, URMA write rebalance, `NotifyRemoteGet` / primary update, data remains readable.

4. Fix `cluster_topology_contract_ut` metrics link.
   - Comment: `182504214`
   - Fix direction: add missing `common_metrics` dependency where the test target needs it.

## Fatal Batch Design

This section refines the P1/P2 items into merge-critical batches. These batches must be handled before a ready signal. Small-file cleanup and format-only cleanup can run in parallel, but they do not replace these fixes.

### Batch F1: Linearized Migration Admission

**Comments:** `181414271`, `181414282`

**Problem:** `MIGRATION_TARGET` operations still rely on instantaneous `CheckAdmission()`, while ordinary Object/KV entrypoints use `AcquireAdmissionGuard()`. Isolation can begin after the check but before migration or metadata commit.

**Design:**

- Split admission into two explicit execution models:
  - ordinary RUNNING Object/KV reads/writes use a lock-free fast path and do not allocate/copy snapshot state;
  - `MIGRATION_TARGET` uses a generation/epoch ticket or guard that covers the critical commit boundary.
- Move the fast-path check before `TryAcquireReadGuard()` in `AcquireAdmissionGuard()` for non-linearized ordinary operations.
- For long or async operations such as `PublishMetaAsync`, do not hold a blocking read lock through the thread-pool task. Instead, either reject before enqueue, or carry an admission epoch and revalidate at execution and commit.
- Replace direct `CheckAdmission(MIGRATION_TARGET)` in migration entrypoints with the chosen linearized helper.

**Completion criteria:**

- Isolation after admission but before commit cannot let a new migration write land.
- Ordinary RUNNING Object/KV service does not pay the heavy guard path.
- Focused tests cover `MigrateData`, `MigrateDataDirect`, `NotifyRemoteGet`, `PublishMeta`, and `ChangePrimaryCopy` rejection after isolation.

**Close after verification:** `181414271`, `181414282`.

### Batch F2: Publish / Forward / Notify Success Semantics

**Comments:** `182498336`, `182498342`, `182504202`

**Problem:** Some write paths can be accepted or reported as successful even when admission or actual migration work failed.

**Design:**

- `PublishMetaAsync` must not make the caller observe success when the worker-pool admission check fails. Prefer a synchronous admission decision before enqueue; if a second execution-time check is retained, its failure must be recorded in the operation result.
- P2P master-forwarding write paths must call the same runtime admission surface as direct local writes before mutating local object state.
- `NotifyRemoteGet` must only allow source-side release for keys that were actually migrated and acknowledged. Failed or skipped keys stay retained or enter an explicit retry/failure list.

**Completion criteria:**

- Injected admission failure in async `PublishMeta` is visible to the caller or operation status.
- Isolated/recovering worker rejects P2P forwarded writes.
- Partial `NotifyRemoteGet` migration failure does not release source data for failed keys.

**Close after verification:** `182498336`, `182498342`, `182504202`.

### Batch F3: Recovery Evidence, Retry, And Complete Semantics

**Comments:** `182504189`, `182522659`, `182522700`, `182522721`, `182522763`

**Problem:** Recovery can be marked consumed or complete without durable evidence, and stale callbacks can block later retries.

**Design:**

- Treat `Complete` as a durable evidence state, not as "request dispatched".
- Bind `objectCacheRecoveryRequestInFlight_` to recovery generation rather than a plain bool, or clear it on stale terminal callbacks when no current fanout owns it.
- `PushMeta` batch partial failure must return an incomplete evidence report and preserve failed ids for retry.
- Deferred retry must not complete an incident only because work was placed in memory. Completion requires durable success or a terminal failure state.
- Recovered ACK locations must be persisted to the authoritative location table before ownership evidence is marked ready.
- Local recovery failure must preserve retry eligibility under a bounded budget.

**Completion criteria:**

- Stale generation callback cannot block a fresh recovery request.
- Batch failure, deferred retry, and ACK persistence failure all keep `Complete == false`.
- Retry is bounded, observable, and generation-fenced.

**Close after verification:** `182504189`, `182522659`, `182522700`, `182522721`, `182522763`.

### Batch F4: Recovery Address And Primary Authority

**Comments:** `182522749`, `182498353`

**Problem:** Membership recovery drops the recovered address, and failure recovery can promote ownership without sufficient data evidence.

**Design:**

- `HandleMembershipRecovery(address, timestamp)` must either:
  - ignore events where `address != localAddress_` for local worker recovery; or
  - carry the peer address through the recovery request if the intended operation is peer recovery.
- The worker must never report local recovery evidence for a peer address.
- Failed primary promotion must require authoritative owner/copy/generation evidence. If evidence is absent or inconclusive, keep migration/promotion blocked and surface recovery incomplete.

**Completion criteria:**

- Peer RECOVERING event does not trigger local object-cache recovery fanout.
- Failed primary cannot be replaced by executor without copy/owner evidence.
- Tests distinguish local recovery, peer recovery, and stale recovery events.

**Close after verification:** `182522749`, `182498353`.

### Batch F5: Bounded Time, Retry, And Shutdown

**Comments:** `182555200`, `182522827`, `182555202`, `182555198`

**Problem:** Some recovery operations can run longer than their callers, give up too early relative to async writes, or wait forever during shutdown.

**Design:**

- Do not use `node_dead_timeout_s` as recovery operation deadline. It is a failure-detection parameter, not a reconciliation execution budget.
- Effective recovery deadline is `min(caller deadline, incident deadline, recovery hard limit)`.
- Clear-data retry budget must cover a single async L2 Save timeout, or persist unfinished cleanup as retryable work.
- `UnregisterResourceRecoveredHandler()` must accept or derive a stop deadline, cancel future work, then use timed wait. On timeout, return/log a clear failure instead of blocking forever.
- Restore busy-heal probe timing so the third probe does not fall inside the 1-second remote rate-limit window.

**Completion criteria:**

- Short caller deadline is not extended by `node_dead_timeout_s`.
- Clear-data does not abandon cleanup before one normal async L2 Save can finish.
- Worker stop cannot block forever on a resource recovered handler.
- Busy-heal probe schedule is limiter-window safe.

**Close after verification:** `182555200`, `182522827`, `182555202`, `182555198`.

### Batch F6: Rolling Upgrade Compatibility

**Comments:** `182522913`

**Problem:** New `ReconciliationQueryPb.event_type` can be sent to an old master that treats the request as restart semantics.

**Design:**

- Add feature/version gating before sending non-restart event types to metadata owners.
- If compatibility cannot be proven, fall back to restart-safe behavior or reject with retryable status until all involved metadata owners support the field.
- Keep old master handling safe: unknown event type must not trigger local-isolation or network-recovery side effects as restart.

**Completion criteria:**

- New worker to old master does not misclassify `LOCAL_ISOLATION` or `NETWORK_RECOVERY` as restart cleanup.
- Tests or compatibility harness cover old-field-absent behavior.

**Close after verification:** `182522913`.

## Already-Replied But Still Open

Re-check these before closing:

- `180770953`: has a previous "changed this line" reply, but still unresolved.
- `181414282`: has a reply, but latest source still appears to take heavy guard path first. Do **not** resolve until truly fixed.
- `182311566`: has a previous reply. Re-verify whether sorting was removed or justified.
- `182319331`: has a previous reply. Re-verify whether the small-file concern was actually addressed.

## Suggested Commit Order

1. **Scope cleanup commit**
   - Remove unrelated coordinator-election/raft slice if confirmed accidental.
   - Revert pure formatting and accidental file changes.

2. **Quick correctness and build commit**
   - Busy-heal constants, deadline semantics, proto deps, copyright/log cleanup, zero-timeout classifier, centralized local-master metadata cleanup.

3. **Admission correctness commit**
   - `MIGRATION_TARGET` linearization, real `AcquireAdmissionGuard` fast path, `PublishMetaAsync` result semantics, P2P forwarded write admission.

4. **Recovery generation commit**
   - no side effects under generation lock, in-flight generation binding, membership address preservation, retry/ACK/PushMeta evidence correctness.

5. **Slimming and boundary commit**
   - coordination backend responsibility, classifier inlining, ObjectTable hot-path cost, small files.

6. **Acceptance coverage commit**
   - ST/UT/Bazel/CMake coverage for S2/S7, startup admission, URMA no-replication rebalance, metrics link.

Each commit should be self-verifiable and reviewable. If the current PR policy requires squashing later, still keep local commit boundaries during development for review and rollback.

## Verification Gate Before Resolving Comments

Before replying/resolving any GitCode discussion:

```bash
git diff --check <base>...HEAD
python3 .skills/ds-pr-review/scripts/sensitive_scan.py --range <base>...HEAD
```

Also run the focused unit or ST target that corresponds to the fixed comment. For comments about architecture/scope, include exact diffstat reduction and deleted/retained files in the reply.

Use the comment processor:

```bash
python3 .skills/ds-pr-comment-proc/scripts/pr_comment_proc.py list --pr 1405 --unresolved
python3 .skills/ds-pr-comment-proc/scripts/pr_comment_proc.py process --plan /tmp/pr1405-resolve-plan.json
python3 .skills/ds-pr-comment-proc/scripts/pr_comment_proc.py process --plan /tmp/pr1405-resolve-plan.json --apply
python3 .skills/ds-pr-comment-proc/scripts/pr_comment_proc.py verify --plan /tmp/pr1405-resolve-plan.json
```

Plan item format:

```json
{
  "pr": 1405,
  "items": [
    {
      "comment_id": 182555198,
      "reply": "已修复：恢复 busy-heal probe 退避到 1 秒滑窗安全语义，并补充 focused UT 覆盖第三次 probe 在窗口释放后执行。验证：<sanitized command/result>。",
      "resolve": true
    }
  ]
}
```

Do not resolve a parent discussion with only a generic reply. The reply should name what changed, what was verified, and why the original risk is gone.

## Reporting Back To Central Control

When done with a batch, report:

- exact head SHA;
- base SHA;
- file count and diffstat delta before/after the batch;
- comments fixed and resolved by id;
- comments fixed but intentionally left open, with reason;
- remaining P0/P1 blockers;
- exact verification commands and exit codes;
- whether any push occurred, and the verified fork remote if so.

No extra `/retest` is needed unless central control explicitly requests it.
