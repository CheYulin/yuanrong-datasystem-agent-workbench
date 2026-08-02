# Validation Ledger

Baseline: `main/master@a90f6c6b718857367575068c83fb976494f6c751`.

## Pre-Edit Checks

| Command | Result |
|---|---|
| `git status --short --branch` in source worktree | clean on `feat/worker-coordinator-isolation-rejoin...main/master` |
| `git rev-parse HEAD` in source worktree | `a90f6c6b718857367575068c83fb976494f6c751` |
| `git remote -v` in main repo | `origin` is yche-huawei fork; `main` is openeuler upstream |
| `python3 .skills/ds-test/scripts/ds_test.py check-config` | OK, configured target `tiantiyun-80c128g`, private details redacted |

## CodeGraph

| Command | Result |
|---|---|
| `timeout 30s /home/t14s/.local/bin/codegraph status` | failed: `unable to open database file` |
| `timeout 30s /home/t14s/.local/bin/codegraph query TopologyEngine` | failed: `unable to open database file` |
| `timeout 30s /home/t14s/.local/bin/codegraph callers TopologyEngine::PublishBackendEvidence` | failed: `unable to open database file` |
| `timeout 30s /home/t14s/.local/bin/codegraph impact src/datasystem/cluster/runtime/topology_engine.cpp` | failed: `unable to open database file` |
| `timeout 30s /home/t14s/.local/bin/codegraph affected src/datasystem/cluster/runtime/topology_engine.cpp` | failed: `unable to open database file` |

## Evidence

| Gate | Command | Status | Runtime |
|---|---|---|---|
| RED UT | `./build/bin/ds_ut --gtest_filter=TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill` | blocked locally before build: `./build/bin/ds_ut` missing; first remote compile failed before production fix because `WaitForCondition` was unavailable in `topology_engine_test.cpp` | n/a |
| GREEN UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter=TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill` | PASS on `tiantiyun-80c128g` | gtest 3 ms, wall 0.04 s |
| Membership gate UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="DsCoordinationBackendSessionTest.RecreatedMembership*"` | PASS, 2 tests | gtest 0 ms, wall 0.03 s |
| Object cleanup UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.CleanupLocalStateForRejoin*"` | PASS, 3 tests | gtest 4 ms; cases 2 ms, 0 ms, 0 ms |
| Peer hash-ring UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerWorkerOcApiTest.RemoteHashRingRefreshRequiresInitializedSession:WorkerGetHashRingTest.*"` | PASS, 4 tests | gtest 0 ms; cases 0 ms, 0 ms, 0 ms, 0 ms |
| Coordinator smoke ST | `./build/tests/st/ds_st_coordinator_backend_manual --gtest_filter="CoordinatorBackendClusterTest.RestartWorkerPropagatesTopologyByCoordinatorWatch:CoordinatorBackendClusterThreeWorkerTest.GracefulWorkerExitKeepsExistingKeysReadable"` | PASS, 2 existing smoke cases. These are not suitable for the ordinary <6s ST gate because process startup dominates. | 21.696 s; 26.153 s |
| CMake build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache bash build.sh -t build -P off -X off -j 80 -i on` in remote `tmux` session `ds-m2-final-cmake` | PASS by log: source, example, and final `build datasystem success`. The first helper run exposed a `ds-test` zsh `status` readonly variable issue, then validation used a bash/tmux wrapper. | total 470 s; source 387 s; example 3 s |
| Bazel build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache bash build.sh -b bazel -P off -X off -j 80 -i on` in remote `tmux` session `ds-m2-final-bazel` | PASS, `rc=0`; `Build completed successfully`, `bazel install done`, `build datasystem (bazel) success` | total 458 s; source 430 s |
| Format | `git clang-format main/master --diff -- src/datasystem/cluster/runtime/topology_engine.cpp tests/ut/cluster/topology_engine_test.cpp` | PASS, no modified files | <1 s |
| Whitespace | `git diff --check` | PASS | <1 s |
| Tidy | `clang-tidy --quiet -p build --extra-arg=-Wno-unused-command-line-argument src/datasystem/cluster/runtime/topology_engine.cpp tests/ut/cluster/topology_engine_test.cpp` | PASS, `rc=0`; remaining output is historical/non-touched warning noise in existing test helpers, generated headers, and third-party headers | 111.81 s |

## Remote Cache Resolution

User-provided cache: `/home/ds-thirdparty-cache`. All successful remote CMake/UT evidence above used this cache.

The long-running build was executed under `tmux` after switching from the repo helper, so SSH/Codex disconnects would not
interrupt the build. Initial `-j8` build was restarted at `-j40` after checking that another remote watcher existed and
the host had 80 CPUs.

## Commits

| Commit | Purpose | Status |
|---|---|---|
| `34a865261` | `feat(cluster): keep worker alive for topology rejoin` | pushed to verified yche fork remote `origin/feat/worker-coordinator-isolation-rejoin` |
| `e3a909bfc` | `feat(cluster): gate membership recreate on cleanup` | pushed to verified yche fork remote `origin/feat/worker-coordinator-isolation-rejoin` |
| `b69447ec8` | `feat(worker): clear local objects before rejoin` | pushed to verified yche fork remote `origin/feat/worker-coordinator-isolation-rejoin` |
| `e38035845` | `feat(worker): gate rejoin membership on cleanup` | pushed to verified yche fork remote `origin/feat/worker-coordinator-isolation-rejoin` |
| `46cb0bb62` | `feat(worker): expose peer hash ring control rpc` | pushed to verified yche fork remote `origin/feat/worker-coordinator-isolation-rejoin` |

## Task 2 Evidence

| Gate | Command | Status | Runtime |
|---|---|---|---|
| RED UT compile | `cmake --build build --target cluster_topology_contract_ut -j 40` after applying test-only patch | FAIL as expected: `DsCoordinationBackend` had no `SetMembershipRecreateGate` | 8.58 s |
| GREEN UT build | `cmake --build build --target cluster_topology_contract_ut -j 40` after implementation | PASS | 38.21 s |
| GREEN focused UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="DsCoordinationBackendSessionTest.RecreatedMembership*"` | PASS, 2 tests | gtest 0 ms, wall 0.03 s |
| Session regression UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="DsCoordinationBackendSessionTest.*"` | PASS, 19 tests | gtest 14 ms, wall 0.05 s |

Task 2 currently provides the backend gate and contract tests. The Worker runtime lambda is intentionally deferred to
Task 3, where `CleanupLocalStateForRejoin(...)` exists; wiring a no-op gate here would look complete without enforcing
cleanup.

## Task 3 Evidence

| Gate | Command | Status | Runtime |
|---|---|---|---|
| RED UT compile | `cmake --build build --target ds_ut_object -j 40` after applying test-only patch | FAIL as expected: `WorkerOCServiceImpl` had no `CleanupLocalStateForRejoin` | compile reached new tests in the first target build |
| GREEN UT build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache cmake --build build --target ds_ut_object -j 40` | PASS | incremental target build completed before UT |
| GREEN focused UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.CleanupLocalStateForRejoin*"` | PASS, 3 tests | gtest 4 ms; cases 2 ms, 0 ms, 0 ms |
| Fixture regression UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.*"` | PASS, 23 tests, 1 disabled existing test | gtest 1024 ms; max case 204 ms |
| Format | `git clang-format main/master --diff -- src/datasystem/worker/object_cache/worker_oc_service_impl.h src/datasystem/worker/object_cache/worker_oc_service_impl.cpp src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.h src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp` | PASS, no modified files | <1 s |
| Whitespace | `git diff --check` | PASS | <1 s |
| Tidy | `clang-tidy --quiet -p build --extra-arg=-Wno-unused-command-line-argument src/datasystem/worker/object_cache/worker_oc_service_impl.cpp src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp` | PASS, `rc=0`; full output is historical third-party/generated/test-helper warning noise | long-running |
| Tidy line-filter | `clang-tidy --quiet -p build --extra-arg=-Wno-unused-command-line-argument --line-filter=... tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp` | PASS, `rc=0`, no output for new test lines | completed |

Task 3 adds the Worker-local cleanup entry and a data clear-all narrow path. It deliberately does not use
`SubmitTopologyFailureCleanup`, metadata owner reconciliation, or ref rebuild; the new
`CleanupLocalStateForRejoinDoesNotRebuildRefs` case locks that contract.

## Task 4 Evidence

| Gate | Command | Status | Runtime |
|---|---|---|---|
| RED UT compile | `cmake --build build --target cluster_topology_contract_ut -j 40` after applying test-only patch | FAIL as expected: `TopologyEngine` had no `RequiresMembershipRejoin` | failed after reaching `topology_engine_test.cpp` |
| GREEN focused UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill:TopologyEngineTest.AsymmetricBackendOutageIsolatesThenRecovers"` | PASS, 2 tests | gtest 4 ms; cases 2 ms, 1 ms |
| Worker target build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache cmake --build build --target datasystem_worker_static -j 40` | PASS | incremental build completed |
| Topology regression UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="TopologyEngineTest.*"` | PASS, 24 tests | gtest 886 ms |
| Format | `git clang-format main/master --diff -- src/datasystem/cluster/runtime/topology_engine.h src/datasystem/cluster/runtime/topology_engine.cpp src/datasystem/worker/worker_oc_server.cpp tests/ut/cluster/topology_engine_test.cpp` | PASS, no modified files | <1 s |
| Whitespace | `git diff --check` | PASS | <1 s |
| Tidy | `clang-tidy --quiet -p build --extra-arg=-Wno-unused-command-line-argument src/datasystem/cluster/runtime/topology_engine.cpp src/datasystem/worker/worker_oc_server.cpp tests/ut/cluster/topology_engine_test.cpp` | PASS, `rc=0`; output is historical third-party/generated/test-helper warning noise | long-running |

Task 4 wires the backend recreate gate to Worker runtime cleanup through `TopologyEngine::Builder`. The production
lambda only runs cleanup when `TopologyEngine::RequiresMembershipRejoin()` is true, avoiding data cleanup for transient
backend isolation where the local topology identity is still valid.

## Task 5 Evidence

| Gate | Command | Status | Runtime |
|---|---|---|---|
| RED build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache cmake --build build --target ds_ut_object -j 40` after adding worker-worker `GetHashRing` with the same RPC method name as `WorkerOCService` | FAIL as expected for the wrong interface shape: generated `GetHashRingSvcMethod` conflicted with `object_posix.service.rpc.pb.h` | failed during worker/object compile |
| GREEN build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache cmake --build build --target ds_ut_object -j 40` after renaming the worker-worker RPC to `GetPeerHashRing` | PASS | incremental target build completed |
| Final rebuild | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache cmake --build build --target ds_ut_object -j 40` after test constant cleanup | PASS | incremental target build completed |
| Focused UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerWorkerOcApiTest.RemoteHashRingRefreshRequiresInitializedSession:WorkerGetHashRingTest.*"` | PASS, 4 tests | gtest 0 ms; cases 0 ms, 0 ms, 0 ms, 0 ms |
| Worker-worker API regression UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerWorkerOcApiTest.*"` | PASS, 2 tests | gtest 0 ms |
| Whitespace | `git diff --check` | PASS | <1 s |
| Tidy full | `clang-tidy -p build src/datasystem/worker/object_cache/worker_worker_oc_api.cpp src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp tests/ut/worker/object_cache/worker_worker_oc_api_test.cpp` | FAIL due existing compile-command/third-party noise: linker flags as unused arguments and TBB `task.h` enum initializer compiler error | long-running |
| Tidy line-filter, test | `clang-tidy -p build tests/ut/worker/object_cache/worker_worker_oc_api_test.cpp --line-filter=... --extra-arg=-Wno-unused-command-line-argument --extra-arg=-Wno-error` | PASS; warnings suppressed by line-filter/non-user code | completed |
| Tidy line-filter, production | same as above for `worker_worker_oc_api.cpp` and `worker_worker_oc_service_impl.cpp` | `worker_worker_oc_api.cpp` passed; `worker_worker_oc_service_impl.cpp` still blocked by third-party TBB compiler error, with all source warnings suppressed | completed |

Task 5 adds only the peer-observed hash-ring control surface: `WorkerWorkerOCService.GetPeerHashRing` reuses
`GetHashRingReqPb/GetHashRingRspPb`, and `WorkerRemoteWorkerOCApi::GetHashRing` provides a bounded signed peer call.
The service side delegates to the existing `WorkerOCServiceImpl::GetHashRing`, so AK/SK validation, snapshot conversion,
version semantics, and host-id loading remain single-sourced. This does not publish peer data as coordinator authority
and does not add periodic background work.

## Final Focused Validation

| Gate | Command | Status | Runtime |
|---|---|---|---|
| CMake source build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache bash build.sh -t build -P off -X off -j 80 -i on` | PASS by tmux log: `Build source`, `build example success`, and `build datasystem success` | total 470 s; source 387 s; example 3 s |
| Topology/backend focused UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill:TopologyEngineTest.AsymmetricBackendOutageIsolatesThenRecovers:DsCoordinationBackendSessionTest.RecreatedMembership*"` | PASS, 4 tests | total 6 ms; cases 0 ms, 0 ms, 3 ms, 1 ms |
| Worker/object focused UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.CleanupLocalStateForRejoin*:WorkerWorkerOcApiTest.RemoteHashRingRefreshRequiresInitializedSession:WorkerGetHashRingTest.*"` | PASS, 7 tests | total 6 ms; cases 0 ms, 0 ms, 0 ms, 3 ms, 0 ms, 0 ms, 0 ms |
| Coordinator smoke ST | `./build/tests/st/ds_st_coordinator_backend_manual --gtest_filter="CoordinatorBackendClusterTest.RestartWorkerPropagatesTopologyByCoordinatorWatch:CoordinatorBackendClusterThreeWorkerTest.GracefulWorkerExitKeepsExistingKeysReadable"` | PASS, 2 existing smoke cases; not a normal fast gate candidate because process startup dominates | total 47.850 s; cases 21.696 s, 26.153 s |
| Bazel source build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache bash build.sh -b bazel -P off -X off -j 80 -i on` | PASS, `rc=0`; `Build completed successfully`, `bazel install done`, `build datasystem (bazel) success` | total 458 s; source 430 s |

## PR Creation Attempt

| Gate | Command | Status |
|---|---|---|
| Remote branch check | `git ls-remote origin refs/heads/feat/worker-coordinator-isolation-rejoin` | PASS, remote branch HEAD equals local `46cb0bb621c6ab6820d61ef346d255c305de85bc` |
| PR body sensitive scan | local grep for IP, `/home`, `/root`, token, secret, password | PASS after redacting the third-party cache path from the public PR body |
| PR create, first attempts | `python3 .skills/ds-create-pr/scripts/create_pr.py --owner openeuler --repo yuanrong-datasystem --base master --head feat/worker-coordinator-isolation-rejoin --fork-path yche-huawei/yuanrong-datasystem --title ... --body-file ... --timeout 180` | BLOCKED: GitCode API timed out because shell proxy variables pointed at an unavailable local proxy |
| PR create, no proxy | `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy python3 .skills/ds-create-pr/scripts/create_pr.py --owner openeuler --repo yuanrong-datasystem --base master --head yche-huawei:feat/worker-coordinator-isolation-rejoin --title ... --body-file ... --timeout 120` | PASS: created PR `!1798`, `CONFLICT_STATUS=clean`, URL `https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1798` |
| PR review prepare | `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy python3 .skills/ds-pr-review/scripts/review_pr.py prepare 1798` | PASS: bundle `/tmp/yuanrong-pr-review-cache/pr-1798/bundle.json`, 18 files, 309 changed lines, no warnings, language zh, `parallel_multi_round` |
| Local review | see `review-notes.md` | PASS: two focused rounds completed; no high-confidence findings to publish |
| Final UT recheck | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill:TopologyEngineTest.AsymmetricBackendOutageIsolatesThenRecovers:DsCoordinationBackendSessionTest.RecreatedMembership*"` and `./build/tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.CleanupLocalStateForRejoin*:WorkerWorkerOcApiTest.RemoteHashRingRefreshRequiresInitializedSession:WorkerGetHashRingTest.*"` | PASS: 4 tests and 7 tests on remote HEAD `46cb0bb621c6ab6820d61ef346d255c305de85bc` | topology/backend total 13 ms; worker/object total 18 ms |

## Conflict Resolution 2026-08-02

| Gate | Command | Status | Runtime / Notes |
|---|---|---|---|
| Fetch latest upstream | `git fetch main master` | PASS: `main/master` first advanced `a90f6c6b7..1855d0d26`; later advanced again to `ee711eb52` | pushed only to `origin=git@gitcode.com:yche-huawei/yuanrong-datasystem.git`; `main=git@gitcode.com:openeuler/yuanrong-datasystem.git` was fetch-only |
| CodeGraph | `timeout 30s /home/t14s/.local/bin/codegraph status`; `timeout 30s /home/t14s/.local/bin/codegraph query TopologyEngine` | BLOCKED: `unable to open database file`; exact-head git/source/build evidence used as fallback | shared index not rebuilt from this worktree |
| Rebase conflict 1 | `git rebase main/master` | RESOLVED: `tests/ut/cluster/topology_engine_test.cpp` conflict at top-level test constants; retained upstream child-process exit-code constants | no production code conflict |
| Rebase conflict 2 | `GIT_EDITOR=true git rebase --continue` | RESOLVED: same test file in `AsymmetricBackendOutageIsolatesThenRecovers`; retained both `proxy.ClearRangeFailures()` from upstream and `EXPECT_FALSE(engine->RequiresMembershipRejoin())` from this PR | preserves upstream backend-recovery case and this PR's no-rejoin gate |
| Compile regression found | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache cmake --build build --target cluster_topology_contract_ut ds_ut_object -j80` in tmux | FAIL before fix: `TopologyEngine::KillSelfIfIsolationExpired()` used `Provider::Instance().FlushLogs()` without `datasystem/common/log/spdlog/provider.h`; build failure meant following UT binary output was stale and not counted as PASS | root cause: rebase lost upstream includes for `<csignal>` and log provider |
| Include fix | add `<csignal>` and `datasystem/common/log/spdlog/provider.h` to `src/datasystem/cluster/runtime/topology_engine.cpp` | PASS: fix was squashed into `feat(cluster): keep worker alive for topology rejoin` via `git commit --fixup` + `git rebase -i --autosquash main/master` | kept final PR at 5 semantic commits |
| Strict CMake focused build | tmux `ds_conflict_verify2`: `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache cmake --build build --target cluster_topology_contract_ut ds_ut_object -j80` | PASS: `BUILD_RC=0` after strict `&& ... || exit 1` gating | validates rebased content on `1855d0d26`; final rebase to `ee711eb52` was clean and only added upstream client fixes |
| Topology/backend focused UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill:TopologyEngineTest.AsymmetricBackendOutageIsolatesThenRecovers:DsCoordinationBackendSessionTest.RecreatedMembership*"` | PASS: 4 tests | total 65 ms; cases 0 ms, 0 ms, 2 ms, 62 ms |
| Worker/object focused UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.CleanupLocalStateForRejoin*:WorkerWorkerOcApiTest.RemoteHashRingRefreshRequiresInitializedSession:WorkerGetHashRingTest.*"` | PASS: 7 tests | total 5 ms; cases 0 ms, 0 ms, 0 ms, 3 ms, 0 ms, 0 ms, 0 ms |
| Conflict marker / whitespace | `rg -n '^(<<<<<<<|=======|>>>>>>>)' src tests`; `git diff --check main/master...HEAD` | PASS: no conflict markers, no whitespace errors | `rg` returned 1 because no markers were found |
| clang-format dry-run | `git diff --name-only main/master...HEAD | rg '\.(cpp|h)$' | xargs -r clang-format --dry-run --Werror` | NOT USED AS PASS GATE: full-file dry-run reports large existing/local style differences across changed files | no auto-format applied to avoid noisy unrelated reformat |
| Final push | `git push --force-with-lease origin feat/worker-coordinator-isolation-rejoin` | PASS: final fork branch `ec1418f75601b703ebb6d030eac72dc73b2fa2f6` | openeuler remote was not pushed |
| Final PR status | GitCode API without proxy env; `review_pr.py prepare 1798` | PASS: `mergeable=True`, `conflict_passed=True`, `branch_missing_passed=True`, `non_ff_passed=True`, head `ec1418f75601b703ebb6d030eac72dc73b2fa2f6`, base `ee711eb52baf0130752aefc1ae64159cace64b11`; prepare: 18 files, 303 changed lines, no warnings | PR updated at `2026-08-02T10:02:14+08:00` |

## Weak Evidence Closure 2026-08-02

Scope: finish incomplete/weak-evidence items after the conflict-resolution PR head. Worktree and remote validation both ran
under `/home`; third-party components used `/home/ds-thirdparty-cache`; long-running commands ran in `tmux`. Build
parallelism used `-j40` because there were other validation sessions on the host.

Design cross-check:

- Original measure 2 requires Worker not to self-kill during coordinator control-plane isolation, and to recover once the
  coordinator topology is available again.
- Coordinator remains the authority; worker peer information is still non-authoritative. This round does not add new
  peer-authority paths.
- Membership recreation remains gated by local cleanup. This round only fixes recovery progress and keepalive/reporting
  liveness around the existing design.

Fixes:

| Area | Gap | Fix | Evidence |
|---|---|---|---|
| Coordinator recovery | Accepted canonical payload could wait forever if no exact new report arrived at the discovery deadline. | Add one delayed reconcile at the discovery deadline after an accepted payload is stored but no install version is ready yet. | `TopologyRecoveryManagerTest.AcceptedPayloadInstallsAfterDiscoveryWindowWithoutNewReport` |
| Worker keepalive | `OnMembershipEnsured()` notified the keepalive loop, but the wait predicate only checked exit, so the immediate renew could be swallowed until the lease interval. | Add a protected `keepAliveWakeEpoch_` and make the keepalive wait predicate wake on membership ensure. | `DsCoordinationBackendSessionTest.EnsuredMembershipWakesRenewalBeforeLeaseExpires` |
| Recovery reporter | A RECOVERING response without payload request stopped the current report loop, requiring a new membership signal. | Keep reporting with backoff while the coordinator remains RECOVERING, and attach payload only when requested. | `TopologyRecoveryReporterTest.RecoveringKeepsReportingWithoutMembershipSignal`; `TopologyRecoveryReporterTest.SendsPayloadOnlyWhenCoordinatorRequestsIt` |

Validation:

| Gate | Command | Status | Runtime / Notes |
|---|---|---|---|
| CMake focused build | `DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache cmake --build build --target cluster_topology_contract_ut ds_ut ds_ut_object ds_st_coordinator_backend_manual -j40` in tmux | PASS after keepalive wake fix | 89.40 s |
| Coordinator recovery UT | `./build/tests/ut/ds_ut --gtest_filter="TopologyRecoveryManagerTest.InstallsUniqueHighestCanonicalPayload:TopologyRecoveryManagerTest.AcceptedPayloadInstallsAfterDiscoveryWindowWithoutNewReport:TopologyRecoveryManagerTest.RecoveryTasksPreserveSubmittingTraceContext"` | PASS, 3 tests | wall 0.09 s |
| Backend session UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="DsCoordinationBackendSessionTest.EnsuredMembershipClearsEarlierRenewalFailure:DsCoordinationBackendSessionTest.EnsuredMembershipWakesRenewalBeforeLeaseExpires:DsCoordinationBackendSessionTest.RecreatedMembership*:DsCoordinationBackendSessionTest.StaleEnsuredRevisionCannotRollbackNewerMembershipMutation"` | PASS, 4 tests | wall 0.07 s |
| Topology contract UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill:TopologyEngineTest.AsymmetricBackendOutageIsolatesThenRecovers:TopologyEngineTest.*BackendEvidence*"` | PASS, 4 tests | wall 0.45 s |
| Worker OC UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.CleanupLocalStateForRejoin*:WorkerWorkerOcApiTest.RemoteHashRingRefreshRequiresInitializedSession:WorkerGetHashRingTest.*"` | PASS, 2 matched tests in this build layout | wall 0.07 s |
| Recovery reporter UT | `./build/tests/ut/ds_ut --gtest_filter="TopologyRecoveryReporterTest.*"` | PASS, 14 tests | wall 0.22 s |
| Coordinator outage ST | `./build/tests/st/ds_st_coordinator_backend_manual --gtest_filter="CoordinatorBackendClusterTest.WorkersStayAliveDuringCoordinatorOutageAndRecover"` rerun in tmux | PASS, 1 test | gtest 47.772 s; wall 47.85 s; in-test recovery path logged 24.044 s |

Notes:

- First full ST attempt after the UT pass failed during initial process startup before the target outage/recovery
  scenario: one worker hit `Coordinator routing deadline exceeded` and another saw `cluster topology Snapshot is not
  ready`. The ST-only rerun passed and is the counted ST evidence for this round.
- Focused UT count for this round is 27 cases, aggregate outer runtime 0.90 s. Counted ST is 1 case, outer runtime
  47.85 s.

## URMA_MOCK Final Validation 2026-08-02

Scope: user requested final validation with `URMA_MOCK` enabled and build parallelism `-j80`. Validation ran on remote
HEAD `066caeb3b23929d6baa902f28c4706721345e8e8`, using `/home/ds-thirdparty-cache`, under `tmux`, with all new
temporary output rooted under `/home`.

| Gate | Command | Status | Runtime / Notes |
|---|---|---|---|
| CMake build | `bash build.sh -t build -P off -X off -U on -j80 -i on` | PASS, `CMAKE_BUILD_RC=0`; `BUILD_WITH_URMA_MOCK=on` | 611 s |
| Coordinator recovery UT | `./build/tests/ut/ds_ut --gtest_filter="TopologyRecoveryManagerTest.InstallsUniqueHighestCanonicalPayload:TopologyRecoveryManagerTest.AcceptedPayloadInstallsAfterDiscoveryWindowWithoutNewReport:TopologyRecoveryManagerTest.RecoveryTasksPreserveSubmittingTraceContext"` | PASS, 3 tests | gtest 9 ms; outer 0 s |
| Backend session UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="DsCoordinationBackendSessionTest.EnsuredMembershipClearsEarlierRenewalFailure:DsCoordinationBackendSessionTest.EnsuredMembershipWakesRenewalBeforeLeaseExpires:DsCoordinationBackendSessionTest.RecreatedMembership*:DsCoordinationBackendSessionTest.StaleEnsuredRevisionCannotRollbackNewerMembershipMutation"` | PASS, 5 tests | gtest 11 ms; outer 0 s |
| Topology contract UT | `./build/tests/ut/cluster_topology_contract_ut --gtest_filter="TopologyEngineTest.LocalMemberRemovedFromSnapshotRequiresRejoinWithoutSigkill:TopologyEngineTest.AsymmetricBackendOutageIsolatesThenRecovers:TopologyEngineTest.*BackendEvidence*"` | PASS, 2 matched tests | gtest 66 ms; outer 0 s |
| Worker OC UT | `./build/tests/ut/ds_ut_object --gtest_filter="WorkerOcServiceImplTest.CleanupLocalStateForRejoin*:WorkerWorkerOcApiTest.RemoteHashRingRefreshRequiresInitializedSession:WorkerGetHashRingTest.*"` | PASS, 8 tests | gtest 5 ms; outer 0 s |
| Recovery reporter UT | `./build/tests/ut/ds_ut --gtest_filter="TopologyRecoveryReporterTest.*"` | PASS, 14 tests | gtest 141 ms; outer 1 s |
| Coordinator outage ST | `./build/tests/st/ds_st_coordinator_backend_manual --gtest_filter="CoordinatorBackendClusterTest.WorkersStayAliveDuringCoordinatorOutageAndRecover"` | PASS, 1 test | gtest 39.748 s; outer 39 s; shutdown 543 ms; outage survival 5000 ms; recovery 12402 ms; scenario 17946 ms |
| clang-tidy | line-filtered `clang-tidy --quiet -p build --extra-arg=-Wno-unused-command-line-argument --extra-arg=-Wno-error --line-filter=...` over 8 changed files | PASS by progression through all 8 files and continuation into Bazel; no touched-line diagnostic was emitted | full log retained in tmux validation ledger |
| Bazel build and install | `TMPDIR=/home/.../tmp PATH=/home/.../bazel-home-bin:$PATH bash build.sh -b bazel -P off -X off -U on -j80 -i on`, wrapper injects `bazel --output_user_root=/home/.../bazel-output-root` | PASS, `BAZEL_HOME4_RC=0`; command used `--config=release --config=urma_mock --jobs=80`; `Build completed successfully`, `bazel install done`, `build datasystem (bazel) success` | final incremental/install run 26 s; preceding full Bazel source build succeeded in 548 s before install hit environment space |

Counts for PR description: focused UT 32 cases, counted ST 1 case. UT outer runtime from script markers is 1 s
aggregate; gtest-reported UT runtime is 232 ms aggregate. ST outer runtime is 39 s; gtest-reported ST runtime is
39.748 s.

Bazel environment note: the first Bazel attempt used default `/root/.cache/bazel` and failed with `No space left on
device`. The second attempt moved Bazel `output_user_root` to `/home` and proved source build success
(`5416 total actions`) but install still touched root-backed `/tmp`. The final counted Bazel run set both
`output_user_root` and `TMPDIR` under `/home`, then passed.
