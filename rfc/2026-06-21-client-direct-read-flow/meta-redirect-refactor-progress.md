# Meta Redirect/Moving Refactor Progress

**Status:** In-Progress (P2 Done, R2 Done)  
**Branch:** `feature/client-direct-read-flow`  
**Started:** 2026-06-24

## Goal

Extract duplicated QueryMeta redirect/moving control-plane logic from Client (`direct_read_rpc_adapter.cpp`) and Worker (`QueryMetaFromMasterDirect`, `RedirectRetryWhenMetasMoving`, `QueryMetadataFromRedirectMaster`) into `common/object_cache/read_access/`.

## Completed

| Step | Artifact | Notes |
|------|----------|-------|
| Layer 1 | `query_meta_merge_helper.{h,cpp}` | `MergeQueryMetaResponses`, `AppendQueryMetaPayloads` |
| Layer 2 | `query_meta_redirect_helper.{h,cpp}` | `RetryWhileMetaIsMoving`, `FollowQueryMetaRedirects`, `QueryMetaWithRedirectAndMoving` |
| P2 Common | `query_meta_orchestrating_meta_client`, `IQueryMetaTransport` | Client/Worker 共用编排入口 |
| P2 Client | `ClientQueryMetaTransport`, `client_direct_read_meta_options`, slim `direct_read_rpc_adapter` | 外层 retry 仅 stale route |
| P2 Worker | `WorkerQueryMetaTransport`, slim `QueryMetaFromMasterDirect` | options 注入 deadline / primary replica |
| Codex A | redirect payload index | append **then** merge；UT 断言 index |
| Codex B | steady-state ring refresh | cheap lookup vs event refresh；ST 断言 refresh count |
| Codex C | moving 单层重试 | `IsOuterMetaPhaseRetriable` + orchestrator UT |
| R2 | `GetClusterStateRspPb.ring_etcd_mod_revision` | Worker `HashRing::currEtcdModRevisionOfRing_`；Client 拒绝 stale worker 快照 |
| R2 | distributed cutback ST | `LocalWorkerRecoveryCutbackWithDistributedRing` 取消 GTEST_SKIP |
| Build | Bazel ST `client_direct_read_st_test` + `st_bazel_test_main.cpp` | 修复 protoc main 抢占 gtest |
| UT | redirect / orchestrator / fallback / hash_ring_view | payload index + version guard |

## Behavioral preservation

| Aspect | Client | Worker |
|--------|--------|--------|
| Moving retry budget | `client_direct_read_retry_count` | RPC deadline (`reqTimeoutDuration`) |
| Moving ring refresh | `beforeMovingRetry` → event refresh | N/A |
| Redirect address | `HostPort::ParseString` | `GetPrimaryReplicaAddr` |
| Nested redirect / moving on redirect | Rejected | Not rejected (`rejectNestedRedirectInfo=false`, `rejectMovingOnRedirect=false`) |

## Verification (2026-06-21, Bazel on tiantiyun)

| Suite | Scope | Result |
|-------|-------|--------|
| Bazel UT targeted | redirect_helper, orchestrating_meta_client, direct_read_fallback, read_only_hash_ring_view | **PASS** |
| Bazel ST | `ClientDirectRead*` (22 cases, R2 unskip distributed cutback) | **PASS** (post R2 verify) |

## Deferred

| Item | Notes |
|------|-------|
| R3 | Gateway Get 携带 client ring version；Worker STALE |
| P3 | Worker HashRing 复用 `ReadOnlyHashRingView` |
| Worker `RedirectRetryWhenMetasMoving` | bulk 路径仍独立模板 |
| Worker gateway meta batch ST | 与 ClientDirectRead 对称回归 |
| URMA direct read | P4 |
| RFC doc §12 checkboxes in design.md | 待同步 |

## Related docs

- [pr-description.md](./pr-description.md) — MR 1119 描述
- [hash-ring-refresh-policy.md](./hash-ring-refresh-policy.md) — R1/R2 Done, R3 Planned
- [cursor-guidance.md](./cursor-guidance.md) — Codex review checklist
