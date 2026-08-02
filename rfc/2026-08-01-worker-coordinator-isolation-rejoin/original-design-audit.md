# Original Design Audit

Source design: `C:/Users/T14S/Documents/datasystem/措施二.md`

PR: https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1798

## Summary

The implementation covers the core no-kill and cold-rejoin gate path, but it is not a perfect one-to-one landing of the
original design. The main differences are:

- The original design says v1 should not add RPC/PB, while the PR adds `WorkerWorkerOCService.GetPeerHashRing`.
- The original design asks for coordinator-unavailable peer topology/hashring sync; the PR currently exposes the peer
  control surface and tests the existing `GetHashRing` response contract, but does not implement periodic peer refresh
  in `TopologyEngine`.
- The original design asks to clear local meta/data, including OC/SC metadata cleanup. The PR clears local object-table
  data and gates membership recreate, but does not yet add OC/SC per-worker metadata cleanup wrappers.

## Requirement Mapping

| Original requirement | Current PR evidence | Status | Note |
|---|---|---|---|
| Worker must not SIGKILL when its local member disappears from topology | `TopologyEngine::PublishBackendEvidence` logs `action=require_rejoin`, sets `ROLE_ISOLATED`, returns OK; UT `LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill` | Covered | Core process-liveness behavior is implemented. |
| Worker must not self-kill while coordinator is unavailable but local topology still contains itself | `AsymmetricBackendOutageIsolatesThenRecovers` asserts no membership rejoin is required for transient backend quorum loss | Partially covered | It validates the no-rejoin/no-kill distinction in UT; no process-level isolation ST was added. |
| Once removed, ordinary business must close before rejoin | `TopologyAvailabilityLevel::ROLE_ISOLATED` drives existing availability handler to close topology serving admission | Partially covered | Admission close is implied by existing handler; no direct business RPC `K_NOT_READY` UT was added. |
| Membership recreate must be blocked until cleanup succeeds | `DsCoordinationBackend::MembershipRecreateGate`; UT `RecreatedMembershipIsBlockedUntilCleanupGatePasses` and `RecreatedMembershipInvalidatesWatchesAfterCleanupGatePasses` | Covered | Gate is invoked before membership mutation. |
| Cleanup gate must cover all `AutoCreateKeepAliveKey(true)` recreate paths | Gate is inside `AutoCreateKeepAliveKey(bool recreated)` and guarded by `recreated` | Covered | This is the right choke point for recreate writes. |
| Cold rejoin cleanup must be local and narrow, not survivor failure cleanup or ref rebuild | `WorkerOCServiceImpl::CleanupLocalStateForRejoin`; `WorkerOcServiceClearDataFlow::ClearLocalObjectsForRejoin`; UT `CleanupLocalStateForRejoinDoesNotRebuildRefs` | Partially covered | Data/object table cleanup covered; OC/SC metadata cleanup wrappers are not implemented. |
| No new class in v1 | No new production class added | Covered | Implemented through existing classes, builder setter, hook, and lambdas. |
| No new RPC/PB in v1 | PR adds `WorkerWorkerOCService.GetPeerHashRing` | Deviates | This is the clearest original-design mismatch. |
| Peer information remains observation only, not coordinator authority | PR service delegates to existing `WorkerOCServiceImpl::GetHashRing`; no publication into authoritative `TopologySnapshot` | Covered for exposed surface | Since periodic peer refresh is not wired, it cannot publish peer data as authority. |
| Coordinator recovery must exact-read ground truth | Existing topology/watch path remains unchanged; no peer-authority publishing added | Partially covered | No new exact-read-specific test added in this PR. |
| Periodically pull peer `GetHashRing` while coordinator is unavailable and accept only newer version | No `TopologyEngine` peer refresh hook or periodic refresh implementation; existing `WorkerGetHashRingTest.*` covers response version semantics | Not implemented | PR body lists background refresh as follow-up. This is a scope reduction versus original `措施二.md`. |
| If peer topology does not contain local member, enter rejoin-required | No peer-refresh consumer exists | Not implemented | Would need the deferred peer refresh hook. |
| Ordinary ST cases should target under 6s | No new ST added; existing ST smoke passes but is 21.696s and 26.153s | Partially covered | The PR avoids adding slow gate ST; current smoke evidence is manual only. |

## Recommended Follow-Up Decision

Before merging, decide whether the original design should be adjusted or the PR should be changed:

1. Strict original design: remove `GetPeerHashRing` RPC/PB and defer peer sync entirely, or find an existing worker-worker
   path that can call `WorkerOCServiceImpl::GetHashRing` without protocol changes.
2. Accept minimal protocol extension: update the design/PR description to explicitly justify `GetPeerHashRing` as the
   smallest worker-worker control surface needed for peer observation.
3. Complete the missing peer refresh path in this PR: add the `TopologyEngine`/`WorkerOCServer` hook that periodically
   pulls peer hash-ring versions and handles missing-local peer responses. This broadens the PR beyond the current small
   review surface.

The current PR is a coherent subset for no-kill plus cold-rejoin gating, but it should not be described as a complete
implementation of every peer-sync and metadata-cleanup detail in the original `措施二.md`.
