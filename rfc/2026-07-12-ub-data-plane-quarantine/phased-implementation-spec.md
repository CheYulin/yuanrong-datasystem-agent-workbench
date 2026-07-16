# UB Fault Isolation Phased Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task.
> Each phase below has its own TDD gate, code-review gate, and acceptance gate.

**Goal:** On latest `main/master`, turn UB data-plane faults from silent
success-rate degradation into explicit, recoverable admission decisions: do not
write or migrate to a UB-faulted Worker before recovery, and fail fast with a
recognizable error when reads can only hit a UB-faulted data Worker.

**Architecture:** Keep Global Fact and Local Observation separate. Global Fact
answers membership, slot, ownership, primary copy, migration task, and Worker
self-reported UB health summary; Local Observation answers whether the current
Client/Worker process should attempt a specific UB path now. `common/rdma`
continues to expose raw URMA outcomes; object-cache and migration layers own
classification, admission, fallback policy, explicit errors, and recovery
probing.

**Tech Stack:** C++17, DataSystem Object/KV cache, URMA mock, CMake first,
Bazel fallback with `--config=urma_mock`, gtest/ctest ST/UT.

**Reference:** UB Mock usage and boundaries follow
<https://yche.me/design/urma-mock-developer-guide-20260711.html>.

## Global Constraints

- Baseline is latest `main/master` after URMA Mock merge; current observed head
  is `e5d7178ac` (`!1129 feat(urma): add opt-in URMA mock backend for local validation`).
- Work in a fresh worktree and branch from `main/master`; do not reuse the old
  `feat/urma-fake-r11-rebase` branch for implementation.
- CMake builds must follow the repository CLion remote CMake workflow first:
  use `scripts/clion_remote_build.sh` as the build/indexing reference so
  `compile_commands.json`, generated sources, and third-party include paths stay
  usable by CLion/clangd.
- Builds must reuse the DataSystem third-party cache. The current CLion script
  default is `REMOTE_THIRDPARTY=/home/ds-thirdparty-cache`, passed as
  `DS_OPENSOURCE_DIR` to `build.sh`. Do not run a clean third-party rebuild
  unless the cache is missing or proven invalid.
- Use TDD: every production behavior change starts with a failing UT/ST that is
  run and observed failing for the intended reason.
- Use SDD: dispatch independent implementation/review subagents per phase when
  execution starts; the coordinator owns cross-phase consistency.
- Commit phase progress after each independently verified phase. Commit messages
  must describe the user-visible behavior, for example
  `feat(ub): fail fast writes when peer ub admission is unavailable`, not only
  the internal class name.
- UB isolation is data-plane eligibility only. A local UB observation must not
  mark a Worker globally DOWN, remove it from membership, rewrite metadata, or
  declare an object missing.
- Default behavior after a hard UB fault is fail fast, not transparent TCP
  success. Existing TCP fallback can remain only for explicitly allowed policy
  cases and must not clear UB health.
- `ERROR 4` is a hard local URMA Write signal. `ERROR 9`, RPC timeout, and weak
  RPC failures enter suspect/probe paths and must not be promoted into hard UB
  port failure without Jetty rebuild/probe evidence.
- `K_TRY_AGAIN` from send-Jetty pool pressure is local resource pressure, not
  remote Worker UB quarantine.
- `PROBING` is not business recovery. Normal write and migration traffic remains
  blocked until probe success reaches the configured threshold.
- TCP-only / URMA-disabled deployments must keep current behavior and must not
  create UB quarantine state.
- URMA Mock is a hardware/CI substitute for URMA semantics, failure paths, and
  CI runnability. Production/Object/KV code must continue to use the
  `ds_urma_*` wrapper path and must not call mock-only provider APIs directly.
- URMA Mock tests should reuse `tests/support/datasystem/common/urma_mock/inject`
  for CQE/event/handshake faults. New mock-only control hooks belong in test
  helpers or the inject framework, not production headers.
- URMA Mock ST parallelism should stay low enough to avoid UDS base directory,
  listener, endpoint registry, and inject-state cross-test interference.

---

## Acceptance Matrix

| Acceptance item | Covered by phases | Required proof |
| ---- | ---- | ---- |
| UB write failure puts the affected admission into `UNAVAILABLE`, and later writes fail fast or reselect before real UB write | P1, P2, P6 | `PeerUbAdmissionTest`, client set/put URMA mock ST |
| ShmOnly/local write does not commit an object that other nodes cannot read when UB data plane is unavailable | P2, P6 | worker create/publish focused UT or ST with local client + remote reader |
| Default fallback does not hide hard UB faults; opt-in fallback is bounded and observable | P1, P2, P6 | fallback policy UT, client set/get ST |
| Read with only UB-faulted data Worker fails fast with recognizable data-plane status | P3, P6 | client get/direct-read ST with provider ERROR 4 |
| Read with an alternate healthy source skips the faulted source | P3, P6 | object read source-selection UT/ST |
| Worker-worker remote get and batch remote get propagate explicit provider UB status | P3, P6 | `WorkerWorkerOCServiceImpl` / batch remote get tests |
| Migration target selection, connect/create, and redirect retry skip UB-unavailable targets | P4, P6 | `migrate_data_handler_test`, `migrate_data_direct_test`, target selector UT |
| Rebalance/hash-ring stale target is checked again before execution | P4, P6 | rebalance validation focused UT/ST |
| Global Fact and Local Observation are not conflated; global recovery summary only triggers local probe | P1, P5 | admission state-machine UT |
| Recovery is probe-driven; `PROBING` blocks business writes/migration; success restores admission | P1, P5, P6 | recovery probe UT and URMA mock unavailable-to-available ST |
| etcd/TCP membership faults and UB faults are separated | P1, P5 | classifier/admission UT |
| TCP-only / URMA disabled behavior is unchanged | P6 | focused regression ST with URMA disabled |

## Phase P0: Worktree, Baseline, and Build Truth

**Purpose:** Establish a reproducible latest-main baseline and avoid editing a
dirty or stale branch.

**Files:**
- Read: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.bazelrc`
- Read: `/home/t14s/workspace/git-repos/yuanrong-datasystem/tests/ut/CMakeLists.txt`
- Read: `/home/t14s/workspace/git-repos/yuanrong-datasystem/tests/st/CMakeLists.txt`
- Read: `/home/t14s/workspace/git-repos/yuanrong-datasystem/scripts/clion_remote_build.sh`
- Read: `/home/t14s/workspace/git-repos/yuanrong-datasystem/scripts/rewrite_clion_compile_commands.py`
- Create worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ub-fault-isolation-main`

**Steps:**

- [ ] Fetch and record latest upstream head.

  Run:

  ```bash
  git -C /home/t14s/workspace/git-repos/yuanrong-datasystem fetch main master
  git -C /home/t14s/workspace/git-repos/yuanrong-datasystem log --oneline -3 main/master
  ```

  Expected: latest `main/master` includes the URMA Mock merge commit.

- [ ] Create a fresh feature worktree.

  Run:

  ```bash
  git -C /home/t14s/workspace/git-repos/yuanrong-datasystem worktree add \
    .worktrees/ub-fault-isolation-main main/master
  git -C /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ub-fault-isolation-main \
    switch -c feat/ub-fault-isolation-main
  ```

- [ ] Confirm CMake and Bazel URMA mock knobs.

  Run:

  ```bash
  rg -n "BUILD_WITH_URMA_MOCK|enable_urma_mock|USE_URMA_MOCK|urma_mock" \
    /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ub-fault-isolation-main/.bazelrc \
    /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ub-fault-isolation-main/tests \
    /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ub-fault-isolation-main/src
  ```

  Expected: CMake supports `-DBUILD_WITH_URMA_MOCK=on`; Bazel supports
  `--config=urma_mock` or `--define=enable_urma_mock=true`.

- [ ] Confirm the CLion CMake indexing path and third-party cache.

  Run:

  ```bash
  sed -n '1,120p' scripts/clion_remote_build.sh
  test -d /home/ds-thirdparty-cache
  ```

  Expected:
  - `scripts/clion_remote_build.sh` uses `REMOTE_THIRDPARTY` and exports it to
    `build.sh` through `DS_OPENSOURCE_DIR`.
  - The script pulls `compile_commands.json`, generated build sources, and
    third-party headers, then runs `scripts/rewrite_clion_compile_commands.py`.
  - `/home/ds-thirdparty-cache` exists on the build host. If the selected host
    uses a different cache path, record that exact path before building.

- [ ] Generate or refresh the CLion compile database using the repo script
  before heavy UB isolation debugging.

  Run:

  ```bash
  REMOTE_HOST=xqyun-32c32g \
  REMOTE_THIRDPARTY=/home/ds-thirdparty-cache \
  JOBS=80 \
  scripts/clion_remote_build.sh tests-index
  ```

  Expected:
  - `.clion-remote/build/compile_commands.json` exists.
  - Repository root `compile_commands.json` symlinks to the CLion remote build
    compile database.
  - Third-party include paths are rewritten for local CLion/clangd indexing.

- [ ] Run the standalone URMA Mock provider smoke suite before UB isolation
  tests.

  Run:

  ```bash
  bazel test --config=urma_mock --jobs=8 \
    //tests/ut/common/urma_mock:all \
    --test_output=errors
  ```

  Expected: provider ABI dispatch, segment import, post_send, completion, and
  inject basics pass before higher-level Object/KV scenarios are blamed.

**Exit criteria:**
- Worktree exists.
- Baseline commit is recorded in the PR notes.
- Focused build command is known before coding starts.
- CLion/clangd indexing database is generated from the same CMake/build.sh path
  used for validation, with `DS_OPENSOURCE_DIR` pointing at the shared
  third-party cache.

## Phase P1: Admission Semantics and Explicit Error Codes

**Purpose:** Define the shared state-machine and error semantics before wiring
client, worker, or migration flows.

**Files:**
- Create: `src/datasystem/common/object_cache/ub_failure_classifier.h`
- Create: `src/datasystem/common/object_cache/ub_failure_classifier.cpp`
- Create: `src/datasystem/common/object_cache/peer_ub_admission.h`
- Create: `src/datasystem/common/object_cache/peer_ub_admission.cpp`
- Modify: `src/datasystem/common/object_cache/CMakeLists.txt`
- Modify: `include/datasystem/utils/status.h`
- Modify: `src/datasystem/common/util/status_code.def`
- Test: `tests/ut/common/object_cache/peer_ub_admission_test.cpp`
- Test: `tests/ut/common/object_cache/ub_failure_classifier_test.cpp`

**Interfaces:**

```cpp
enum class UbAdmissionState { AVAILABLE, SUSPECT, UNAVAILABLE, PROBING };
enum class UbOperationKind {
    CLIENT_PUT,
    CLIENT_GET_WRITEBACK,
    WORKER_REMOTE_GET_WRITEBACK,
    MIGRATION_DIRECT_READ,
    MIGRATION_WRITE,
    REBALANCE_WRITE,
    RECOVERY_PROBE,
};
enum class UbFailureClass {
    SUCCESS,
    PORT_UNAVAILABLE_ERROR4,
    TIMEOUT_SUSPECT,
    CONNECT_OR_PATH_FAILURE,
    LOCAL_RESOURCE_PRESSURE,
    NON_UB_FAILURE,
};

struct UbOpOutcome {
    HostPort peer;
    UbOperationKind op;
    Status status;
    std::optional<int> providerStatus;
    std::optional<int> cqeStatus;
    uint64_t payloadSize = 0;
    std::string learnedFrom;
};

class UbFailureClassifier {
public:
    UbFailureClass Classify(const UbOpOutcome &outcome) const;
};

class PeerUbAdmission {
public:
    Status CheckWriteTarget(const HostPort &peer, UbOperationKind op) const;
    Status CheckReadSource(const HostPort &peer) const;
    bool IsReachable(const HostPort &peer, UbOperationKind op) const;
    std::vector<HostPort> FilterReachable(
        const std::vector<HostPort> &peers, UbOperationKind op) const;
    void ReportOutcome(const UbOpOutcome &outcome);
    void ApplyGlobalSummary(const HostPort &peer, bool writable, uint64_t epoch);
    void MarkProbeStart(const HostPort &peer);
    void MarkProbeSuccess(const HostPort &peer);
    void MarkProbeFailure(const HostPort &peer, const Status &status);
};
```

**TDD checklist:**

- [ ] `ERROR 4` outcome moves peer to `UNAVAILABLE` immediately.
- [ ] `ERROR 9` / `K_URMA_WAIT_TIMEOUT` moves peer to `SUSPECT`, not hard
  `UNAVAILABLE`, until probe/rebuild evidence is reported.
- [ ] `K_TRY_AGAIN` is classified as `LOCAL_RESOURCE_PRESSURE` and does not
  quarantine the peer.
- [ ] `CheckWriteTarget` and `CheckMigrationTarget` return
  `K_URMA_WORKER_UNAVAILABLE` or the chosen equivalent when peer is
  `UNAVAILABLE` or `PROBING`.
- [ ] `CheckReadSource` returns `K_URMA_DATA_WORKER_UNAVAILABLE` or the chosen
  equivalent when read has no healthy alternative.
- [ ] A newer global available summary moves local hard failure only to
  `PROBING`; it does not jump directly to `AVAILABLE`.
- [ ] Probe success threshold restores `AVAILABLE`; probe failure applies
  backoff and keeps business traffic blocked.

**Exit criteria:**
- Focused UTs fail before implementation and pass after implementation.
- Status names and messages are grep-able in logs and RPC response conversion.
- No client/worker/migration production flow is changed in this phase.
- Commit after verification, with a behavior-oriented message such as
  `feat(ub): add admission states for fail-fast ub isolation`.

## Phase P2: Client Write and Fallback Gate

**Purpose:** Stop client-side Put/Set/MSet from silently succeeding through TCP
after a hard UB write fault, while preserving explicitly allowed fallback cases.

**Files:**
- Modify: `src/datasystem/client/transport/data_plane/ub_transporter.cpp`
- Modify: `src/datasystem/client/transport/data_plane/ub_transporter.h`
- Modify: `src/datasystem/client/object_cache/client_worker_api/client_worker_remote_api.cpp`
- Modify: `src/datasystem/common/object_cache/urma_fallback_tcp_limiter.cpp`
- Modify: `src/datasystem/common/object_cache/urma_fallback_tcp_limiter.h`
- Test: `tests/ut/common/object_cache/urma_fallback_tcp_limiter_test.cpp`
- Test: `tests/ut/client/transport_test.cpp`
- ST: `tests/st/client/kv_cache/kv_client_transport_set_test.cpp`

**Interfaces consumed:** `PeerUbAdmission::CheckWriteTarget`,
`PeerUbAdmission::ReportOutcome`, `UbFailureClassifier::Classify`.

**Behavior:**
- Before client UB write, check target admission.
- If UB write returns hard fault, report outcome and stop default TCP payload
  fallback for that operation.
- If fallback policy explicitly allows fallback and payload is within the cap,
  allow TCP payload but still record the UB fault.
- Future writes to the same blocked admission fail fast before real UB write.

**TDD checklist:**

- [ ] With admission `UNAVAILABLE`, `UbTransporter::Set` returns the explicit
  unavailable status before calling the URMA write path.
- [ ] With URMA mock injecting ERROR 4, first write records the failure and the
  next write does not enter real UB write.
- [ ] Fallback limiter rejects hard UB fault by default.
- [ ] Fallback limiter still preserves existing size/pending-byte rejection
  behavior for non-hard-fault policy paths.
- [ ] URMA disabled / TCP-only path bypasses UB admission and remains unchanged.

**Exit criteria:**
- Client write focused UT passes.
- A mock-backed ST demonstrates fault period write blocking and post-recovery
  write success after P5 is implemented.
- Commit after verification, with a behavior-oriented message such as
  `feat(ub): block client writes after hard ub faults`.

## Phase P3: Read, Direct Read, and RemoteGet Explicit Provider Status

**Purpose:** Make reads distinguish “object missing” from “data exists but the
provider's UB data plane cannot write back now.”

**Files:**
- Modify: `src/datasystem/client/object_cache/client_worker_api/client_worker_remote_api.cpp`
- Modify: `src/datasystem/client/transport/object_read/direct_read_flow.cpp` or the current direct-read data phase file on latest `main/master`
- Modify: `src/datasystem/client/transport/object_read/client_remote_data_transport.cpp` or the current transport adapter file on latest `main/master`
- Modify: `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp`
- Modify: `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp`
- Modify: `src/datasystem/protos/worker_object.proto` only if existing `last_rc` fields cannot carry structured provider status
- Test: `tests/ut/worker/object_cache/worker_worker_oc_api_test.cpp`
- Test: `tests/ut/worker/object_cache/worker_worker_oc_gather_layout_test.cpp`
- ST: `tests/st/client/kv_cache/kv_client_transport_get_test.cpp`

**Interfaces consumed:** `PeerUbAdmission::CheckReadSource`,
`PeerUbAdmission::ReportOutcome`, explicit status codes from P1.

**Behavior:**
- Requesters filter known-unhealthy data source Workers before RemoteGet.
- Provider Workers report explicit UB writeback status in RPC response when
  `GetRequest::UbWriteHelper` or worker-worker writeback sees a hard UB fault.
- Requesters update local admission only from explicit provider status or true
  local operator outcome; RPC timeout remains weak suspect information.
- If all data sources are blocked, return explicit UB data-plane unavailable
  status quickly.

**TDD checklist:**

- [ ] Provider ERROR 4 is serialized into existing or new response status and
  observed by requester.
- [ ] Requester receiving explicit provider UB status records the source as
  unavailable for read admission.
- [ ] Requester RPC timeout does not create `PORT_UNAVAILABLE_ERROR4`.
- [ ] Batch remote get marks only affected keys/source groups as failed with
  recognizable data-plane status.
- [ ] When one healthy replica remains, source selection skips the faulted
  Worker and reads from the healthy one.

**Exit criteria:**
- Focused worker-worker UT passes.
- Client get/direct-read ST shows fail-fast on single faulted source and
  source switching when an alternate healthy source exists.
- Commit after verification, with a behavior-oriented message such as
  `feat(ub): return explicit read errors for unavailable data providers`.

## Phase P4: Migration, Rebalance, and Hash-Ring Task Admission

**Purpose:** Prevent migration and rebalance from repeatedly choosing or
executing UB-unavailable targets.

**Files:**
- Modify: `src/datasystem/worker/object_cache/data_migrator/handler/migrate_data_handler.cpp`
- Modify: `src/datasystem/worker/object_cache/data_migrator/*` target connection files on latest `main/master`
- Modify: `src/datasystem/worker/object_cache/rebalance_candidate_provider.cpp`
- Modify: `src/datasystem/worker/object_cache/service/worker_oc_service_migrate_impl.cpp`
- Modify: `src/datasystem/cluster/executor/topology_task_executor.cpp` if this is the current rebalance task execution gate
- Test: `tests/ut/worker/object_cache/migrate_data_handler_test.cpp`
- Test: `tests/ut/worker/object_cache/migrate_data_direct_test.cpp`
- ST: migration/rebalance focused ST selected after latest-main trace

**Interfaces consumed:** `PeerUbAdmission::CheckMigrationTarget`,
`PeerUbAdmission::FilterReachable`, P1 status codes.

**Behavior:**
- Candidate target selection filters UB-unavailable Workers.
- `ConnectAndCreateRemoteApi` or equivalent target RPC setup performs a second
  admission check to catch stale selections.
- Rebalance/hash-ring task execution checks target admission before moving data.
- Direct migration read failures report source/target outcomes without claiming
  object absence.

**TDD checklist:**

- [ ] Target selector excludes `UNAVAILABLE` and `PROBING` targets.
- [ ] Stale target that becomes unavailable after selection fails fast at
  execution.
- [ ] Redirect retry does not reselect the same UB-unavailable target.
- [ ] Direct migration read ERROR 4 produces a data-plane status and records an
  admission outcome instead of only per-object generic failure.

**Exit criteria:**
- Focused migration UT passes.
- Rebalance/hash-ring task admission has a targeted regression.
- No metadata ownership rewrite is introduced by UB-local observations.
- Commit after verification, with a behavior-oriented message such as
  `feat(ub): skip migration targets with unavailable ub admission`.

## Phase P5: Local/Global State Boundary and Probe Recovery

**Purpose:** Add the recoverability path and the minimum global health summary
needed to stop other components from repeatedly hitting a self-reported bad
Worker, without broadcasting a full peer matrix.

**Files:**
- Create or modify: `src/datasystem/common/object_cache/ub_recovery_probe.*`
- Modify: worker health/heartbeat/resource report files identified by latest-main trace
- Modify: client-worker heartbeat or response path only if it already carries a suitable status summary
- Test: `tests/ut/common/object_cache/peer_ub_admission_test.cpp`
- Test: focused worker health summary UT chosen after trace

**Interfaces consumed/produced:**
- Consumes P1 `PeerUbAdmission`.
- Produces a Worker-owned `UbHealthSummary` with `worker`, `writable`, `epoch`,
  `state`, and `reason` fields if latest-main has a natural proto/reporting
  hook.

**Behavior:**
- Worker self-observed hard UB fault updates LocalUbHealth and bumps an epoch.
- Global summary is Worker-owned self health, not peer-to-peer matrix.
- Consumers apply global unavailable summary as a block.
- Consumers apply global available summary as a reason to probe; local hard
  failures are not cleared without local probe success.
- Probe success N times restores admission; probe failure increases backoff.

**TDD checklist:**

- [ ] Self ERROR 4 generates a higher epoch unavailable summary.
- [ ] Applying unavailable summary blocks write/migration admission.
- [ ] Applying available summary to a locally failed peer enters `PROBING`, not
  `AVAILABLE`.
- [ ] Business write/migration remains blocked in `PROBING`.
- [ ] Probe success threshold opens admission; fallback success does not.

**Exit criteria:**
- Recovery is deterministic in unit tests.
- If global health publication is too large for the first PR, the phase is
  split: P5a local probe recovery in first PR, P5b health summary publication in
  follow-up PR. P5a must still prove recoverability for the process that
  observed the fault.
- Commit after verification, with a behavior-oriented message such as
  `feat(ub): restore admission only after successful ub probes`.

## Phase P6: End-to-End URMA Mock Scenarios and Regression Suite

**Purpose:** Prove the staged implementation matches the user-facing acceptance
features under URMA mock fault injection.

**Files:**
- Modify or add ST under `tests/st/client/kv_cache/`
- Modify or add ST under `tests/st/client/object_cache/`
- Modify or add ST under `tests/st/worker/object_cache/` if a worker migration
  ST hook exists on latest `main/master`
- Use: `tests/support/datasystem/common/urma_mock/inject/fault_inject.*`

**URMA Mock boundaries:**
- Mock replaces `liburma.so`/RNIC dependencies so Object/KV tests can still
  exercise official URMA-shaped `ds_urma_*` wrappers.
- Object/KV isolation tests should inject failures at CQE/event/handshake or
  post-send behavior through the existing inject framework.
- Do not add production-path dependencies on mock endpoint, UDS, memfd, or
  provider internals.
- If ST fails intermittently, first inspect `URMA_MOCK_UDS_BASE_DIR`, listener
  lifecycle, endpoint registry contents, and inject-state cleanup before
  changing isolation logic.

**Scenarios:**

1. Client Put/Set to Worker A: mock ERROR 4, then sustained unavailable, then
   recover.
   - Fault period: first request returns explicit UB data-plane status, later
     requests fail fast before real UB write.
   - Recovery: probe succeeds; subsequent Put/Set succeeds through UB.

2. Client Get / direct read from Worker B: provider writeback ERROR 4.
   - Single source: Get returns recognizable UB data-provider unavailable
     status quickly.
   - Two sources: faulted source skipped, healthy source succeeds.

3. Worker remote get / batch remote get: provider returns explicit UB status.
   - Batch result reports affected keys without waiting for broad timeout.
   - Requester does not turn RPC timeout into hard UB ERROR 4.

4. Migration/Rebalance target Worker T unavailable.
   - Target selection skips T.
   - If task was already issued, worker execution fail-fasts before data move.
   - Recovery probe later makes T eligible again.

5. Regression boundaries.
   - TCP-only / URMA disabled path unchanged.
   - `K_TRY_AGAIN` local pressure does not quarantine remote Worker.
   - etcd/TCP membership failure tests remain under existing isolation logic.

**Verification commands:**

```bash
REMOTE_HOST=xqyun-32c32g \
REMOTE_THIRDPARTY=/home/ds-thirdparty-cache \
JOBS=80 \
scripts/clion_remote_build.sh tests-index

bazel test --config=urma_mock --jobs=8 \
  //tests/ut/common/urma_mock:all \
  --test_output=errors

DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache \
cmake --build . --target datasystem_static ds_ut -j80
ctest --timeout 240 --parallel 8 -L ut -R "ub|Ub|URMA|Urma|migrate|remote_get" --output-on-failure
DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache \
cmake --build . --target datasystem_worker_bin ds_st_object_cache ds_st_kv_cache -j80
CTEST_OUTPUT_ON_FAILURE=1 ctest \
  --timeout 900 \
  --parallel 8 \
  -L st \
  -R "Urma|URMA|Rdma|RDMA|kv_client_urma|transport_get|transport_set|migrate|rebalance" \
  --output-on-failure
```

Bazel fallback when CMake iteration becomes too slow:

```bash
bazel test --config=urma_mock //tests/ut/... --test_filter='*Ub*:*URMA*:*Urma*'
bazel test --config=urma_mock --jobs=8 \
  //tests/st/client/object_cache:urma_object_client_test \
  //tests/st/client/kv_cache:kv_client_urma_failover_test \
  --test_output=errors
```

**Exit criteria:**
- All P6 scenarios pass.
- The PR description lists exact pass counts and any skipped suites with
  reasons.
- If a build or test fails due to implementation, fix it before PR creation.
- If failure is environmental, capture the command, error, and why it is not
  caused by this change.
- `compile_commands.json` remains generated from the CLion CMake build path
  after final validation.
- Commit after verification, with a behavior-oriented message such as
  `test(ub): cover unavailable-to-available ub mock scenarios`.

## Phase P7: PR Creation and Review Package

**Purpose:** Finish with a reviewable branch whose description matches the
acceptance features, not only the code components.

**Files:**
- Read: `.gitee/PULL_REQUEST_TEMPLATE/PULL_REQUEST_TEMPLATE.zh-cn.md`
- Use: repo-local `ds pr create` or `.skills/ds-create-pr/scripts/create_pr.py`

**PR content requirements:**

- Baseline commit from P0.
- Summary by user-facing behavior:
  - write/migration blocked during UB fault,
  - read fail-fast with recognizable status,
  - local/global boundary,
  - probe recovery.
- Acceptance matrix copied from this spec with pass/fail notes.
- Verification commands and exact results.
- Known follow-ups, especially if P5 global summary publication is split.

**Exit criteria:**
- Branch is pushed.
- PR is created against the intended GitCode namespace/base.
- PR body includes the validation evidence.
- The commit series shows phase progress with behavior-oriented messages and no
  unrelated worktree changes.

## SDD Execution Ledger Template

Use this ledger while executing:

| Phase | Implementer | Reviewer | Red test observed | Green tests | Commit | Notes |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| P0 | main coordinator | main coordinator | n/a | baseline build command checked |  |  |
| P1 | subagent | subagent reviewer |  |  |  |  |
| P2 | subagent | subagent reviewer |  |  |  |  |
| P3 | subagent | subagent reviewer |  |  |  |  |
| P4 | subagent | subagent reviewer |  |  |  |  |
| P5 | subagent | subagent reviewer |  |  |  |  |
| P6 | subagent/main coordinator | final reviewer |  |  |  |  |
| P7 | main coordinator | main coordinator | n/a | PR created |  |  |

## Spec Coverage Check

| Story requirement | Plan coverage |
| ---- | ---- |
| Avoid silent UB failures | P1-P6 explicit status, admission, and fallback policy |
| Fault Worker is not written to before recovery | P1, P2, P4, P5, P6 |
| Fault Worker is not migrated to before recovery | P4, P5, P6 |
| Read hits on faulted data Worker fail fast | P3, P6 |
| Local and global state are both considered | P1, P5 |
| Recovery is supported | P1, P5, P6 |
| URMA Mock unavailable-to-available cases are covered | P6 |
| CMake path is primary, Bazel can be used later | P0, P6 |
| Build failures are fixed autonomously before PR | P6 |
| PR is created with repo workflow after validation | P7 |

## References

- [UB Mock Developer Guide](https://yche.me/design/urma-mock-developer-guide-20260711.html)
- [UB Fault Isolation Design](https://yche.me/design/ds-worker-isolation-ub-tcp-boundary-20260716.html)
