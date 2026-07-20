# Worker Self-Healing Scale/Fault Overlap Follow-Ups

Updated: 2026-07-21

This file records the scale-up/scale-down plus overlapping-fault cases that are not yet fully closed as active
acceptance coverage for the worker local-isolation self-healing story.

## Current Scope

The current PR focuses on preventing local-isolation suicide and avoiding data loss under TCP/control-backend blink
scenarios. It already has active coverage for local isolation, peer data survival, global backend outage, topology
jitter, migration-target admission filtering, and several slot-recovery multi-fault paths.

The remaining gap is not the base self-healing path. The gap is the stronger product matrix where scale-up/scale-down
and recovery are already in progress when another worker/control-plane fault overlaps.

## Unfinished Acceptance Cases

| ID | Case | Current State | Needed Acceptance |
| --- | --- | --- | --- |
| SF-01 | Voluntary scale-down migration is in progress, then the old owner hits local isolation/TCP blink. | Partially covered by voluntary scale-down and local-isolation cases separately. | Active ST proves old-owner data is not prematurely cleaned, new owner does not expose incomplete ownership, and final Get returns expected data. |
| SF-02 | Scale-up target joins while source/peer has local isolation or backend disconnect. | Migration-target admission filtering exists for isolated/recovering/OOM/draining targets, but not a full scale-up overlap ST. | Active ST proves planner/admission does not route data to an unsafe target and source data remains readable after recovery. |
| SF-03 | Same slot has dual failure during recovery. | Disabled end-to-end slot case exists conceptually; active coverage has multi-worker slot recovery but not same-slot dual-failure E2E. | Promote or replace with deterministic ST proving one accepted owner, no stale owner service, and data intact. |
| SF-04 | Recovery takeover owner fails again before recovery finishes. | Slot manager has consecutive-failure coverage, but E2E ownership/data visibility closure is not fully active. | Active ST proves takeover can continue or retry safely, failed owner is fenced, and object data remains intact. |
| SF-05 | Restart worker or newly scale-up worker is selected as recovery target. | Topology/slot planner UT coverage exists; active E2E target-selection validation is still incomplete. | Active ST proves selected target has full admission/evidence readiness before serving migrated data. |
| SF-06 | Recovery preload hits OOM while receiver already has partial data. | OOM admission and migration OOM UT coverage exists; receiver-data preservation E2E remains open. | Active ST proves receiver partial data is either fenced/invisible or completed safely, with no incorrect read. |
| SF-07 | Voluntary scale-down transitions into passive scale-down/fault path. | Reason/mode distinction is under review; active overlap semantics are not closed. | Active UT/ST proves voluntary draining and passive fault reasons are distinguishable and cleanup/recovery behavior stays correct. |
| SF-08 | Local topology stamp lags while peer has newer topology and reports control backend available. | Review feedback identified risk; coverage needs to prove stale local stamp does not block local-isolation admission closure. | UT plus ST/injection proves peer AVAILABLE with stale local stamp still closes admission and records stale-authority diagnostics. |
| SF-09 | Peer probe partially fails after earlier local-isolation evidence was observed. | Review feedback identified all-or-nothing probe risk. | UT proves partial successful peer observations are retained and sticky LOCAL evidence is not overwritten by a later empty batch. |
| SF-10 | Active scale-in/scale-out request overlaps with global backend outage. | Global outage no-self-isolation is covered; active topology operation overlap needs explicit acceptance. | ST proves workers do not self-isolate during true global outage and do not commit unsafe scale migration until backend evidence returns. |
| SF-11 | Stream client-facing traffic overlaps with local isolation or recovery. | Task 6 audit found no runtime/admission integration in `worker/stream_cache`; this is explicitly out of Plan A source changes. | Active stream ST/UT proves Subscribe/GetDataPage/DeleteStream/Reset or equivalent normal stream traffic rejects during `LOCAL_ISOLATED` and `RECOVERING`, while legal recovery/control RPCs remain allowed. |
| SF-12 | KV/object-style normal API traffic overlaps with local isolation or recovery. | Object-cache admission is stronger after Task 5, but the design-level KV acceptance cases need named active coverage instead of implicit mapping. | Active KV-facing Get/Set acceptance proves normal traffic rejects during `LOCAL_ISOLATED` and `RECOVERING`, and data remains readable after recovery completes. |

## Recommended Closure Order

1. SF-08 and SF-09 first: these are correctness blockers in the control-backend failure classifier and directly affect
   whether admission closes during real local isolation.
2. SF-01 and SF-04 next: these directly validate the story's data-loss goals during scale-down/recovery overlap.
3. SF-02 and SF-05 next: these close scale-up target safety and planner/admission integration.
4. SF-06, SF-07, SF-10, SF-11, and SF-12 after the above pass: these harden OOM, reason semantics, global-outage,
   stream admission, and KV-facing admission overlap.

## Acceptance Rule

Do not mark this matrix complete only because CI passes. Each case above needs one of:

- an active UT/ST name linked in the coverage matrix, or
- an explicit decision that the case is out of scope for this PR/stage, with the owner and follow-up branch recorded.

Disabled tests do not count as active acceptance coverage until they are either enabled or replaced by deterministic
active tests with bounded runtime.

## Related Cluster Boundary Review

The scale/fault overlap matrix is also tracked as CB-07 in
`cluster-boundary-review-20260720.md`. The overall cluster-boundary judgment is:

- the implementation direction is correct because worker self-healing does not directly own topology commits;
- `ICoordinationBackend` and the topology controller/repository path remain the intended cluster boundary;
- the merge gate is not closed until recovery-before-visible, fresh recovery evidence, backend role lifecycle separation,
  inconclusive fail-closed behavior, and admission coverage are either fixed or explicitly scoped into follow-up work.
