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
| `#942` | `182982988`, `182982993`, `182983006` | Peer refresh performance/defense | No | Mostly optimization or defense-in-depth; current v1 peer data remains non-authoritative as designed. |
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

Final diff size:

| Area | Files | Delta |
|---|---|---|
| Source | `src/datasystem/cluster/runtime/topology_engine.cpp` | Minimal logic change, 3 lines touched. |
| UT | `tests/ut/cluster/topology_engine_test.cpp`, `tests/ut/cluster/ds_coordination_backend_session_test.cpp` | Added 3 focused cases or assertions. |
| ST | `tests/st/worker/object_cache/coordinator_backend_cluster_test.cpp` | Adjusted 1 short-blink case. |

Validation evidence:

| Gate | Result | Runtime |
|---|---|---|
| CodeGraph shared index | PASS, up to date, 2159 files, 53469 nodes, 157732 edges | N/A |
| `git diff --check` | PASS | N/A |
| `git clang-format --diff` on touched files | PASS | N/A |
| CMake build with URMA mock and 80 jobs | PASS | source 465s, example 5s |
| Target UT | PASS, 3 cases, 0 failed | 0.29s total |
| Target ST | PASS, 2 cases, 0 failed | 22.237s and 37.557s |
| Bazel source build with URMA mock and 80 jobs | PASS | source 400s, total 424s |

Target UT cases:

| Case | Result | Runtime |
|---|---|---|
| `DsCoordinationBackendSessionTest.RecreatedMembershipIsBlockedUntilCleanupGatePasses` | PASS | 0.06s |
| `DsCoordinationBackendSessionTest.EnsuredMembershipIsBlockedUntilCleanupGatePasses` | PASS | 0.05s |
| `TopologyEngineTest.ConsecutiveMissingLocalMemberSnapshotsKeepRejoinRequired` | PASS | 0.06s |

Target ST cases:

| Case | Result | Runtime |
|---|---|---|
| `CoordinatorBackendClusterTest.SingleWorkerCoordinatorBlinkRecoversWithoutClusterDegrade` | PASS | 22.237s |
| `CoordinatorBackendClusterTest.IsolatedWorkerRemovedThenColdRejoinsWithoutSuicide` | PASS | 37.557s |

Known non-PR blocker:

- `build.sh -b bazel -t build` fails on the current upstream BUILD graph because `//:hashring_parser_file` references `//tests/st:hashring_parser`, while `tests/st/BUILD.bazel` does not declare that target.
- This PR does not change BUILD files and validates Bazel source build with `-t off`.
- Tracking issue: `#945` for the tools packaging gap.
