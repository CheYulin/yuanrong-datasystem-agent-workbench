# 2026-08-04 实施记录

## 当前分支

- worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/fast-failover-isolation-20260804`
- branch: `feat/fast-failover-isolation-20260804`
- base: `e63f4270826783757ddfe1911a94ce87fd9b7461`
- commit: `1c8e25f032300f7cdb1bb3b0811f58e2abe0ce15`
- PR: https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1840

## 已完成

- TDD 用例先行：
  - worker 本地连续失败窗口与成功 reset。
  - Coordinator summary reporter 阈值、过期、inactive 过滤。
  - Controller active candidate 复用 failure plan。
- worker:
  - `node_dead_timeout_s` 作为 membership lease TTL。
  - `node_timeout_s / 3` 作为 keepalive 上报周期。
  - `node_timeout_s / 2` 作为本地失败持续窗口。
  - `failedCount >= 3` 后进入 failed target summary。
- keepalive:
  - 扩展 `KeepAliveReqPb.failed_targets`。
  - summary 只携带 target address。
- Coordinator:
  - KeepAlive 续租成功后解析 membership key 得到 cluster/reporter。
  - `TopologyControlHost` 汇总 `target -> reporter -> receiveTime`。
  - threshold: `min(max(ceil(N * 5%), 5), N - 1)`。
- Controller:
  - 新增 `activeFailureCandidateProvider`。
  - active candidates 在 witness/lease fallback 后合入 `confirmedFailure`。
  - 复用 `CommitConfirmedFailures` 和现有 hashring update。
- metadata RPC:
  - worker metadata RPC 结果通过 `metadataRpcObserver` 上报到 `TopologyEngine`。
  - 仅 `K_RPC_UNAVAILABLE` / `K_RPC_DEADLINE_EXCEEDED` 计入链路失败。

## 本地验证

- `git diff --check`: PASS。
- `git clang-format --diff`: PASS。
- 本地 `bash build.sh -t build ...`: 未到源码编译；失败/阻塞在三方件构建，本地无 `/home/third-party` 或 `/home/cache` 缓存。
- 敏感信息扫描: PASS。

## 远端验证

- target: `tiantiyun-80c128g`。
- CMake build: PASS。
  - command: `build.sh -t build -B /home/$USER/ds-fast-failover-build -o /home/$USER/ds-fast-failover-output ...`
  - note: 默认远端 worktree 在根分区，已将 build/output/tmp/DS_OPENSOURCE_DIR 切到 `/home/$USER`。
- `ds_ut` target build: PASS。
- changed-line clang-tidy: PASS。
  - 9 个生产 `.cpp` 文件，按 `main/master...HEAD` 修改行过滤，全部 RC 0。
- Bazel source targets: PASS。
  - Bazel 7.4.1。
  - targets:
    - `//src/datasystem/cluster:cluster_topology`
    - `//src/datasystem/cluster/coordination_backend:coordination_backend`
    - `//src/datasystem/common/coordinator:coordinator_store`
    - `//src/datasystem/coordinator:coordinator_service_impl`
    - `//src/datasystem/coordinator:coordinator_topology_control_host`
    - `//src/datasystem/worker/object_cache/service:worker_oc_service_crud_common_api`
    - `//src/datasystem/worker/object_cache/service:worker_oc_service_get_impl`
    - `//src/datasystem/worker/object_cache:worker_oc_service_impl`
  - 8 targets, 207 actions, elapsed 118.36s。
- Bazel full `build.sh -b bazel`: BLOCKED。
  - `//:hashring_parser_file` 引用不存在的 `//tests/st:hashring_parser`。
  - worker/coordinator 直接目标会进一步触发既有 `tests/st/cluster/external_cluster.cpp` 缺 `common.h`。
  - 两处均在本 PR 修改文件外，未进入本 PR 源码失败。
- UT: PASS。
  - `TopologyControlHostTest.WorkerFailureSummariesRequireReporterThreshold`
  - `TopologyControlHostTest.WorkerFailureSummariesExpireAndIgnoreInactiveMembers`
  - `TopologyControlHostTest.WorkerFailureSummaryRefreshWakesReconcile`
  - `DsCoordinationBackendSessionTest.PeerRpcFailuresNeedCountAndWindowBeforeReporting`
  - `DsCoordinationBackendSessionTest.PeerRpcSuccessClearsFailureSummary`
  - `TopologyControllerTest.ActiveFailureCandidateProviderCommitsFailureForReadyMember`
- ST: PASS。
  - `CoordinatorBackendClusterThreeWorkerTest.KilledWorkerScaleDownAllowsNewWritesReadableFromOtherWorker`
  - 1 test, elapsed 26.30s。

## CodeGraph

- shared index: 2,159 files / 53,469 nodes / 157,732 edges，up to date。
- impact:
  - `DsCoordinationBackend`: backend/runtime/coordination backend UT。
  - `TopologyControlHost`: coordinator service/host/coordinator UT/ST。
  - `TryConfirmFailures`: controller reconcile path。

## PR 检查

- `ds-pr-review prepare 1840`: PASS。
  - 24 files。
  - 562 changed lines。
  - warnings: 0。
  - mode: `parallel_multi_round`。
- empty findings dry-run: PASS。
  - posted line comments: 0。
  - posted general comments: 0。

## 下一步

- 等待 CI。
