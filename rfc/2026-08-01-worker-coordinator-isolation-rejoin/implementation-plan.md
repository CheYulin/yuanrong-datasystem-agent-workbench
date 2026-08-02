# Worker-Coordinator Isolation Rejoin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Measure 2 so a Coordinator-backend Worker does not self-kill during worker-coordinator isolation and safely cold-rejoins after topology removal.

**Architecture:** Reuse existing topology availability, admission, membership recreate, watch reset, `GetHashRing`, and object/stream cleanup primitives. Keep policy in existing classes with narrow hooks and no new RPC, protobuf, or v1 helper class.

**Tech Stack:** C++17, gtest, CMake, Bazel, clang-format, clang-tidy, repository-local ds-test/ds-create-pr skills, Tiantiyun remote validation.

## Global Constraints

- Baseline is `main/master@a90f6c6b718857367575068c83fb976494f6c751`.
- Never push to `openeuler/yuanrong-datasystem`; push only to verified yche/yche-huawei fork.
- CodeGraph shared index was attempted and failed with `unable to open database file`; source and BUILD/CMake evidence are authoritative.
- No production code before a failing test.
- Keep v1 within existing classes; do not add `PeerTopologyRefresher` or `WorkerRejoinCleaner`.
- Do not add RPC or protobuf fields.
- Ordinary ST cases must target less than six seconds each.
- UT should avoid sleeps and expensive operations; use fakes/injection for state matrices.
- Avoid broad formatting churn; format only touched files.
- CMake build is the primary validation path; Bazel must at least compile affected source targets.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/datasystem/cluster/runtime/topology_engine.h` | Add narrow Builder hooks and internal rejoin-required helpers if needed. |
| `src/datasystem/cluster/runtime/topology_engine.cpp` | Replace local-member-missing SIGKILL, invoke cleanup/recreate/peer-refresh policy, preserve exact-read authority. |
| `src/datasystem/cluster/coordination_backend/ds_coordination_backend.h` | Add a narrow recreate gate setter or reuse existing reconcile hook with explicit cleanup semantics. |
| `src/datasystem/cluster/coordination_backend/ds_coordination_backend.cpp` | Gate every recreated membership write and preserve watch invalidation after successful recreate. |
| `src/datasystem/worker/worker_oc_server.cpp` | Wire admission, cleanup, and peer hash-ring callbacks into `TopologyEngine::Builder`. |
| `src/datasystem/worker/object_cache/worker_oc_service_impl.h` | Declare internal cold-rejoin cleanup entry. |
| `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp` | Implement cleanup sequencing and keep `GetHashRing` as control-only observation. |
| `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.h` | Declare clear-all-local-data primitive. |
| `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp` | Implement local clear-all without topology failure cleanup or ref rebuild. |
| `src/datasystem/master/metadata_manager_holder.*` | Add or reuse a single local metadata cleanup wrapper only if needed by WorkerOCServiceImpl wiring. |
| `tests/ut/cluster/topology_engine_test.cpp` | Topology state RED/GREEN tests. |
| `tests/ut/cluster/ds_coordination_backend_session_test.cpp` | Membership recreate gate RED/GREEN tests. |
| `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp` | Cleanup and ordinary-business rejection RED/GREEN tests. |
| `tests/ut/worker/object_cache/worker_get_hash_ring_test.cpp` | Control observation tests. |
| `tests/st/worker/object_cache/coordinator_backend_cluster_test.cpp` | Short smoke coverage only. |

## Task 1: No-Kill Rejoin-Required Transition

**Files:**
- Modify: `tests/ut/cluster/topology_engine_test.cpp`
- Modify: `src/datasystem/cluster/runtime/topology_engine.cpp`
- Modify: `src/datasystem/cluster/runtime/topology_engine.h` only if a helper is needed

**Interfaces:**
- Consumes: existing `TopologyEngine::GetAvailability()`, `SetAvailability`, `PublishBackendEvidence`.
- Produces: local-member-missing transitions to closed admission without process kill.

- [ ] **Step 1: Write the failing test**

Replace `TopologyEngineDeathTest.LocalMemberRemovedFromSnapshotTriggersSigkill` with:

```cpp
TEST(TopologyEngineTest, LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill)
{
    testing::FakeCoordinatorServiceProxy proxy;
    TestWatchIngress ingress;
    NoopTopologyCallbacks callbacks;
    const std::string clusterName = "removed-local";
    auto keys = MakeKeys(clusterName);
    PutTopology(proxy, clusterName, MakeTopology());
    auto engine = BuildEngine(proxy, ingress, callbacks, clusterName);
    ASSERT_NE(engine, nullptr);
    DS_ASSERT_OK(engine->Start());

    PutTopology(proxy, clusterName, MakeTopologyWithoutLocal(2));
    DS_ASSERT_OK(EmitTopologyEvent(proxy, ingress, *keys, 2));
    ASSERT_TRUE(WaitForCondition([&engine] {
        return engine->GetAvailability() == TopologyAvailabilityLevel::ROLE_ISOLATED;
    }, TEST_WAIT));

    DS_ASSERT_OK(engine->Shutdown(std::chrono::steady_clock::now() + TEST_WAIT));
}
```

- [ ] **Step 2: Run RED**

Run:

```bash
./build/bin/ds_ut --gtest_filter=TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill
```

Expected: current code terminates with SIGKILL or the case fails before shutdown.

- [ ] **Step 3: Implement GREEN**

In `PublishBackendEvidence`, when `localMemberExisted && !localMemberWasLeaving` and the new exact snapshot lacks the
local member:

```cpp
LOG(ERROR) << "CLUSTER_LIFECYCLE cluster=" << options_.clusterName
           << " role=worker state=local_member_missing action=require_rejoin address=" << options_.localAddress;
SetAvailability(TopologyAvailabilityLevel::ROLE_ISOLATED, "local_member_missing");
return Status::OK();
```

Keep initial missing and voluntary leaving behavior unchanged.

- [ ] **Step 4: Run GREEN**

Run the same single test. Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/ut/cluster/topology_engine_test.cpp src/datasystem/cluster/runtime/topology_engine.cpp src/datasystem/cluster/runtime/topology_engine.h
git commit -m "feat(cluster): keep worker alive after topology removal"
```

## Task 2: Cleanup Gate For Membership Recreate

**Files:**
- Modify: `tests/ut/cluster/ds_coordination_backend_session_test.cpp`
- Modify: `src/datasystem/cluster/coordination_backend/ds_coordination_backend.h`
- Modify: `src/datasystem/cluster/coordination_backend/ds_coordination_backend.cpp`
- Modify: `src/datasystem/cluster/runtime/topology_engine.*`
- Modify: `src/datasystem/worker/worker_oc_server.cpp`

**Interfaces:**
- Consumes: `DsCoordinationBackend::AutoCreateKeepAliveKey(bool recreated)`.
- Produces: `SetMembershipRecreateGate(std::function<Status()>)` or equivalent narrow hook that all recreated membership writes call before Put.

- [ ] **Step 1: Write failing tests**

Add two cases:

```cpp
TEST(DsCoordinationBackendSessionTest, RecreatedMembershipIsBlockedUntilCleanupGatePasses)
{
    testing::FakeCoordinatorServiceProxy proxy;
    DsCoordinationBackend backend(&proxy, WATCHER_ADDRESS);
    std::atomic<size_t> gateCalls{ 0 };
    backend.SetMembershipRecreateGate([&gateCalls] {
        ++gateCalls;
        RETURN_STATUS(K_NOT_READY, "rejoin cleanup pending");
    });

    auto rc = backend.AutoCreateKeepAliveKey(true);
    EXPECT_EQ(rc.GetCode(), K_NOT_READY);
    EXPECT_EQ(gateCalls.load(), 1U);
}

TEST(DsCoordinationBackendSessionTest, RecreatedMembershipInvalidatesWatchesAfterCleanupGatePasses)
{
    testing::FakeCoordinatorServiceProxy proxy;
    DsCoordinationBackend backend(&proxy, WATCHER_ADDRESS);
    size_t resetCount = 0;
    backend.SetMembershipRecreateGate([] { return Status::OK(); });
    backend.SetEventHandler([&resetCount](CoordinationEvent &&event) {
        if (event.type == CoordinationEventType::RESET) {
            ++resetCount;
        }
    });

    DS_ASSERT_OK(backend.AutoCreateKeepAliveKey(true));
    EXPECT_GE(resetCount, 1U);
}
```

- [ ] **Step 2: Run RED**

Run:

```bash
./build/bin/ds_ut --gtest_filter='DsCoordinationBackendSessionTest.RecreatedMembership*'
```

Expected: compile fails because `SetMembershipRecreateGate` is missing.

- [ ] **Step 3: Implement GREEN**

Add `using MembershipRecreateGate = std::function<Status()>;`, setter, guarded field under `eventHandlerMutex_`, and
call the gate at the start of `AutoCreateKeepAliveKey(true)` before reading/writing membership. Do not gate the initial
non-recreated publish.

- [ ] **Step 4: Wire from Worker runtime**

In `WorkerOCServer::ConstructTopologyRuntime`, pass a lambda from `TopologyEngine` or backend integration that blocks
recreate while rejoin cleanup is pending. Keep the hook local to Coordinator backend.

- [ ] **Step 5: Run GREEN**

Run the two tests and the existing membership session tests. Expected PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/ut/cluster/ds_coordination_backend_session_test.cpp src/datasystem/cluster/coordination_backend/ds_coordination_backend.* src/datasystem/cluster/runtime/topology_engine.* src/datasystem/worker/worker_oc_server.cpp
git commit -m "feat(cluster): gate membership recreate on rejoin cleanup"
```

## Task 3: Local Cold-Rejoin Cleanup

**Files:**
- Modify: `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp`
- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.h`
- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`
- Modify: `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.h`
- Modify: `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp`
- Modify: `src/datasystem/master/metadata_manager_holder.*` only if existing ownership requires this wrapper

**Interfaces:**
- Consumes: `WorkerOcServiceClearDataFlow::ClearObject(const std::vector<std::string> &)`.
- Produces: `Status CleanupLocalStateForRejoin(std::chrono::steady_clock::time_point deadline)`.

- [ ] **Step 1: Write failing tests**

Add focused tests that build the existing `WorkerOcServiceImplTest` fixture:

```cpp
TEST_F(WorkerOcServiceImplTest, CleanupLocalStateForRejoinClearsAllLocalObjects)
{
    InsertObject("rejoin-a");
    InsertObject("rejoin-b");

    DS_ASSERT_OK(workerOcService_->CleanupLocalStateForRejoin(std::chrono::steady_clock::now() + TEST_WAIT));

    EXPECT_FALSE(objectTable_->Contains("rejoin-a"));
    EXPECT_FALSE(objectTable_->Contains("rejoin-b"));
}

TEST_F(WorkerOcServiceImplTest, CleanupLocalStateForRejoinReturnsDeadlineExceededWithoutReopeningService)
{
    auto rc = workerOcService_->CleanupLocalStateForRejoin(std::chrono::steady_clock::now());
    EXPECT_EQ(rc.GetCode(), K_RPC_DEADLINE_EXCEEDED);
    EXPECT_FALSE(IsHealthy());
}
```

Use existing fixture helper names if they differ; do not add sleeps.

- [ ] **Step 2: Run RED**

Run:

```bash
./build/bin/ds_ut_object --gtest_filter='WorkerOcServiceImplTest.CleanupLocalStateForRejoin*'
```

Expected: compile fails because cleanup entry is missing.

- [ ] **Step 3: Implement GREEN**

Implement cleanup as a synchronous, deadline-aware path:

```cpp
CHECK_FAIL_RETURN_STATUS(std::chrono::steady_clock::now() < deadline, K_RPC_DEADLINE_EXCEEDED,
                         "rejoin cleanup deadline exceeded");
SetTopologyServingAdmission(false);
RETURN_IF_NOT_OK(clearDataFlow_->ClearAllLocalObjectsForRejoin(deadline));
return Status::OK();
```

Add metadata cleanup only through existing per-worker primitives and without `ProcessWorkerRestart` reconciliation push.

- [ ] **Step 4: Run GREEN**

Run the focused object UT. Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp src/datasystem/worker/object_cache/worker_oc_service_impl.* src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.* src/datasystem/master/metadata_manager_holder.*
git commit -m "feat(worker): clean local state before cold rejoin"
```

## Task 4: Peer HashRing Refresh While Coordinator Is Unavailable

**Files:**
- Modify: `tests/ut/cluster/topology_engine_test.cpp`
- Modify: `tests/ut/worker/object_cache/worker_get_hash_ring_test.cpp` if control-RPC behavior needs a guard
- Modify: `src/datasystem/cluster/runtime/topology_engine.*`
- Modify: `src/datasystem/worker/worker_oc_server.cpp`

**Interfaces:**
- Consumes: existing `GetHashRing` response fields `version`, `hash_ring_changed`, and topology payload.
- Produces: a Builder-injected peer refresh hook used only during backend-unavailable refresh.

- [ ] **Step 1: Write failing tests**

Add:

```cpp
TEST(TopologyEngineTest, PeerHashRingRefreshAcceptsNewerVersionOnly)
{
    // Build engine with local version 1.
    // Inject peer refresh results: stale version 1 then newer version 2.
    // Assert only version 2 is published or recorded as peer-observed.
}

TEST(TopologyEngineTest, PeerHashRingRefreshMissingLocalMemberRequiresRejoin)
{
    // Build engine with local version 1.
    // Inject peer result version 2 whose topology lacks local address.
    // Assert availability becomes ROLE_ISOLATED with local_member_missing semantics.
}
```

Use concrete helpers from existing topology tests rather than sleeping or real RPC.

- [ ] **Step 2: Run RED**

Run the two tests. Expected FAIL or compile failure because the hook/state is missing.

- [ ] **Step 3: Implement GREEN**

In `RefreshUnavailableBackend` or `HandleBackendUnavailable`, if exact read fails with backend-access failure and a
last-good snapshot exists, call the peer refresh hook. Accept only responses whose version is greater than the current
snapshot version. If the accepted peer topology lacks the local member, reuse the rejoin-required transition. Do not
publish peer data as Coordinator authority after Coordinator exact read recovers.

- [ ] **Step 4: Run GREEN**

Run focused topology UT. Expected PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/ut/cluster/topology_engine_test.cpp tests/ut/worker/object_cache/worker_get_hash_ring_test.cpp src/datasystem/cluster/runtime/topology_engine.* src/datasystem/worker/worker_oc_server.cpp
git commit -m "feat(cluster): refresh worker topology from peers during coordinator outage"
```

## Task 5: Fast ST And Validation Evidence

**Files:**
- Modify: `tests/st/worker/object_cache/coordinator_backend_cluster_test.cpp`
- Modify: `rfc/2026-08-01-worker-coordinator-isolation-rejoin/validation.md`

**Interfaces:**
- Consumes: final behavior from Tasks 1-4.
- Produces: PR-safe evidence with command, result, and runtime per case.

- [ ] **Step 1: Add short ST cases**

Add only small Coordinator-backend smoke cases:

```cpp
TEST_F(CoordinatorBackendClusterTest, WorkerCoordinatorIsolationRemovalDoesNotKillWorker)
TEST_F(CoordinatorBackendClusterTest, RemovedWorkerColdRejoinsAfterCleanup)
```

Keep object counts tiny and avoid arbitrary sleeps. Use existing cluster wait helpers with bounded timeouts. Record each
case runtime from test output.

- [ ] **Step 2: Run focused local/remote validation**

Run local source-only checks:

```bash
git diff --check
clang-format --dry-run --Werror <touched cc h files>
clang-tidy <touched cc files> -- <compile flags from compile_commands.json>
```

Run Tiantiyun validation after pushing the branch:

```bash
python3 .skills/ds-test/scripts/ds_test.py run-remote --branch feat/worker-coordinator-isolation-rejoin --command 'DS_OPENSOURCE_DIR=<remote-third-party-cache> bash build.sh -t build'
python3 .skills/ds-test/scripts/ds_test.py run-remote --branch feat/worker-coordinator-isolation-rejoin --command 'DS_OPENSOURCE_DIR=<remote-third-party-cache> bash build.sh -t run_cases -l ut'
python3 .skills/ds-test/scripts/ds_test.py run-remote --branch feat/worker-coordinator-isolation-rejoin --command 'DS_OPENSOURCE_DIR=<remote-third-party-cache> ctest -R "CoordinatorBackendClusterTest\\.(WorkerCoordinatorIsolationRemovalDoesNotKillWorker|RemovedWorkerColdRejoinsAfterCleanup)" --output-on-failure'
bazel build //src/datasystem/cluster:all //src/datasystem/worker:all //src/datasystem/worker/object_cache:all
```

- [ ] **Step 3: Update validation.md**

Record exact commands, PASS/FAIL, and UT/ST case durations. Do not include private host, IP, account, token, or remote
absolute worktree path.

- [ ] **Step 4: Final review**

Run `ds-self-verify`, then `ds-pr-review` if available. Address high-confidence correctness, concurrency, memory,
performance, security, observability, and compatibility findings before PR.

- [ ] **Step 5: Push and create PR**

Verify remote before push:

```bash
git remote -v
git push origin feat/worker-coordinator-isolation-rejoin
python3 .skills/ds-create-pr/scripts/create_pr.py --owner openeuler --repo yuanrong-datasystem --base master --head feat/worker-coordinator-isolation-rejoin --title "feat: worker coordinator 隔离后冷重加" --body-file <pr-body>
```

Expected: PR URL returned. The PR body includes UT/ST case names and runtimes.

## Self-Review

| Check | Result |
|---|---|
| Measures covered | G1-G6 map to Tasks 1-4; ST evidence in Task 5. |
| Placeholders | No `TBD`; `<remote-third-party-cache>` is intentionally resolved at validation time from private remote environment and must not be committed as a private path. |
| Type consistency | Hook names are proposed and must be matched exactly when implemented; no later task depends on an unreviewed public API. |
| Scope | Coordinator backend first; ETCD backend parity is explicit follow-up risk. |
| Test cost | UT uses fakes; ST is capped to two small smoke cases under six seconds target. |
