# PR2056 Client Recovery Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make long-lived Clients with `enableLocalCache=true` and `false` recover after their bound Worker and a second metadata-owner Worker are isolated, without per-timeout TCP probes or ambiguous write replay.

**Architecture:** Recovery keeps the existing immediate `K_RPC_PEER_DEAD` switch. Other recovery-class direct-Get failures only coalesce a `HashRingRefresher::ForceRefresh`; a later authoritative topology update checks whether the currently bound Worker is still ACTIVE and submits the existing lifetime-safe asynchronous switch when it is not. One default four-Worker ST holds both local-cache modes in the same cluster and proves two-Worker isolation, ring convergence, rebind, and subsequent read/write recovery.

**Tech Stack:** C++17, bRPC, protobuf `ClusterTopologyPb`, GoogleTest, CMake, URMA Mock, GitCode CI.

## Global Constraints

- Baseline is PR2056 HEAD `c1146242627b1382120e59d4eac48f0f575b9f52`.
- Never push to `openeuler/yuanrong-datasystem`; push only to the verified `yche-huawei` fork PR branch.
- Do not add a per-request or per-timeout TCP probe.
- Do not treat every timeout as proof that a Worker is dead.
- Do not replay a Set Publish whose dispatch result is unknown.
- Do not change ZMQ behavior or large-scale failure-diffusion policy.
- Normal successful request paths add no RPC, socket, lock, allocation, or log.
- Keep each functional change in a separate commit; do not squash into `c114624`.
- CMake is the runtime validation authority; Bazel only needs source/build-graph closure.
- Report Coordinator isolation timing separately from Client recovery timing; target isolation is 3 seconds.

---

## File Map

- `src/datasystem/client/object_cache/object_client_impl.cpp`: classify recovery failures, request coalesced refresh, and react to authoritative ring removal.
- `src/datasystem/client/object_cache/object_client_impl.h`: private recovery helpers and generalized pending-switch naming.
- `tests/ut/client/transport_test.cpp`: deterministic status, ring-update, dedupe, stale-trigger, and shutdown tests.
- `tests/st/client/kv_cache/coordinator_active_failure_stop_resume_test.cpp`: one default two-failure/two-mode guardian.
- `.repo_context/modules/client/client-sdk.md`: effective recovery semantics and request-replay boundary.
- PR description: exact source/test line purpose, commits, timing, CMake/Bazel boundary, and remaining intentional exclusions.

### Task 1: Create the isolated implementation worktree

**Files:** None.

**Interfaces:**
- Consumes: fork ref `origin/codex/active-isolation-53c2-main` at `c1146242627b`.
- Produces: local branch `codex/pr2056-client-recovery-review-fixes` in a new worktree.

- [ ] **Step 1: Verify the fork and exact remote PR head**

Run:

```bash
git remote get-url origin
git ls-remote origin refs/heads/codex/active-isolation-53c2-main
```

Expected: `origin` is the `yche-huawei` fork and the ref resolves to `c1146242627b1382120e59d4eac48f0f575b9f52`.

- [ ] **Step 2: Create the worktree and branch**

Run:

```bash
git worktree add .worktrees/pr2056-client-recovery-fixes \
  -b codex/pr2056-client-recovery-review-fixes c1146242627b1382120e59d4eac48f0f575b9f52
```

Expected: clean worktree at the exact PR head.

### Task 2: Coalesce routing refresh after direct-Get recovery failures

**Files:**
- Modify: `src/datasystem/client/object_cache/object_client_impl.cpp`
- Modify: `src/datasystem/client/object_cache/object_client_impl.h`
- Test: `tests/ut/client/transport_test.cpp`

**Interfaces:**
- Consumes: `Routing::ForceRefresh()` and the existing metadata failure status set.
- Produces: `static bool ShouldForceRoutingRefresh(StatusCode code)` and `void ForceRoutingRefreshAfterFailure(const HostPort &, const Status &, const char *)` private helpers.

- [ ] **Step 1: Write classifier and no-refresh tests**

Add `ObjectClientTransportTest` assertions that recovery statuses
`K_RPC_UNAVAILABLE`, `K_RPC_DEADLINE_EXCEEDED`, `K_RPC_PEER_DEAD`,
`K_CLIENT_WORKER_DISCONNECT`, and `K_METADATA_OWNER_UNAVAILABLE` return true, while
`K_OK`, `K_NOT_FOUND`, `K_INVALID`, and `K_RPC_CANCELLED` return false.

- [ ] **Step 2: Build the focused UT and verify the missing helper fails compilation**

Run:

```bash
cmake --build build --target ds_ut -j16
```

Expected before implementation: compile failure naming `ShouldForceRoutingRefresh`.

- [ ] **Step 3: Implement the shared classifier**

Declare the private static helper and replace the duplicated status list in `metadataFailureHandler`. In local-cache direct Get, execute refresh logic only after `rc.IsError()` and only for the classified statuses. Keep the existing peer-dead submit after the refresh request.

The request path must remain equivalent to:

```cpp
rc = GetBuffersFromWorker(workerApi, getParam, objectBuffers);
if (rc.IsError() && ShouldForceRoutingRefresh(rc.GetCode())) {
    ForceRoutingRefreshAfterFailure(workerApi->hostPort_, rc, "direct Get");
}
if (rc.GetCode() == K_RPC_PEER_DEAD) {
    (void)SubmitUnavailableWorkerSwitch(workerApi, "direct Get peer-dead");
}
```

`ForceRoutingRefreshAfterFailure` must only load `routing_`, call the coalescing `ForceRefresh()`, and log only when a new refresh window was opened.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
cmake --build build --target ds_ut -j16
build/tests/ut/ds_ut --gtest_filter='ObjectClientTransportTest.*RoutingRefresh*' --gtest_color=no
```

Expected: all new classifier tests pass; existing HashRingRefresher coalescing tests remain unchanged.

- [ ] **Step 5: Commit the first functional change**

```bash
git add src/datasystem/client/object_cache/object_client_impl.{h,cpp} tests/ut/client/transport_test.cpp
git commit -m 'fix(client): refresh ring after direct Get recovery failures'
```

### Task 3: Switch the bound Worker only after authoritative ring removal

**Files:**
- Modify: `src/datasystem/client/object_cache/object_client_impl.cpp`
- Modify: `src/datasystem/client/object_cache/object_client_impl.h`
- Test: `tests/ut/client/transport_test.cpp`

**Interfaces:**
- Consumes: `ClusterTopologyPb::members`, current `workerApi_`, `currentNode_`, and the existing async switch pool.
- Produces: `bool SubmitUnavailableWorkerSwitch(const std::shared_ptr<IClientWorkerApi> &, const char *reason)` and `void MaybeSwitchWorkerRemovedFromRing(const ClusterTopologyPb &ring)`.

- [ ] **Step 1: Write deterministic ring-trigger tests**

Use a one-thread switch pool blocked by a promise so submission is observable before execution. Cover:

1. `routing_ == nullptr` plus an initial ring without the bound Worker leaves pending empty.
2. A published ring containing the bound address in `MembershipPb::ACTIVE` leaves pending empty.
3. A published ring missing the bound address creates exactly one pending entry.
4. Repeating the same removed ring does not enqueue a duplicate.
5. Replacing `workerApi_[currentNode_]` before execution makes the queued old-worker trigger stale.

- [ ] **Step 2: Run the focused test and confirm it fails before implementation**

```bash
cmake --build build --target ds_ut -j16
build/tests/ut/ds_ut --gtest_filter='ObjectClientTransportTest.*RingRemoval*' --gtest_color=no
```

Expected: failure because removed-ring handling is absent.

- [ ] **Step 3: Generalize the existing submit helper**

Rename `SubmitPeerDeadWorkerSwitch` and `peerDeadSwitchPending_` to unavailable-worker terminology. Preserve all existing invariants:

- compare the exact current `workerApi` instance under `switchNodeMutex_`;
- insert pending and call `Execute` while holding `asyncSwitchWorkerMutex_`;
- capture a strong `shared_ptr` in the task;
- clear pending by RAII on every task exit;
- clear pending after submit exceptions;
- drain after heartbeat producers stop and before disconnect/destruction.

- [ ] **Step 4: Add the authoritative ring check**

After `ApplyRoutingWorkerSnapshot` succeeds, call `MaybeSwitchWorkerRemovedFromRing(ring)`. It must return immediately when `routing_` is null, which is true during `InitialFetch` before `InitRouting` publishes the facade. For later versions, snapshot the current API under `switchNodeMutex_`, look up its address in `ring.members()`, and submit only when absent or non-ACTIVE.

Do not call Service Discovery, perform RPC, or wait in the ring update hook.

- [ ] **Step 5: Run lifecycle and ring tests**

```bash
cmake --build build --target ds_ut -j16
build/tests/ut/ds_ut --gtest_filter='ObjectClientTransportTest.*RingRemoval*:ObjectClientTransportTest.ShutdownWaitsForAsyncWorkerSwitchTasks' --gtest_color=no
```

Expected: ring tests and shutdown drain test all pass without hang.

- [ ] **Step 6: Commit the second functional change**

```bash
git add src/datasystem/client/object_cache/object_client_impl.{h,cpp} tests/ut/client/transport_test.cpp
git commit -m 'fix(client): switch bound worker after authoritative ring removal'
```

### Task 4: Add the default two-Worker/two-mode guardian

**Files:**
- Modify: `tests/st/client/kv_cache/coordinator_active_failure_stop_resume_test.cpp`
- Modify: `tests/st/client/kv_cache/BUILD.bazel`

**Interfaces:**
- Consumes: Coordinator Service Discovery, four-Worker external-cluster fixture, active-failure timing helpers, and the two production recovery changes.
- Produces: one non-`DISABLED_` guardian test covering both local-cache modes in one cluster.

- [ ] **Step 1: Add a four-Worker guardian fixture**

Configure four Workers with the existing 3-second online timeout. Create:

- two long-lived Clients selected onto Worker A, one with `enableLocalCache=true`, one false;
- survivor traffic Clients on Workers C and D;
- keys selected so Worker B is the pre-failure metadata owner.

Record the bound addresses before failure and assert both long-lived Clients start on Worker A through the existing switch/connection test hooks rather than reconstructing them after failure.

Add the Bazel target `coordinator_active_failure_stop_resume_test` with the source file, `tags = ["manual"]`,
`KV_COMMON_DEPS`, Coordinator discovery, client transport/routing, flags, signature, hash algorithm,
cluster-topology protobuf, and external-cluster dependencies required by direct includes. This target is compiled but not
executed by the Bazel validation boundary.

- [ ] **Step 2: Add the two-failure recovery test**

Kill A and B with a fixed zero or small deterministic gap. While isolation is pending, have survivor Clients access keys owned by A/B so failure summaries continue. Concurrently retry operations through the original two long-lived Clients.

Assert separately:

- A and B reach FAILED within the 3-second isolation target plus the existing scheduling margin;
- survivor rings exclude A/B;
- both long-lived Clients rebind away from A;
- each mode completes Set followed by Get of a newly routed key for three consecutive attempts;
- the last failure and first success timestamps are logged separately from isolation timing.

The test name must not contain `DISABLED_`.

- [ ] **Step 3: Build and run only the guardian**

```bash
cmake --build build --target ds_st_kv_cache -j16
build/tests/st/ds_st_kv_cache \
  --gtest_filter='*TwoWorker*LocalCacheModes*' \
  --gtest_color=no
```

Expected: pass on the fixed code, cover both modes without creating a new Client after kill, and print isolation/ring/client timing.

- [ ] **Step 4: Verify default discovery**

```bash
build/tests/st/ds_st_kv_cache --gtest_list_tests | grep -A3 -B3 'TwoWorker.*LocalCacheModes'
```

Expected: the test is listed without a `DISABLED_` prefix.

- [ ] **Step 5: Commit the guardian independently**

```bash
git add tests/st/client/kv_cache/coordinator_active_failure_stop_resume_test.cpp \
        tests/st/client/kv_cache/BUILD.bazel
git commit -m 'test(client): guard two-worker recovery for local-cache modes'
```

### Task 5: Document the effective recovery contract

**Files:**
- Modify: `.repo_context/modules/client/client-sdk.md`

**Interfaces:**
- Consumes: completed source behavior and verified tests.
- Produces: source-backed client recovery and replay semantics for future changes.

- [ ] **Step 1: Add a narrow recovery paragraph**

Document that peer-dead switches immediately, other recovery failures coalesce a ring refresh, authoritative removal switches the bound identity, subsequent requests recover in both local-cache modes, and ambiguous Publish is not replayed.

- [ ] **Step 2: Validate repository context**

Run:

```bash
python3 scripts/ai_context/validate_module_metadata.py
git diff --check
```

Expected: metadata validation and diff check pass.

- [ ] **Step 3: Commit documentation separately**

```bash
git add .repo_context/modules/client/client-sdk.md
git commit -m 'docs(client): record authoritative ring recovery contract'
```

### Task 6: Local CMake verification and self-review

**Files:** No intended source changes. Any actual independent defect gets its own commit.

**Interfaces:**
- Consumes: Tasks 2-5.
- Produces: local build/test evidence and a clean diff audit.

- [ ] **Step 1: Build focused CMake targets**

```bash
cmake --build build --target ds_ut ds_ut_object ds_st_kv_cache cluster_topology_contract_ut -j16
```

- [ ] **Step 2: Run focused UT**

Run:

```bash
build/tests/ut/ds_ut --gtest_filter='HashRingRefresherTest.*:ObjectClientTransportTest.*RoutingRefresh*:ObjectClientTransportTest.*RingRemoval*:ObjectClientTransportTest.ShutdownWaitsForAsyncWorkerSwitchTasks:ObjectMetadataClientTest.*' --gtest_color=no
build/tests/ut/ds_ut --gtest_filter='TopologyControlHostTest.*:CoordinatorServiceImplTest.*:TopologyControllerTest.*:TopologyEngineTest.*:DsCoordinationBackendTest.*' --gtest_color=no
```

Record exact pass counts and elapsed time for both commands.

- [ ] **Step 3: Run focused ST**

Run the new default guardian, existing peer-dead switch ST, local-cache true/false recovery ST, and the historical two-kill focused scenario with `--gtest_also_run_disabled_tests`. Record isolation, ring, and Client recovery timings separately.

- [ ] **Step 4: Check Bazel build closure without running Bazel tests**

Inspect `BUILD.bazel` direct dependencies and compile exactly these affected Bazel targets without running them:

```bash
bazel build //tests/ut/client:transport_test \
  //tests/ut/client/routing:hash_ring_refresher_test \
  //tests/st/client/kv_cache:coordinator_active_failure_stop_resume_test
```

Do not claim Bazel runtime validation.

- [ ] **Step 5: Run pre-commit verification**

Follow `.repo_context/playbooks/upkeep/ai-self-verification.md` and the repository `$ds-self-verify` skill. Verify:

- no normal hot-path network/lock/allocation addition;
- no per-timeout probe;
- async producer/drain order and lock order remain valid;
- no ambiguous write replay;
- no new status/public compatibility change;
- `git diff --check` and formatting pass;
- commit list remains functional and unsquashed.

### Task 7: Tiantiyun CMake validation

**Files:** No intended source changes.

**Interfaces:**
- Consumes: exact local branch HEAD.
- Produces: remote 80C/128G build and runtime evidence.

- [ ] **Step 1: Verify remote source destination and exact SHA**

Use the existing `tiantiyun-80c128g` workflow. Preserve remote source, Git metadata, evidence, and active builds. Confirm the remote checkout SHA after sync.

- [ ] **Step 2: Build with CMake**

Use the existing Release `WITH_TESTS` + `BUILD_WITH_URMA_MOCK` configuration and build
`ds_ut`, `ds_ut_object`, `ds_st_kv_cache`, and `cluster_topology_contract_ut` with `-j16`.
Record commands and elapsed time.

- [ ] **Step 3: Run the guardian and focused suites**

Run the same filters as Task 6. Run the new guardian five consecutive times with simultaneous two-kill and no test-process restart between individual filtered invocations. Record all five runs, not only the final pass.

- [ ] **Step 4: Audit the 3-second target**

For each run, report:

- kill issued/completed;
- each target isolation time relative to its own kill;
- survivor ring convergence relative to the final kill;
- last Client failure and first consecutive success for each local-cache mode.

Do not merge these into one latency number.

### Task 8: Push, gate, review, and respond

**Files:** PR description and GitCode discussions.

**Interfaces:**
- Consumes: verified functional commits and remote evidence.
- Produces: updated fork PR branch, green full gate, resolved review threads, and final no-new-finding ds PR review.

- [ ] **Step 1: Verify push destination and push fast-forward**

```bash
git remote get-url origin
git push origin HEAD:codex/active-isolation-53c2-main
```

Expected: verified `yche-huawei` fork and fast-forward update only.

- [ ] **Step 2: Update PR description**

Add commit-separated source/test changes, file/line purpose, CMake commands, exact pass counts, 3-second isolation measurements, Client recovery measurements, Bazel boundary, and explicit deferred items.

- [ ] **Step 3: Trigger and monitor the full gate**

Trigger one final gate after all commits are pushed. Diagnose failures from exact console output; fix only source-backed defects in new separate commits. Continue until CodeCheck, license/SCA, x86 CMake, aarch64 CMake, and configured Bazel build checks are terminal-success.

- [ ] **Step 4: Respond to review threads**

Use thread replies for every identifiable review finding. State the implementing commit, exact source behavior, and verification. Do not mark a thread resolved when only a test or comment changed.

- [ ] **Step 5: Run final ds PR review**

```bash
python3 .skills/ds-pr-review/scripts/review_pr.py prepare 2056
```

Review the exact final HEAD through all required rounds. For any high-confidence finding, create a separate fix commit, rerun validation, and reply/publish through dry-run then publish. If findings are empty, validate locally and publish no summary/noise comment.

- [ ] **Step 6: Completion audit**

Prove every explicit requirement from the RFC against source, commit history, local/remote tests, CI, PR description, and discussion state before declaring completion.
