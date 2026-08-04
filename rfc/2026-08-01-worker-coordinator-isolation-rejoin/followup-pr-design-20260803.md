# PR1798 Follow-up Fix Design

| Attribute | Value |
|---|---|
| Created | 2026-08-03 |
| Baseline | `openeuler/yuanrong-datasystem main/master@232919bfd4c5e3c06d22920e273487deea4fe0aa` |
| Source PR | `openeuler/yuanrong-datasystem!1798` merged |
| Related issues | `#940`, `#941`, `#942`, `#943` |
| Target PR count | One follow-up PR |
| Execution style | TDD + SDD, small commits, remote validation on `tiantiyun-80c128g` |

## 1. Goal

This follow-up PR fixes the simple, design-aligned gaps left by PR1798 review comments without broadening Measure 2.
The PR must preserve the original Measure 2 semantics:

- Coordinator topology remains the only ground truth.
- Peer topology/hashring is non-authoritative evidence only.
- A worker that has been removed from authoritative topology must not serve ordinary business with the old identity.
- Cleanup gate must block membership recreate until local cleanup succeeds.
- The implementation stays in existing classes and avoids a new recovery state machine.

## 2. Issue Classification

| Issue | Review comments | Category | Include in this PR | Reason |
|---|---|---|---|---|
| `#940` | `182982980` | Serious correctness | Yes | Consecutive missing snapshots can clear `membershipRejoinRequired_`; this directly violates the cleanup gate contract. |
| `#940` | `182982981`, `182982998`, `182983002` | Cleanup performance/concurrency | No | Valid, but shortening locks and changing object-table traversal requires a separate concurrency design. |
| `#941` | `182982978` | Serious coordinator HA | No | Valid, but delayed reconcile backoff changes coordinator recovery scheduling policy. |
| `#941` | `182982983`, `182982997`, `182983004` | Coordinator recovery edge cases | No | Related to recovery manager policy and shutdown boundedness, not the minimal Worker rejoin contract. |
| `#942` | `182982988`, `182982993`, `182983006` | Peer refresh performance/defense | Partial | Per-peer RPC boundedness is a safe local fix; response trimming remains out of scope because peer data is still non-authoritative in v1. |
| `#943` | `182983982` | ST semantic gap | Yes | The short-blink case must prove worker-coordinator RPC blink only, with other workers healthy. |
| `#943` | `182983990` | Cleanup gate evidence gap | Yes, preferably UT first | Measures the required cleanup-before-recreate contract without adding a slow ST matrix. |

## 3. Included Fixes

### 3.1 Keep Rejoin Required Across Consecutive Missing Snapshots

Current source evidence:

- `TopologyEngine::PublishBackendEvidence` consumes `localMemberExistedInPreviousSnapshot_` by `exchange(false)`.
- On the first authoritative snapshot that misses the local member, the worker enters `membershipRejoinRequired_=true`.
- On a second consecutive missing snapshot, `localMemberExistedInPreviousSnapshot_` is already false. The current branch can store `membershipRejoinRequired_=false`, which weakens the cleanup gate.

Required behavior:

- If `membershipRejoinRequired_` is already true, a later missing snapshot must keep it true.
- If the local member never existed in any prior legal snapshot, keep `NOT_READY` and do not require rejoin.
- If the last known local member was `PRE_LEAVING` or `LEAVING`, keep the existing scale-in behavior and do not force cold rejoin.

Minimal implementation shape:

```cpp
const bool rejoinAlreadyRequired = membershipRejoinRequired_.load(std::memory_order_relaxed);
if ((!localMemberExisted && !rejoinAlreadyRequired) || localMemberWasLeaving) {
    membershipRejoinRequired_.store(false, std::memory_order_relaxed);
    SetAvailability(TopologyAvailabilityLevel::NOT_READY, "local_member_missing");
    return Status::OK();
}
membershipRejoinRequired_.store(true, std::memory_order_relaxed);
SetAvailability(TopologyAvailabilityLevel::ROLE_ISOLATED, "local_member_missing");
return Status::OK();
```

Test design:

- Add or adjust a `TopologyEngineTest` UT.
- Arrange a legal snapshot containing the local worker and publish it.
- Publish a later authoritative snapshot without the local worker.
- Publish another authoritative snapshot without the local worker.
- Assert `RequiresMembershipRejoin()` remains true after both missing snapshots.

### 3.2 Clarify Short-Blink ST Semantics

Measure 2 expects this case:

- Only worker4-coordinator RPC is unavailable for a short window.
- Other workers remain connected to the coordinator and to worker4.
- Coordinator does not commit worker4 removal.
- After the RPC blink recovers, worker4 remains alive and aligns membership/watch/topology.

Current review concern:

- Existing blink injection window was longer than `node_timeout_s`, which makes the case ambiguous: it may be testing either short blink, delayed removal, or removal followed by fast rejoin.

Fix direction:

- Keep the injected failure shorter than the configured passive-removal threshold for this ST, or explicitly configure the test threshold so the injected window is inside the non-removal interval.
- Keep the injection scoped to worker-coordinator RPC only.
- Keep peer/other-worker paths healthy.
- Assert both process survival and cluster membership after recovery.

This test change must not depend on route correction from peer hash rings because that mechanism is out of scope for v1.

### 3.3 Strengthen Cleanup Gate Evidence

Measure 2 requires:

- After authoritative topology removes the local worker, ordinary business is rejected.
- Membership recreate must be blocked until local cleanup succeeds.
- Cleanup failure must keep the worker unavailable and prevent membership recreation.

Preferred test approach:

- Use UT/fake before adding ST cost.
- Reuse existing cleanup-gate tests if present; otherwise add the smallest UT around the recreate path.
- Verify cleanup failure or incomplete cleanup blocks recreate.
- Verify cleanup success allows recreate.

ST should remain a process-level smoke test only. Do not add a large matrix or long retry window.

## 4. Excluded Fixes And Follow-up Issues

| Issue | Why Excluded From This PR | Expected Follow-up |
|---|---|---|
| `#941` delayed reconcile livelock | Needs a coordinator recovery scheduling design: backoff, retry budget, and wake-up rules. | Separate design + PR. |
| `#940` cleanup lock/deadline/concurrency | Requires object-table and metadata-manager concurrency boundary analysis. | Separate cleanup safety PR. |
| `#942` peer refresh budget/full response/exception guard | Performance and defense-in-depth; not required to restore Measure 2 correctness. | Separate optimization PR. |

## 5. Commit Plan

| Commit | Scope | Expected tests |
|---|---|---|
| `fix(worker): preserve rejoin-required on repeated missing topology` | `TopologyEngine` minimal logic plus UT | Focused `TopologyEngineTest` |
| `test(worker): clarify coordinator blink isolation st` | Short-blink ST semantic cleanup | Focused coordinator-backend ST |
| `test(worker): strengthen rejoin cleanup gate evidence` | UT or minimal ST for cleanup-before-recreate | Focused coordination/backend cleanup gate UT |

The final PR may keep these commits during review. Squash only if requested before merge.

## 6. Validation Plan

Remote-only validation:

- Host: `tiantiyun-80c128g`.
- Third-party cache: `/home/ds-thirdparty-cache`.
- CMake build must enable `URMA_MOCK`.
- Build concurrency: `-j80` when host is idle, otherwise `-j40`.
- Background tasks should run in `tmux`.

Required evidence:

| Gate | Required evidence |
|---|---|
| UT | New focused UT names, count, and runtime. |
| ST | New/updated focused ST names, count, and runtime. |
| CMake | Build command, `URMA_MOCK` option, concurrency, result. |
| Bazel | Source builds through Bazel or exact blocker. |
| Format | `clang-format` only on touched files to avoid unrelated noise. |
| Static | `clang-tidy` or codecheck triage for touched files; do not churn function-size-only findings. |

## 7. PR Description Requirements

The follow-up PR description must include:

- Linked issues: `#940`, `#943`.
- Explicit exclusions: `#941` and `#942` remain tracked follow-ups.
- Measure 2 semantic matrix:
  - local member missing after prior existence keeps worker alive and rejoin-required.
  - cleanup gate blocks recreate before local cleanup succeeds.
  - short worker-coordinator blink does not imply whole-cluster degradation.
- UT/ST case count and runtime table with line wrapping friendly Markdown.

## 8. Integration Result

Created follow-up PR:

- PR: `openeuler/yuanrong-datasystem!1821`
- URL: `https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1821`
- Source branch: `yche-huawei:pr1798-followup-integrated`
- Target branch: `openeuler/yuanrong-datasystem:master`
- Conflict status at creation: clean

Commits:

| Commit | Scope |
|---|---|
| `0e334ecc4` | Keep `membershipRejoinRequired_` across consecutive authoritative snapshots that still miss the local worker. |
| `f1cb5c0d4` | Clarify short worker-coordinator blink ST semantics and strengthen cleanup-gate UT evidence. |
| `c4b30fd12` | Retry stored authority adoption after an empty read, with TDD coverage for the #941 stored-authority latch gap. |
| `295b46959` | Align the old cold-rejoin ST with upstream witness semantics: short worker-coordinator RPC isolation is protected from removal, while real worker failure still exercises removal and cold rejoin without suicide. |
| `33dc550de` | Guard peer topology refresh exceptions so a thrown peer hint callback cannot terminate the Engine state thread. |
| `72bd287bb` | Propagate the rejoin cleanup deadline into final local object cleanup and keep the membership recreate gate closed if the object-clear stage times out. |

Final diff size:

| Area | Files | Delta |
|---|---|---|
| Source | `src/datasystem/cluster/runtime/topology_engine.cpp` | Minimal logic change, 3 lines touched. |
| Source | `src/datasystem/cluster/runtime/topology_engine.cpp` | Adds exception containment around non-authoritative peer topology refresh. |
| Source | `src/datasystem/coordinator/topology_recovery_manager.cpp` | Resets `storedAuthorityChecked` after an empty stored-authority read when the same context is still recovering. |
| UT | `tests/ut/cluster/topology_engine_test.cpp`, `tests/ut/cluster/ds_coordination_backend_session_test.cpp` | Added 3 focused cases or assertions. |
| UT | `tests/ut/coordinator/topology_recovery_manager_test.cpp` | Added 1 stored-authority retry regression and reran 2 adjacent authority cases. |
| ST | `tests/st/worker/object_cache/coordinator_backend_cluster_test.cpp` | Adjusted 1 short-blink case. |

Review severity handling included in the PR description:

| Severity | Review / Issue | PR Handling | Notes |
|---|---|---|---|
| Serious | `182982980` / `#940` | Fixed | Consecutive authoritative snapshots that still miss the local worker keep `membershipRejoinRequired_` true. |
| Warning | `182983982` / `#943` | Modified | The short-blink ST now injects a 1s worker-coordinator RPC blink, bounded below `node_timeout_s=2`. |
| Suggestion | `182983990` / `#943` | Evidence strengthened | Cleanup gate UT verifies no Range/Put membership side effects before cleanup readiness. |
| Serious / Warning | `#941` stored authority | Fixed | An empty stored-authority read no longer permanently latches `storedAuthorityChecked`; later membership activity can adopt newly stored authority. |
| Warning | `#940` cleanup performance/concurrency items | Follow-up | Lock scope, metadata scan deadline, and object-table concurrency boundaries need a separate concurrency design. |
| Serious / Warning | `#941` recovery/shutdown | Follow-up | Coordinator recovery scheduling and shutdown boundedness are coordinator HA policy changes. |
| Warning / Suggestion | `#942` | Partially fixed | Peer refresh exception containment and per-peer RPC boundedness are fixed; response trimming remains non-authoritative optimization work. |
| Build suggestion | `#945` | Follow-up | Bazel `-t build` tools packaging lacks `hashring_parser`; source build was validated with `-t off`. |

Validation evidence:

| Gate | Result | Runtime |
|---|---|---|
| CodeGraph shared index | PASS, up to date, 2159 files, 53469 nodes, 157732 edges | N/A |
| `git diff --check` | PASS | N/A |
| `git clang-format --diff` on touched files | PASS | N/A |
| `ds-pr-review prepare` | PASS, 4 files, 77 changed lines, 0 comments, no warnings | N/A |
| `ds-pr-review publish --dry-run` | PASS, 0 findings, 0 comments, no warnings | N/A |
| CMake build with URMA mock and 80 jobs | PASS | source 465s, example 5s |
| CMake build with URMA mock and 80 jobs on final SHA `295b46959` | PASS | source 465s, example 5s, total 586s |
| Target UT | PASS, 3 cases, 0 failed | 0.29s total |
| Final target UT on `295b46959` | PASS, 6 cases, 0 failed | 0.56s total |
| Peer refresh exception UT on `33dc550de` | PASS, 1 case, 0 failed | 0.043s |
| Peer refresh regression UT on `33dc550de` | PASS, 4 cases, 0 failed | 0.131s total |
| Target ST on `295b46959` | PASS, 2 cases, 0 failed | 60.119s total |
| Bazel source build with URMA mock and 80 jobs | PASS | source 400s, total 424s |

Remote validation note:

- Host: `tiantiyun-80c128g`.
- Worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1821-295b469-validate`.
- Third-party cache: `/home/ds-thirdparty-cache`.
- Remote tmux session: `pr1821_295b_validate`, completed and exited.
- Build log includes non-blocking `objcopy` debuglink warnings during strip, but the build continued through example and focused test validation successfully.

TDD evidence for #941 stored authority:

| Phase | Case | Result | Runtime |
|---|---|---|---|
| RED | `TopologyRecoveryManagerTest.StoredAuthorityEmptyReadDoesNotLatchAgainstLaterAuthority` before production fix | FAIL as expected: `DriveUntil(... READY)` returned false | 2.08s |
| GREEN | same case on `c4b30fd12` | PASS | 0.07s |

Target UT cases:

| Case | Result | Runtime |
|---|---|---|
| `DsCoordinationBackendSessionTest.RecreatedMembershipIsBlockedUntilCleanupGatePasses` | PASS | 0.06s |
| `DsCoordinationBackendSessionTest.EnsuredMembershipIsBlockedUntilCleanupGatePasses` | PASS | 0.05s |
| `TopologyEngineTest.ConsecutiveMissingLocalMemberSnapshotsKeepRejoinRequired` | PASS | 0.06s |
| `TopologyRecoveryManagerTest.ReturningMemberReusesCurrentProcessTopologyAuthority` | PASS | 0.08s |
| `TopologyRecoveryManagerTest.StoredAuthorityEmptyReadDoesNotLatchAgainstLaterAuthority` | PASS | 0.08s |
| `TopologyRecoveryManagerTest.StaleStoredAuthorityReadCannotPublishIntoRecreatedContext` | PASS | 0.08s |

Target ST cases:

| Case | Result | Runtime |
|---|---|---|
| `CoordinatorBackendClusterTest.SingleWorkerCoordinatorBlinkRecoversWithoutClusterDegrade` | PASS | 22.348s |
| `CoordinatorBackendClusterTest.RealFailedWorkerColdRejoinsWithoutSuicide` | PASS | 37.771s |

Cold-rejoin ST semantic correction:

- RED evidence: direct run of the old `IsolatedWorkerRemovedThenColdRejoinsWithoutSuicide` failed because worker1 remained `ACTIVE` after only `CoordinationBackend.KeepAlive.returnError` was injected. This matches the latest upstream witness gate: direct worker liveness proves the worker is still reachable, so a worker-coordinator RPC-only blink must not be removed.
- SDD alignment: measures-two short-blink isolation is covered by `SingleWorkerCoordinatorBlinkRecoversWithoutClusterDegrade` and upstream witness cases. Cold rejoin should validate true worker failure removal followed by restart/rejoin, not single-link RPC isolation removal.
- GREEN validation: direct-run `CoordinatorBackendClusterTest.SingleWorkerCoordinatorBlinkRecoversWithoutClusterDegrade` and `CoordinatorBackendClusterTest.RealFailedWorkerColdRejoinsWithoutSuicide` on tiantiyun with URMA mock build. Both cases passed on final SHA `295b46959`.

TDD evidence for #942 peer refresh exception containment:

| Phase | Case | Result | Runtime |
|---|---|---|---|
| RED | `TopologyEngineTest.PeerTopologyRefreshExceptionDoesNotTerminateStateThread` before production fix | FAIL as expected: unhandled `std::runtime_error` terminates the UT process | build target completed, test aborted |
| GREEN | same case on `33dc550de` | PASS | 0.043s |

Peer refresh regression cases on `33dc550de`:

| Case | Result | Runtime |
|---|---|---|
| `TopologyEngineTest.PeerHashRingRefreshAcceptsNewerVersionOnly` | PASS | 0.044s |
| `TopologyEngineTest.PeerTopologyRefreshExceptionDoesNotTerminateStateThread` | PASS | 0.042s |
| `TopologyEngineTest.PeerHashRingRefreshMissingLocalMemberRequiresRejoin` | PASS | 0.022s |
| `TopologyEngineTest.PeerHashRingRefreshFailedLocalMemberRequiresRejoin` | PASS | 0.022s |

Known non-PR blocker:

- `build.sh -b bazel -t build` fails on the current upstream BUILD graph because `//:hashring_parser_file` references `//tests/st:hashring_parser`, while `tests/st/BUILD.bazel` does not declare that target.
- This PR does not change BUILD files and validates Bazel source build with `-t off`.
- Tracking issue: `#945` for the tools packaging gap.

## 9. Next-Issue Triage After `295b46959`

Pre-edit checks:

- Branch `pr1798-followup-integrated` is aligned with the fork branch and is 4 commits ahead of upstream `main/master`.
- Shared CodeGraph index is available and up to date: 2159 files, 53469 nodes, 157732 edges.
- PR1821 currently has no unresolved review discussion; `ds-pr-review prepare` sees only bot/CLA comments.

Potential follow-up commits:

| Issue | Current assessment | TDD/SDD next step |
|---|---|---|
| `#945` Bazel tools packaging | Not a simple missing BUILD target. Exact source search found only `BUILD.bazel` and `scripts/build_bazel.sh` references; no `hashring_parser` source exists in the current tree, and CMake ST explicitly excludes `hashring_parser.cpp`. | Discuss whether the tool is still required. If yes, define the parser contract first; if no, remove the package target and update `build.sh` expectations with a Bazel analysis/build RED first. |
| `#942` peer refresh boundedness | Real peer loop is `RefreshPeerHashRing` in `worker_oc_server.cpp`, not `TopologyEngine::RefreshPeerTopology()` alone. Per-peer budget can be fixed locally by sharing the remaining deadline across remaining peers; response trimming still requires RPC/protobuf contract discussion. | Add a focused RED around "one peer RPC timeout is capped by remaining deadline / remaining peers"; keep peer evidence non-authoritative and defer response trimming. |
| `#941` recovery scheduling/shutdown | Stored-authority empty-read gap is fixed. Remaining delayed reconcile backoff, shutdown boundedness, and ensure-after-recheck behavior are coordinator HA policy changes. | Keep out of this PR until retry budget, cancellation, and shutdown semantics are explicitly agreed. |
| `#940` cleanup lock/deadline/concurrency | Rejoin gate correctness is fixed. Deadline propagation through the final object clear stage is safe and local. Lock scope, object-table traversal concurrency, and condition-variable conversion remain higher-risk. | Add a focused RED around expired rejoin object-clear deadline. Do not convert write-lock + sleep or redesign cleanup traversal in this PR. |

## 10. #945 Bazel Tools Packaging Fix

Decision:

- Preserve the documented `-t build` tools contract instead of deleting `hashring_parser_file` from root Bazel packaging.
- Restore `tests/st:hashring_parser` as a standalone `ClusterTopologyPb` json/binary converter. The old historical tool parsed legacy `HashRingPb`, which no longer exists after topology runtime integration; the new tool keeps the install name but updates the message contract to current `ClusterTopologyPb`.
- Keep the ST cluster Bazel fix declarative: remove one redundant `common.h` include from `external_cluster.cpp`, add direct Bazel deps for the headers it actually uses, and open `static_coordinator_discovery` visibility only to `tests/st/cluster`.

TDD evidence for `#945`:

| Phase | Command / Case | Result | Runtime |
|---|---|---|---|
| RED | Bazel `build.sh -b bazel -t build -U on -X off -J off -G off -P off -j 80 -u 80 -i on` on PR head before fix | FAIL as expected: root `//:hashring_parser_file` references missing `//tests/st:hashring_parser` | 5.064s |
| RED follow-up | Same gate after restoring parser target | FAIL: `tests/st/cluster:st_cluster` exposed missing Bazel direct inputs/deps (`common.h`, then topology/coordinator headers) | 38s-177s per iteration |
| GREEN | Same Bazel gate with URMA mock, 80 jobs, third-party cache | PASS: `build datasystem (bazel) success` | source 129s, total 150s |
| GREEN | CMake `build.sh -b cmake -t build -U on -X off -J off -G off -P off -j 80 -u 80 -i on` with URMA mock, 80 jobs, third-party cache | PASS: `hashring_parser`, `ds_st`, `ds_ut`, examples built; `build datasystem success` | third-party 6s, source 473s, total 617s |
| Smoke | `hashring_parser` encode/decode/help on `ClusterTopologyPb` sample | PASS | <1s |

Validation notes:

- Host: `tiantiyun-80c128g`.
- Third-party cache: `/home/ds-thirdparty-cache`.
- Bazel command includes `--config=urma_mock --config=test --jobs=80`.
- CMake build produced `build/tests/st/hashring_parser` and installed `output/tools/hashring_parser`.
- Parser smoke encoded `{"clusterHasInit":true,"version":"1","schemaVersion":"1"}` to binary, decoded it back to json, and verified installed tool help output.
- CMake strip emitted repeated `objcopy: ... debuglink section already exists` lines during install, but build.sh continued through example build and ended with `build datasystem success`.

## 11. #942 Peer Refresh Per-Peer Boundedness Fix

Decision:

- Keep the Measure 2 v1 boundary: peer topology remains non-authoritative evidence and does not correct routing.
- Fix only the worker-side peer refresh budget: each peer attempt receives `remaining_deadline / remaining_peer_count` across stub initialization and `GetHashRing`, instead of giving the first peer the whole remaining scope deadline.
- This prevents the first slow peer from consuming the full peer-refresh deadline and starving later healthy peers.
- Keep the total scope deadline unchanged and keep stale-version/missing-local/failed-local handling unchanged.
- Defer response trimming because that changes the peer RPC payload contract and is not required for the worker-coordinator isolation contract.

TDD evidence for `#942` boundedness:

| Phase | Command / Case | Result | Runtime |
|---|---|---|---|
| RED | Bazel `//tests/ut/worker:worker_worker_oc_api_test` with only `PeerHashRingRefreshRpcTimeoutIsSharedAcrossRemainingPeers` added | FAIL as expected: missing `peer_hash_ring_refresh_timeout.h` / helper | 275.992s wall, 0 tests executed |
| GREEN | Same focused Bazel UT with URMA mock, 80 jobs, third-party cache | PASS: `WorkerWorkerOcApiTest.PeerHashRingRefreshRpcTimeoutIsSharedAcrossRemainingPeers` | 39.008s wall, test 4.1s |
| GREEN | CMake source build with URMA mock, 80 jobs, third-party cache | PASS: `build datasystem success` | source 476s, total 620s |
| GREEN | CMake focused UT binary `ds_ut_object` | PASS: `WorkerWorkerOcApiTest.PeerHashRingRefreshRpcTimeoutIsSharedAcrossRemainingPeers` | 0ms gtest, 0.056s process |
| GREEN | Bazel source build with URMA mock, 80 jobs, third-party cache | PASS: `build datasystem (bazel) success` | source 387s, total 409s |

Implementation notes:

- New helper: `src/datasystem/worker/peer_hash_ring_refresh_timeout.h`.
- Call site: `RefreshPeerHashRing()` now computes remaining peers from the iterator and caps the per-peer init/RPC attempt.
- The helper is pure calculation, so the UT has no sleeps and no real RPC.

## 12. #940 Rejoin Object Cleanup Deadline Propagation

Decision:

- Keep the existing `WriteLock + sleep_for` ordinary RPC drain behavior in this PR. Converting it to a condition variable touches the `reconFlag_` ownership and ordinary RPC admission boundary, so it remains follow-up design work.
- Keep the existing object-table snapshot and synchronous clear flow. This commit only propagates the already-existing `CleanupLocalStateForRejoin(deadline)` budget into `WorkerOcServiceClearDataFlow::ClearLocalObjectsForRejoin(deadline)`.
- Check the deadline before collecting object ids and before each local object clear. If the deadline expires, return `K_RPC_DEADLINE_EXCEEDED` so the membership recreate gate remains closed.
- Extract `ClearOneObject` so the ordinary `ClearObject(vector)` path keeps its previous void/continue-on-error behavior without adding per-object temporary vectors.

TDD evidence for `#940` deadline propagation:

| Phase | Command / Case | Result | Runtime |
|---|---|---|---|
| RED | CMake build with only `WorkerOcServiceImplTest.ClearLocalObjectsForRejoinRespectsExpiredDeadline` added | FAIL as expected: no matching `ClearLocalObjectsForRejoin(deadline)` overload | build failed at `ds_ut_object` compile |
| GREEN | CMake incremental build with URMA mock, 80 jobs, third-party cache | PASS: `build datasystem success` | total 203s |
| GREEN | `WorkerOcServiceImplTest.ClearLocalObjectsForRejoinRespectsExpiredDeadline` | PASS | gtest 3ms, process 0.05s |
| Regression | 6 focused rejoin cleanup UT cases | PASS | gtest 27ms, process 0.08s |
| Build | Bazel source build with URMA mock, 80 jobs, third-party cache, `/home` output root | PASS: 9 targets, 5471 actions | elapsed 454.122s |

Regression cases:

| Case | Result | Runtime |
|---|---|---|
| `WorkerOcServiceImplTest.CleanupLocalStateForRejoinClearsLocalObjects` | PASS | 2ms |
| `WorkerOcServiceImplTest.CleanupLocalStateForRejoinRespectsExpiredDeadline` | PASS | 0ms |
| `WorkerOcServiceImplTest.ClearLocalObjectsForRejoinRespectsExpiredDeadline` | PASS | 0ms |
| `WorkerOcServiceImplTest.CleanupLocalStateForRejoinDoesNotRebuildRefs` | PASS | 0ms |
| `WorkerOcServiceImplTest.CleanupLocalStateForRejoinStopsWhenMetadataCleanupFails` | PASS | 0ms |
| `WorkerOcServiceImplTest.CleanupLocalStateForRejoinWaitsForOrdinaryRpcDrain` | PASS | 21ms |

Still deferred:

- `WriteLock + sleep_for` to condition-variable conversion.
- Cleanup traversal lock shortening and object-table concurrency redesign.
- Cleanup retry/backoff state machine.
- Coordinator delayed reconcile retry budget, shutdown cancellation, and `EnsureLeaderMembership` post-write HA policy.

CodeGraph note:

- Sandbox run returned `unable to open database file`.
- Elevated read-only `codegraph status /home/t14s/workspace/git-repos/yuanrong-datasystem` succeeded: 2159 files, 53469 nodes, 157732 edges, DB size 2571.96 MB, index up to date.

## 13. CodeCheck Function-Size Follow-up

Latest gate context:

- Jenkins trigger: `yuanrong-datasystem/8941`.
- CodeCheck report: `MR_aa294719381d41328442ee3b555c47bc/24cdaf8e0e24ed8d6116ff3ee8d8d404`.
- Result: one remaining CodeCheck issue.

Issue classification:

| Rule | File | Location | Severity | Decision |
|---|---|---|---|---|
| `G.FUN.01-CPP 函数功能要单一--函数大小` | `src/datasystem/coordinator/topology_recovery_manager.cpp` | `TopologyRecoveryManager::AdoptStoredAuthorityIfPresent`, line 1112 | Minor / level 2 | Fix with a small equivalent refactor |

Fix:

- Extracted `ResetStoredAuthorityCheckIfCurrentLocked`.
- The helper only wraps the duplicated locked predicate used by both the Range-error path and the empty-read path.
- Measure 2 semantics are unchanged: Range errors still return the Store status; empty reads still return OK and allow a later stored-authority read only when the same round, generation, and recovering context remain current.
- `AdoptStoredAuthorityIfPresent` reduced from the CodeCheck-reported 52 lines to 44 nonblank/noncomment lines.

Validation:

| Phase | Command / Case | Result | Runtime |
|---|---|---|---|
| Static | `git clang-format --diff HEAD -- src/datasystem/coordinator/topology_recovery_manager.cpp src/datasystem/coordinator/topology_recovery_manager.h` | PASS: no formatting changes | local |
| Static | `git diff --check` | PASS | local |
| Build | `cmake --build build-pr1821-codecheck --target ds_ut -j80` on `tiantiyun-80c128g`, third-party cache `/home/ds-thirdparty-cache` | PASS: `INCREMENTAL_BUILD_RC=0` | 3.6s |
| Regression | `TopologyRecoveryManagerTest.ReturningMemberReusesCurrentProcessTopologyAuthority` | PASS | 10ms |
| Regression | `TopologyRecoveryManagerTest.StoredAuthorityEmptyReadDoesNotLatchAgainstLaterAuthority` | PASS | 5ms |
| Regression | `TopologyRecoveryManagerTest.StaleStoredAuthorityReadCannotPublishIntoRecreatedContext` | PASS | 5ms |

Notes:

- Full CMake build was launched in tmux with `build.sh -b cmake -B build-pr1821-codecheck -o output-pr1821-codecheck -t build -U on -X off -J off -G off -P off -j 80 -u 80 -i on`; build source phase reached `Build source: 480 seconds` and produced `build-pr1821-codecheck/tests/ut/ds_ut`.
- Focused UT gtest total runtime: 22ms.

## 14. AArch64 Gate Follow-up: Direct Probe Error Must Not Reset Missing Budget

Gate context:

- Jenkins trigger: `yuanrong-datasystem/8943`.
- AArch64 downstream: `yuanrong-datasystem/9010`.
- Failed case: `KVClientWorkerTimeoutStorage.LEVEL1_WorkerTimeoutAndMetaGetFromEtcd`.

Root cause:

- The case shuts down worker0, waits for the dead budget, then reads the object from worker1.
- In the failing run, worker1 observed worker0 missing and reached `absence_timeout`, but the direct probe to worker0 returned `K_RPC_PEER_DEAD`.
- `BuildFailedProbeResult` attached an `UNKNOWN` observation for non-retryable RPC errors. `TopologyController::ConfirmMissingMembersUnreachable` treated any observation as transport reachability evidence, reset the missing budget, and logged `direct_probe_inconclusive`.
- The missing budget restarted before the failure topology and failure callback could commit, so worker1 queried metadata while worker0 was still the selected location and the read failed with `GetObjectRemote -> RPC peer dead`.

Fix:

- Treat a direct probe observation as reachability proof only when `ControlBackendProbeOutcome::RESPONSE`.
- Probe `ERROR`, `UNAVAILABLE`, `DEADLINE_EXCEEDED`, or `CANCELLED` are no-response evidence even if the result object carries diagnostic `UNKNOWN` observation data.
- Added `TopologyControllerTest.DirectProbeErrorWithUnknownObservationCommitsFailure` to pin the exact failing branch.

Validation:

| Phase | Command / Case | Result | Runtime |
|---|---|---|---|
| Build | `cmake --build build-pr1821-codecheck --target cluster_topology_contract_ut -j80` on `tiantiyun-80c128g` | PASS | 22.5s including build + UT run |
| Regression | `TopologyControllerTest.OneCompleteDirectProbeWithoutResponseCommitsFailure` | PASS | included in 6ms gtest total |
| Regression | `TopologyControllerTest.DirectProbeErrorWithUnknownObservationCommitsFailure` | PASS | included in 6ms gtest total |
| ST | `TEST_SRCDIR=$PWD TEST_WORKSPACE=. ./build-pr1821-codecheck/tests/st/ds_st_kv_cache --gtest_filter=KVClientWorkerTimeoutStorage.LEVEL1_WorkerTimeoutAndMetaGetFromEtcd` | PASS | 15.352s |

Post-fix evidence:

- Direct probe failure is logged as `direct_probe_no_response probe_result=error`, then `direct_probe_unreachable membership_exact_read=absent`.
- Failure topology commits with failed member state and `batch_type=FAILURE`.
- Failure callback runs `ProcessWorkerTimeout` for the stopped worker and finishes OK.
- The final Get observes the worker disconnected and reads from L2, then the ST passes.

## 15. PR1798 13 Unresolved Review Comments: PR1821 Status Refresh

Date: 2026-08-04 CST.

Current boundary:

- PR1821 remains a Measure 2 follow-up bugfix/hardening PR.
- It must not change peer hash-ring routing correction semantics.
- It must not change coordinator topology arbitration semantics without a separate design.
- It can fix local correctness bugs, defensive exception handling, bounded per-attempt waits, tests, and build packaging gaps.

CodeGraph and source check:

- CodeGraph query used first: `TopologyRecoveryManager Shutdown pendingRecoveryWork shutdownCv recoveryPool`.
- Exact PR1821 head checked locally: `28752be6feee2867bbb57fd78d44e7159e0e035d`.
- Remote safety checked: push remote `origin` is the yche-huawei fork; upstream `main` is openeuler and remains push-forbidden.

13-comment status matrix now reflected in PR1821 description:

| Comment | Issue | Priority | Status | Reason |
|---|---|---|---|---|
| `182982978` | `#941` | Serious, livelock / HA | Fixed in PR1821 follow-up | Incomplete evidence now backs off expired delayed-reconcile retries by one `discoveryWindow`; new evidence and non-expired deadlines keep existing behavior. |
| `182982980` | `#940` | Serious, correctness | Fixed | Consecutive missing-local-member topologies keep `membershipRejoinRequired_` true. |
| `182982981` | `#940` | Serious, latency | Partially fixed | Object clear deadline is now enforced; metadata scan deadline and write-lock scope remain follow-up concurrency work. |
| `182982983` | `#941` | Warning, correctness | Follow-up | Ensure-post leader recheck window needs membership pending-confirmation or retry semantics. |
| `182982988` | `#942` | Warning, performance | Partially fixed | Per-peer RPC budget is bounded; session reuse remains follow-up optimization. |
| `182982993` | `#942` | Warning, performance | Follow-up | Same-version response is already trimmed; different-version incremental/compact response needs RPC contract design. |
| `182982997` | `#941` | Warning, HA | Follow-up | This comment is about discovery-window stored-authority policy; current PR only fixes the empty-read latch gap. |
| `182982998` | `#940` | Warning, concurrency | Follow-up | Metadata manager lock scope requires lifecycle/concurrency boundary design. |
| `182983002` | `#940` | Warning, concurrency / memory | Follow-up | Object table traversal and batch cleanup need object-table API/concurrency design. |
| `182983004` | `#941` | Suggestion, reliability | Follow-up, treated as HA risk | Shutdown can wait forever on `pendingRecoveryWork_`; simple wait timeout is insufficient because `ThreadPool` still joins running tasks and closures capture manager `this`. |
| `182983006` | `#942` | Suggestion, coredump | Fixed | `RefreshPeerTopology()` catches callback exceptions and drops only the non-authoritative peer hint. |
| `182983982` | `#943` | Warning, ST semantics | Fixed | Blink ST is now under `node_timeout_s`, covering worker-coordinator RPC blink while other workers stay healthy. |
| `182983990` | `#943` | Warning, test evidence | Evidence strengthened | Cleanup gate is pinned by UT; cold-rejoin ST avoids long keepalive-failure oscillation. |

`#941 recovery/shutdown` assessment:

- The review concern is real even though the original label was suggestion-level: if a recovery task blocks in Store RPC or network retry, coordinator shutdown or failover can wait indefinitely.
- A naive `shutdownCv_.wait_for(...)` patch would not close the risk. After the wait times out, `ThreadPool` destruction still joins running tasks; skipping destruction while closures capture `this` would risk use-after-free.
- A real fix needs a small coordinator-recovery shutdown design: pass cancellation/deadline into recovery closures, define timeout return semantics, and prove manager ownership remains valid until tasks observe cancellation.
- Therefore PR1821 keeps this as `#941` follow-up rather than mixing a coordinator HA policy change into a Measure 2 worker bugfix PR.

## 16. Rebase Conflict and Serious #941 Delayed-Reconcile Backoff Fix

Date: 2026-08-04 CST.

Rebase status:

- Fetched upstream `main/master` at `5f169d7840b385d548cbda854e472780d6393500`.
- Backup branch before rebase: `backup/pr1821-before-rebase-20260804-164105`.
- Conflict file: `tests/st/worker/object_cache/coordinator_backend_cluster_test.cpp`.
- Resolution: keep the current PR semantics for `RealFailedWorkerColdRejoinsWithoutSuicide`: real `KillWorker(1)`, wait worker1 removed, verify worker0 remains alive, then `StartWorkerAndWaitReady({1})`; do not revert to keepalive/witness injection style.

Design decision:

- The serious review issue `182982978/#941` is a valid HA risk: after delayed reconcile reads empty authority and evidence is still incomplete, the old code re-queues with an already expired `discoveryDeadline`, causing immediate repeated recovery work.
- Keep the fix minimal and within Measure 2 semantics. Do not change topology arbitration, peer hash-ring routing correction, or recovery ownership model.
- Fix point: `TopologyRecoveryManager::ScheduleDelayedReconcileLocked`. When an expired deadline is re-queued, move it to `clock_->Now() + options_.discoveryWindow`; if the deadline is still in the future, leave it unchanged.

TDD evidence:

| Phase | Command / Case | Result | Runtime |
|---|---|---|---|
| RED | `TopologyRecoveryManagerTest.DelayedReconcileRetriesAreBackedOffWhenEvidenceIsIncomplete` on tiantiyun `ds_ut` before production fix | FAIL as expected: inject count grew from 3 to 150 in the 20ms observation window | 75ms |
| GREEN | Same new UT after production fix | PASS: one stored-authority read, no retry inside the 20ms observation window | 76ms |
| Regression | `TopologyRecoveryManagerTest.AcceptedPayloadInstallsAfterDiscoveryWindowWithoutNewReport` | PASS | included in 211ms total |
| Regression | `TopologyRecoveryManagerTest.DelayedReconcileTimerDoesNotBlockPayloadValidationWorker` | PASS | included in 211ms total |
| Regression | `TopologyRecoveryManagerTest.ShutdownCancelsDelayedReconcile` | PASS | included in 211ms total |
| Static | `git diff --check` | PASS | local |
| Static | `git clang-format --diff HEAD -- src/datasystem/coordinator/topology_recovery_manager.cpp tests/ut/coordinator/topology_recovery_manager_test.cpp` | PASS: no formatting changes | local |

Remote validation environment:

- Host: `tiantiyun-80c128g`.
- Build mode: CMake with `BUILD_WITH_URMA_MOCK=on`.
- Parallelism: `-j80`.
- Third-party cache/temp: `/home/ds-thirdparty-cache`, with compiler temp redirected through `/home/ds-thirdparty-cache/tmp`.

## 17. PR1821 Scope Narrowing After Latest Review Comments

Date: 2026-08-04 CST.

Review-driven scope decision:

- `#945` hashring parser/build packaging changes are not part of Measure 2 worker-coordinator isolation bugfix scope. The PR branch withdraws `build.sh`, `tests/st/CMakeLists.txt`, and `tests/st/hashring_parser.cpp` related changes.
- `#942` per-peer refresh timeout splitting is a performance/fairness optimization. The PR branch withdraws `peer_hash_ring_refresh_timeout.h` and related test/callsite changes; it keeps only the `RefreshPeerTopology()` exception guard.
- `#940` final object-clear deadline was withdrawn because the partial timeout path can return rejoin failure without a convergence/retry path. This remains a follow-up item together with cleanup convergence and lock-scope design.

Current PR1821 source boundary after narrowing:

- Production files: `topology_controller.cpp`, `topology_engine.cpp`, `topology_recovery_manager.cpp`, `topology_recovery_manager.h`.
- Test files: coordinator-backend ST plus four focused UT files.
- Net diff vs `main/master`: 9 files, 273 insertions, 48 deletions.
- No peer routing correction, no hashring parser packaging, no final-clear deadline semantics.

Latest test adjustment:

- `TopologyRecoveryManagerTest.DelayedReconcileRetriesAreBackedOffWhenEvidenceIsIncomplete` now verifies the positive backoff/retry behavior instead of relying on a fragile short negative sleep window.
- `TopologyRecoveryManagerTest.StoredAuthorityEmptyReadDoesNotLatchAgainstLaterAuthority` clears the empty-read injection and advances the mock discovery window before driving the second stored-authority read.

Latest validation on `tiantiyun-80c128g`:

| Phase | Command / Case | Result | Runtime |
|---|---|---|---|
| Build | `cmake --build build-pr1821-narrow --target ds_ut cluster_topology_contract_ut ds_st_kv_cache -j80` | PASS | targets built |
| UT | 7-case `ds_ut` recovery-manager filter covering stored authority retry/ABA, delayed reconcile, shutdown | PASS | 258ms gtest; summary wall time 1s |
| UT | 6-case `cluster_topology_contract_ut` filter covering rejoin gate, peer refresh exception, direct-probe failure | PASS | 51ms gtest; summary wall time <1s |
| ST | `KVClientWorkerTimeoutStorage.LEVEL1_WorkerTimeoutAndMetaGetFromEtcd` | PASS | 17.420s gtest; summary wall time 17s |

Comment handling plan:

- Resolve comments about withdrawn `#945`/hashring parser and unrelated build/test changes by pointing to the scope withdrawal.
- Resolve comments about withdrawn `#942` per-peer timeout/header/unit changes by pointing to the follow-up split.
- Resolve comments about withdrawn `#940` final-clear deadline partial fix by acknowledging the no-convergence risk and leaving it to the follow-up issue.
- Reply to remaining test-purpose comments with the exact guard each UT/ST provides.
