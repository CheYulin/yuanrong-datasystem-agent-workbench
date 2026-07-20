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
- Minimize modifications outside `src/datasystem/worker`, `src/datasystem/common/kvstore/etcd`,
  `src/datasystem/cluster/coordination_backend`, and their focused tests.
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
- `src/datasystem/cluster/coordination_backend/etcd_coordination_backend.cpp`: make callback clearing role-scoped or no-op for controller-only shutdown.
- `tests/ut/cluster/coordination_backend_contract_test.cpp`: prove controller shutdown does not clear worker callbacks.
- Existing hot-path files such as `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`,
  `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp`, and
  `src/datasystem/worker/worker_service_impl.cpp`.

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

## Task 4: Role-Scoped Coordination Backend Callback Lifecycle

**Files:**

- Modify: `src/datasystem/cluster/coordination_backend/etcd_coordination_backend.cpp`
- Modify: `src/datasystem/cluster/coordination_backend/etcd_coordination_backend.h`
- Modify: `src/datasystem/common/kvstore/etcd/etcd_store.h`
- Modify: `tests/ut/cluster/coordination_backend_contract_test.cpp`

**Interfaces:**

- Consumes: existing `EtcdStore::SetLocalIsolationHandler()` and `SetLocalRecoveryHandler()`.
- Produces:
  - `enum class CoordinationBackendRole { MEMBER = 0, CONTROLLER };`
  - `explicit EtcdCoordinationBackend(EtcdStore *etcdStore);`
  - `EtcdCoordinationBackend(EtcdStore *etcdStore, CoordinationBackendRole role);`
  - `bool EtcdStore::HasLocalIsolationHandlerForTest() const;`
  - `bool EtcdStore::HasLocalRecoveryHandlerForTest() const;`
  - Worker/member shutdown may clear keepalive callbacks; controller-only shutdown may not.

- [ ] **Step 1: Write failing lifecycle UT**

Add test:

```cpp
TEST(CoordinationBackendContractTest, ControllerShutdownDoesNotClearWorkerKeepAliveHandlers)
{
    EtcdStore store("127.0.0.1:2379");
    store.SetLocalIsolationHandler([](const Status &) {});
    store.SetLocalRecoveryHandler([] {});

    EtcdCoordinationBackend controllerBackend(&store, CoordinationBackendRole::CONTROLLER);
    EXPECT_TRUE(controllerBackend.ShutdownEventSources().IsOk());

    EXPECT_TRUE(store.HasLocalIsolationHandlerForTest());
    EXPECT_TRUE(store.HasLocalRecoveryHandlerForTest());
}
```

Add `HasLocalIsolationHandlerForTest()` and `HasLocalRecoveryHandlerForTest()` as public methods compiled only when
`WITH_TESTS` is defined. They return whether the corresponding `std::function` is non-empty and do not invoke callbacks.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
time ./build/tests/ut/cluster_topology_contract_ut --gtest_filter='CoordinationBackendContractTest.ControllerShutdownDoesNotClearWorkerKeepAliveHandlers'
```

Expected: FAIL because controller shutdown currently clears callbacks.

- [ ] **Step 3: Implement minimal role scoping**

Add a role flag to `EtcdCoordinationBackend`. `ShutdownEventSources()` clears local isolation/recovery handlers only for
the Worker/member backend role. Avoid creating new topology ownership paths.

- [ ] **Step 4: Run focused cluster UT and record time**

Run:

```bash
time ./build/tests/ut/cluster_topology_contract_ut --gtest_filter='*CoordinationBackend*:*TopologyRuntimeComposition*'
```

Expected: PASS. Record elapsed time and added case count.

- [ ] **Step 5: Commit**

```bash
git add src/datasystem/cluster/coordination_backend/etcd_coordination_backend.* \
        src/datasystem/common/kvstore/etcd/etcd_store.h \
        tests/ut/cluster/coordination_backend_contract_test.cpp
git commit -m "fix(cluster): keep worker isolation callbacks across controller shutdown"
```

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

- [ ] **Step 1: Write failing facade UT**

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

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
time ./build/tests/ut/ds_ut --gtest_filter='WorkerAdmissionFacadeTest.*'
```

Expected: build fails because facade does not exist.

- [ ] **Step 3: Implement facade**

Wrap existing `WorkerServiceAdmission`. `TryAcquireNormalGuard()` first acquires `WorkerRuntimeStateReadGuard`, then
runs `WorkerServiceAdmission::Check(snapshot, WorkerAdmissionKind::NORMAL_WRITE, operation)` against the guarded
snapshot. Return empty optional on failure.

- [ ] **Step 4: Integrate lowest-risk object hot paths**

Replace scattered snapshot-only checks only where they guard Object and base Worker critical business sections. Keep
operation behavior unchanged and do not modify stream/KV source files in this task.

- [ ] **Step 5: Run focused UT and record time**

Run:

```bash
time ./build/tests/ut/ds_ut --gtest_filter='WorkerAdmissionFacadeTest.*:WorkerServiceAdmissionTest.*:WorkerRuntimeStateTest.*'
time ./build/tests/ut/ds_ut_object --gtest_filter='WorkerOCServiceImplTest.*Admission*:WorkerWorkerOCServiceImplTest.*Admission*'
```

Expected: PASS. Record elapsed time and added case count.

- [ ] **Step 6: Commit**

```bash
git add src/datasystem/worker/worker_admission_facade.* \
        src/datasystem/worker/object_cache/worker_oc_service_impl.cpp \
        src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp \
        src/datasystem/worker/worker_service_impl.cpp \
        src/datasystem/worker/CMakeLists.txt src/datasystem/worker/BUILD.bazel \
        tests/ut/worker/worker_admission_facade_test.cpp tests/ut/worker/BUILD.bazel
git commit -m "refactor(worker): add admission facade for self-healing paths"
```

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

- [ ] **Step 1: Audit stream/KV worker-side entry points**

Run:

```bash
rg -n "CheckRuntimeAdmission|WorkerServiceAdmission|RegisterService|Status .*\\(" src/datasystem/worker/stream_cache src/datasystem/worker src/datasystem/common/kvstore
```

Expected: a short list of worker-side stream/KV business entry points and current admission coverage.

- [ ] **Step 2: Pick one scope decision**

Update `cluster-boundary-review-20260720.md` with these follow-up IDs:

- `CB-06-SC-01`: Stream worker client-facing business RPC rejects normal traffic during `LOCAL_ISOLATED`.
- `CB-06-SC-02`: Stream worker client-facing business RPC rejects normal traffic during `RECOVERING`.
- `CB-06-KV-01`: KV worker-facing normal request rejects during `LOCAL_ISOLATED`.
- `CB-06-KV-02`: KV worker-facing normal request rejects during `RECOVERING`.

- [ ] **Step 3: Run docs/self-check**

Run:

```bash
rg -n "CB-06|Stream|KV|admission" /home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-07-15-worker-isolation-self-healing
```

Expected: CB-06 is explicit and no longer ambiguous.

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
