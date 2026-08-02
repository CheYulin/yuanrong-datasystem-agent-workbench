# Core Case Coverage Plan

PR: https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1798

Issue #925 covers deferred peer routing correction and performance optimization. This plan covers only measure-2 core
correctness cases that are not covered by #925.

## Scope

| Case | Test shape | Goal | Out of scope |
|---|---|---|---|
| Single worker-coordinator blink, not removed | ST | Only one Worker loses Coordinator control-plane access; other Workers and Coordinator remain normal. The isolated Worker must not exit or cold rejoin, and must recover after the fault is cleared. | Peer routing correction, async peer refresh, cleanup backoff. |
| Removed Worker rejects ordinary business | UT | Once local member is removed or identity changes, ordinary client-facing admission closes and business validation returns not-ready. | Process-level restart/rejoin. |
| Recreate/Ensure gate coverage | UT | Every membership incarnation path is blocked while cleanup gate fails and proceeds only after cleanup succeeds. | Cleanup performance optimization. |
| Transient backend failure does not cleanup/rejoin | UT | Backend unavailable while local topology still contains this Worker must not invoke cleanup/rejoin. | Route correction from peer view. |

## ST Design

Test name: `CoordinatorBackendClusterTest.SingleWorkerCoordinatorBlinkRecoversWithoutClusterDegrade`.

Minimal sequence:

1. Start one Coordinator and two Workers.
2. Wait for both Workers to be `ACTIVE`.
3. Write one small KV object.
4. Inject Coordinator-backend failure only into Worker 1.
   - First choice: existing `CoordinationBackend.KeepAlive.returnError`.
   - If this does not drive the intended local control-plane failure, add one narrow test-only inject point in
     `DsCoordinationBackend` for Worker-side Coordinator RPCs.
5. Wait past `node_timeout_s`, but avoid waiting for a Coordinator-side worker removal.
6. Assert Worker 1 process is alive and Worker 0 remains active in Coordinator topology.
7. Assert cleanup/rejoin was not triggered by checking a dedicated cleanup inject counter.
8. Clear the Worker 1 injection.
9. Wait for both Workers to be active again.
10. Run one lightweight Put/Get after recovery.

## UT Design

1. Removed Worker rejects ordinary business:
   - Drive `TopologyEngine` to `ROLE_ISOLATED` by publishing a topology that no longer contains the local member.
   - Verify the availability handler closes admission.
   - Verify a lightweight `WorkerOCServiceImpl::ValidateWorkerState` path returns `K_NOT_READY`.

2. Gate coverage:
   - `AutoCreateKeepAliveKey(true)` fails while gate returns `K_NOT_READY`.
   - `OnMembershipEnsured(...)` fails while gate returns `K_NOT_READY`.
   - Both proceed after the gate returns OK.

3. Transient failure does not cleanup/rejoin:
   - Current snapshot contains the local member.
   - Coordinator access returns unavailable.
   - Peer/control evidence does not remove or fail the local member.
   - Assert `RequiresMembershipRejoin()` remains false and cleanup hook count remains zero.

## TDD / SDD Execution

1. RED: add the ST/UT tests first and run the focused targets remotely on `tiantiyun-80c128g`. Record expected failures.
2. GREEN: implement only the missing test hook or minimal production behavior needed by the tests.
3. Verify remotely with `/home/ds-thirdparty-cache`, `URMA_MOCK=on`, and `-j80` under `tmux`.
4. Always rerun previously failed cases:
   - `TopologyRecoveryManagerTest.UnboundRequestsDoNotConsumeClusterAdmission`
   - `TopologyRecoveryManagerTest.RequestsOnePayloadForIdenticalHighestEvidence`
   - `KVCacheClientServiceDiscoverySwitchBackTest.TestRecoverLocalWorker`
5. Update PR description with case counts, durations, and the exact boundary that #925 owns only deferred routing and
   performance work.

## CodeGraph

`timeout 30s /home/t14s/.local/bin/codegraph status /home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph`
failed with `unable to open database file`; exact-head source inspection and build rules are used as fallback.
