# Cursor Guidance: Client Direct Read Refactor

> 2026-06-25 latest review guidance: see
> [review-guidance-2026-06-25.md](./review-guidance-2026-06-25.md) for the current PR #1119 guidance on
> maximizing client/worker meta/data reuse, explicit data timeout semantics, hash ring versioning, and scale/cutback
> verification.

## Context

Current branch: `feature/client-direct-read-flow`

Goal: support client-side access to remote workers by extracting reusable meta/data access orchestration, meta redirect/moving handling, and hash ring maintenance logic.

The first implementation has useful pieces, but the architecture is only half-extracted:

- `ObjectReadAccessFlow` is common, but it still inlines response/payload merge.
- `query_meta_redirect_helper` is common, but Client/Worker still build orchestration options in role-specific thick adapters.
- Client route lookup refreshes the hash ring too often.
- Worker-sourced hash rings have no version, so stale-ring protection is weak.

Treat this as a contract-first refactor, not a mechanical code movement.

## Critical Findings To Fix First

## CodeGraph Notes From Feature Worktree

CodeGraph was initialized on:

`/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/client-direct-read-flow`

The feature worktree currently has newer P2 WIP than `origin/feature/client-direct-read-flow`:

- `QueryMetaOrchestratingMetaClient` exists.
- `IQueryMetaTransport`, `ClientQueryMetaTransport`, and `WorkerQueryMetaTransport` exist.
- `ObjectReadAccessFlow` already calls `MergeQueryMetaGroupResult`.
- Client outer meta retry now uses `IsOuterMetaPhaseRetriable` and `RefreshRouteOnClusterEvent`.
- `ClientHashRingSource` already has cheap lookup vs event refresh split.

So the next Cursor pass should **not recreate** those abstractions. It should harden the contracts and finish the missing pieces below.

Useful CodeGraph findings:

```text
callers ClientHashRingSource::RefreshForRouteLookup
  - ClientHashRingSource::GetMetaAddress
  - DirectReadRouteProvider::RefreshRouteIfNeeded
  - ObjectClientImpl::TryDirectReadCutbackToLocalWorker

callees DirectReadFlow::ExecuteMetaPhaseWithRetry
  - ObjectReadAccessFlow::ExecuteMetaPhase
  - IsOuterMetaPhaseRetriable
  - DirectReadFallback::ToPathFallbackStatus
  - DirectReadTestHook::RecordStaleRouteRetry
  - DirectReadRouteProvider::RefreshRouteOnClusterEvent

callees ObjectReadAccessFlow::ExecuteMetaPhase
  - routeProvider_->RefreshRouteIfNeeded
  - routeProvider_->GetMetaAddress
  - QueryMetaGroup
```

Interpretation:

- Hot-path refresh is improved but still needs tests proving steady-state repeated Gets do not call full refresh per key/Get.
- Outer moving retry is improved but needs tests proving `K_TRY_AGAIN/meta_is_moving` is handled only by the orchestrating meta client.
- Redirect payload merge is still wrong: `FollowQueryMetaRedirects` still merges `redirectRsp` before adjusting payload indexes.

### 1. Redirect payload indexes are wrong when primary and redirect responses both carry payloads

File: `src/datasystem/common/object_cache/read_access/query_meta_redirect_helper.cpp`

In `FollowQueryMetaRedirects`, the code currently does:

```cpp
MergeQueryMetaResponses(rsp, redirectRsp);
RETURN_IF_NOT_OK(AppendQueryMetaPayloads(payloads, redirectRsp, redirectPayloads));
```

`MergeQueryMetaResponses` swaps `redirectRsp.query_metas()` into `rsp`. After that, `AppendQueryMetaPayloads` adjusts indexes on the now-empty or moved-from `redirectRsp`, not on the merged metas in `rsp`. If primary payloads already exist, redirected metas can still point at index `0` and read the wrong payload.

Required red test:

- Build a primary response with one inline payload and one redirect info.
- Redirect response returns one meta with `payload_indexs = [0]` and one redirect payload.
- After `QueryMetaWithRedirectAndMoving`, assert:
  - total payload count is 2
  - primary meta index is 0
  - redirected meta index is 1

Fix direction:

- Adjust redirect response payload indexes before moving its metas into the aggregate, or add a helper that appends payloads and merges the adjusted response as one atomic operation.
- Use that same helper from `ObjectReadAccessFlow`.

### 2. Hash ring refresh is on the hot path multiple times per Get

Files:

- `src/datasystem/common/object_cache/read_access/object_read_access_flow.cpp`
- `src/datasystem/client/object_cache/direct_read/direct_read_route_provider.cpp`
- `src/datasystem/client/object_cache/direct_read/client_hash_ring_source.cpp`

Current path:

1. `ObjectReadAccessFlow::ExecuteMetaPhase` calls `routeProvider_->RefreshRouteIfNeeded()`.
2. For every key, `DirectReadRouteProvider::GetMetaAddress` calls `RefreshRouteIfNeeded()` again.
3. `ClientHashRingSource::GetMetaAddress` calls `RefreshForRouteLookup()` again.
4. `RefreshForRouteLookup()` calls `RefreshRing()` whenever a snapshot exists.

This makes steady-state direct reads do repeated worker/etcd ring refreshes, which contradicts `hash-ring-refresh-policy.md`.

Required behavior:

- Bootstrap: refresh if no snapshot.
- Steady state: use cached ring.
- Scaling task visible in ring: refresh.
- Explicit event: moving, stale route, or cutback check: full refresh.
- Debug force flag may refresh every lookup, but only when explicitly enabled.

Fix direction:

- Split API names by intent:
  - `EnsureRouteSnapshotForLookup()` or `RefreshForRouteLookup()` should be cheap.
  - `RefreshOnClusterEvent()` should do worker -> etcd full refresh.
- Remove nested refresh calls. A lookup method should not secretly refresh if the caller already did.
- Add a steady-state ST/UT assertion that N repeated Gets do not increase worker/etcd refresh count linearly.

### 3. Moving retry remains double-layered

Files:

- `src/datasystem/client/object_cache/direct_read/direct_read_flow.cpp`
- `src/datasystem/client/object_cache/direct_read/direct_read_rpc_adapter.cpp`
- `src/datasystem/common/object_cache/read_access/query_meta_redirect_helper.cpp`

Current state:

- `QueryMetaWithRedirectAndMoving` retries `meta_is_moving`.
- `DirectReadFlow::ExecuteMetaPhaseWithRetry` also treats `K_TRY_AGAIN` as retriable and records moving retry.

Required behavior:

- Moving retry belongs to the common meta client/orchestrator.
- Outer direct-read retry should handle stale route / route refresh only, not moving.

Fix direction:

- Introduce a common `QueryMetaOrchestratingMetaClient` implementing `IObjectReadMetaClient`.
- Its transport dependency performs exactly one `QueryMeta` RPC.
- Client/Worker provide options, not loops.
- Outer `DirectReadFlow` only retries clear route-stale statuses.

### 4. Worker `GetClusterState` does not return a ring version

Files:

- `src/datasystem/protos/worker_object.proto`
- `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp`
- `src/datasystem/client/object_cache/direct_read/direct_read_rpc_adapter.cpp`
- `src/datasystem/common/object_cache/read_only_hash_ring_view.cpp`

Current state:

- Worker response contains `HashRingPb` only.
- Client writes `version = -1` for worker-sourced rings.
- `ReadOnlyHashRingView::UpdateFromPb` cannot reject stale worker snapshots if both versions are unknown.

Required behavior:

- `GetClusterStateRspPb` should include the authoritative ring revision/version.
- Client should store worker and etcd ring snapshots in one comparable version space, or explicitly document separate version spaces and never let unknown worker snapshots overwrite newer etcd snapshots.

Fix direction:

- Add `int64 ring_etcd_mod_revision` or an equivalent authoritative version to `GetClusterStateRspPb`.
- Populate it from the worker's hash ring source.
- Use it in `DirectReadRpcAdapter::GetClusterState`.
- Add a test: newer snapshot cannot be overwritten by older worker snapshot.

## Target Shape

Keep these boundaries:

| Layer | Owns | Must not own |
|---|---|---|
| Common read access | grouping, redirect/moving algorithm, response/payload merge | Client fallback policy, Worker-specific address resolution |
| Client direct read | gate, fallback, route refresh policy, TCP data read | redirect/moving loops |
| Worker | master RPC transport, deadline policy, primary replica address resolution | duplicated redirect/moving algorithm |
| Transport | exactly one RPC call | retry, redirect follow, response merge |

Recommended common interfaces:

```cpp
class IQueryMetaTransport {
public:
    virtual ~IQueryMetaTransport() = default;
    virtual Status QueryMetaOnce(const HostPort &metaAddress,
                                 const std::vector<std::string> &objectKeys,
                                 int64_t subTimeoutMs,
                                 bool enableRedirect,
                                 master::QueryMetaRspPb &rsp,
                                 std::vector<RpcMessage> &payloads) = 0;
};

class QueryMetaOrchestratingMetaClient : public IObjectReadMetaClient {
public:
    struct Options {
        QueryMetaMovingRetryOptions moving;
        QueryMetaRedirectFollowOptions redirect;
    };

    Status QueryMeta(const HostPort &metaAddress,
                     const std::vector<std::string> &objectKeys,
                     int64_t subTimeoutMs,
                     master::QueryMetaRspPb &rsp,
                     std::vector<RpcMessage> &payloads) override;
};
```

## Implementation Order

1. Add missing failing tests for redirect payload offset and steady-state hash ring refresh count.
2. Fix merge helper ordering by making payload append + response merge atomic.
3. Change `ObjectReadAccessFlow` to use the shared merge helper instead of inline payload offset logic.
4. Split Client hash ring refresh into cheap lookup vs explicit event refresh; remove nested refreshes.
5. Introduce `IQueryMetaTransport` and `QueryMetaOrchestratingMetaClient`.
6. Move Client and Worker QueryMeta loops into the orchestrating meta client.
7. Add ring version to `GetClusterState` and wire it into `ReadOnlyHashRingView`.
8. Only after all above, consider further file cleanup in `direct_read/`.

## Cursor Prompt For Current P2 WIP

Use this prompt for the next Cursor pass:

```text
You are working in yuanrong-datasystem on branch feature/client-direct-read-flow.

Read these docs first:
- yuanrong-datasystem-agent-workbench/rfc/2026-06-21-client-direct-read-flow/design.md
- yuanrong-datasystem-agent-workbench/rfc/2026-06-21-client-direct-read-flow/hash-ring-refresh-policy.md
- yuanrong-datasystem-agent-workbench/rfc/2026-06-21-client-direct-read-flow/cursor-guidance.md

Important: the current feature worktree already has P2 WIP:
- QueryMetaOrchestratingMetaClient
- IQueryMetaTransport
- ClientQueryMetaTransport
- WorkerQueryMetaTransport
- ObjectReadAccessFlow using MergeQueryMetaGroupResult
- cheap RefreshForRouteLookup vs event RefreshOnClusterEvent

Do not recreate those abstractions and do not do broad cleanup first. First lock the remaining behavior with failing tests.

Task A: Add a red UT proving redirect inline payload indexes are offset correctly when both the primary QueryMeta response and a redirected QueryMeta response contain payloads. The redirected meta must point to payload index 1 after merge, not 0.

Task B: Add a red UT/ST proving steady-state direct reads do not refresh the client hash ring on every route lookup. Bootstrap may refresh; explicit moving/stale-route events may refresh; ordinary repeated Gets should use the cached snapshot.

Task C: Add a red UT proving meta moving is not double-retried: QueryMetaOrchestratingMetaClient owns moving retry; DirectReadFlow outer retry only handles stale route / route unavailable.

Then implement the smallest fixes:
1. Fix QueryMeta redirect merge so payload index adjustment happens before the redirected metas are moved into the aggregate response. Prefer an atomic helper like MergeQueryMetaGroupResult for redirect too.
2. Make sure all QueryMeta merge paths use the same helper/order. Tests must assert payload indexes, not just payload count.
3. Keep RefreshForRouteLookup cheap. If any lookup path still triggers nested full refreshes, remove that and keep full refresh in RefreshOnClusterEvent only.
4. If GetClusterState still returns HashRingPb without a comparable version, add the proto/version wiring or explicitly block worker snapshots from overwriting known newer etcd snapshots.

Do not implement URMA direct read. Do not broaden the feature gate. Do not refactor unrelated worker get/data paths.

Verification required:
- targeted UT for query_meta_redirect_helper
- targeted UT for object_read_access_flow
- ClientDirectRead ST subset covering moving, redirect fallback, bootstrap, and steady-state refresh count
- Bazel/CMake target wiring for any new files
```

## Review Checklist For Cursor Output

- No role-specific adapter contains a moving loop or redirect follow loop after P2.
- No QueryMeta transport function does more than one RPC.
- Every response merge path adjusts payload indexes before or during merge.
- `ObjectReadAccessFlow` has no hand-written payload offset loop.
- Steady-state direct read does not call worker/etcd for the ring per key.
- `K_TRY_AGAIN/meta_is_moving` is not retried both inside and outside the meta client.
- Worker and client redirect behavior differs only through options.
- Tests assert payload indexes, not just payload counts.
- Tests assert refresh counts, not just successful reads.
