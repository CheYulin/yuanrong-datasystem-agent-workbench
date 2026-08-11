# OS-Suspended Worker Recovery ST Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the synthetic Worker1 lease/peer fault with an OS `SIGSTOP`/`SIGCONT` scenario and prove both bounded request rejection and cold-rejoin recovery.

**Architecture:** Keep the existing two-Worker external-ETCD ST and production fix unchanged. The fixture owns only a suspended PID flag for teardown safety; the test freezes Worker1, checks a 2s-budget request fails, waits for authoritative removal, resumes the process, and verifies re-admission plus cross-Worker metadata access.

**Tech Stack:** C++17, GoogleTest, POSIX signals, DataSystem ExternalCluster, CMake/CTest, Bazel, Tiantiyun.

## Global Constraints

- Modify no production source and add no public test-framework API.
- Preserve measures-two semantics: RECOVERING remains isolated; cleanup precedes READY; ACTIVE topology precedes service.
- Run builds only on Tiantiyun with `/home/ds-thirdparty-cache`, `URMA_MOCK`, tmux, and `-j80` when idle or `-j40` otherwise.
- Keep the DataSystem branch at one commit before updating PR #1981; push only to the verified yche-huawei fork.

---

### Task 1: Replace the focused ST fault model

**Files:**
- Modify: `tests/st/client/kv_cache/kv_client_etcd_dfx_test.cpp:221-285`

**Interfaces:**
- Consumes: `ExternalCluster::GetWorkerPid(uint32_t) const`, POSIX `kill(pid_t, int)`, `InitTestKVClient(..., requestTimeoutMs)`.
- Produces: the existing `LEVEL1_WorkerEtcdReconnectColdRejoinsAndRestoresMetadataAccess` test with OS suspension coverage.

- [x] Add `<csignal>` and fixture members `pid_t suspendedWorkerPid_{ -1 }` plus `bool workerSuspended_{ false }`.
- [x] Replace inject cleanup in `TearDown()` with best-effort `SIGCONT` when `workerSuspended_` is true.
- [x] Remove the four Worker1 inject actions and retain only the short heartbeat/dead-node flags needed by the test.
- [x] Initialize Worker1's client with a 2,000ms request timeout and prove one baseline Set/Get succeeds.
- [x] Send `SIGSTOP`, start a new Set mapped to Worker1 before topology removal, assert error status, and assert elapsed time is below 6 seconds.
- [x] Wait until authoritative topology removes Worker1 and prove Worker0 still completes Set/Get.
- [x] Send `SIGCONT`, clear the fixture suspension flag, wait for two ACTIVE members and drained tasks, then prove Worker1 Set plus Worker0 Get succeeds.
- [x] Run clang-format on the changed test file and verify `git diff --check`.

### Task 2: TDD RED and GREEN evidence

**Files:**
- Test: `tests/st/client/kv_cache/kv_client_etcd_dfx_test.cpp`

**Interfaces:**
- Consumes: the Task 1 test-only patch and master `a222c258897725588962f33a1239855b4e2f5e35`.
- Produces: persisted RED/GREEN command, result, and timing evidence.

- [x] On Tiantiyun, apply only the Task 1 test delta to a clean master worktree and build the focused ST target.
- [x] Run the focused ST and verify RED because Worker1 remains removed/isolated after `SIGCONT`, not because of signal permission, setup, or timeout plumbing.
- [x] Build the rebased PR worktree with the same cached dependencies and `URMA_MOCK` settings.
- [x] Run the focused ST and verify request rejection is under 6 seconds and the full case is GREEN.

### Task 3: Regression and delivery

**Files:**
- Modify: `rfc/2026-08-10-etcd-membership-lease-rebind-recovery/detailed-design.md`
- Modify: `rfc/2026-08-10-etcd-membership-lease-rebind-recovery/validation-report.md`
- Modify: PR #1981 description and Issue #1027 progress comment.

**Interfaces:**
- Consumes: exact-HEAD build/test markers and individual case timings.
- Produces: one reviewed DataSystem commit and durable validation evidence.

- [ ] Run 116 topology/coordination UTs, the focused ST, and the three existing coordinator recovery STs on Tiantiyun.
- [ ] Build `//src/datasystem/cluster:cluster_topology` and `//tests/st/client/kv_cache:kv_client_etcd_dfx_test` with Bazel `--config=release --config=test --config=urma_mock`.
- [x] Update the RFC and validation report with RED/GREEN evidence, request-failure bound, total case timing, and residual boundary that `SIGSTOP` is broader than a network-only fault.
- [ ] Re-run `ds-pr-review prepare 1981` and sensitive scan.
- [ ] Create a pre-squash backup, squash DataSystem changes to one intent-clear commit, verify tree/status/remote URLs, and force-with-lease push only to yche-huawei.
- [ ] Update PR #1981 and Issue #1027; trigger `/retest` only after exact pushed-head validation and verify the CI bot response.
