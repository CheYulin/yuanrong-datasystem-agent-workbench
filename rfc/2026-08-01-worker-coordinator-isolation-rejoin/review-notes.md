# Review Notes

PR: https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1798

## Prepare

`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy python3 .skills/ds-pr-review/scripts/review_pr.py prepare 1798`

Result:

- bundle: `/tmp/yuanrong-pr-review-cache/pr-1798/bundle.json`
- files: 18
- changed lines: 309
- warnings: none
- language: zh
- review plan: `parallel_multi_round`

## Round 1: Correctness, Lifecycle, Concurrency

- `DsCoordinationBackend::AutoCreateKeepAliveKey(true)` copies `membershipRecreateGate_` under `eventHandlerMutex_` and invokes it before `membershipMutationMutex_`, so Worker cleanup cannot deadlock against membership mutation.
- Recreate gate failure prevents membership mutation before coordinator `Range` or `Put`. This preserves the contract that a Worker does not publish a new membership identity until local cleanup succeeds.
- `TopologyEngine::RequiresMembershipRejoin()` is driven only by local identity invalidation: local member missing after it previously existed, local identity changed, or local member failed.
- Transient control-backend quorum uncertainty continues to publish `ROLE_ISOLATED` without setting `membershipRejoinRequired_`, so temporary coordinator access failures do not trigger data cleanup.
- The Worker lambda uses the existing `TOPOLOGY_STOP_GRACE` deadline and delegates cleanup through `WorkerOCServiceImpl`, keeping ownership with the object-cache service rather than duplicating cleanup in `WorkerOCServer`.
- `CleanupLocalStateForRejoin` first closes incoming migration admission and then clears local objects. The tests cover success, expired deadline, and no ref rebuild.

High-confidence findings: none.

## Round 2: API Boundary, Build, Tests, Compatibility

- `WorkerWorkerOCService.GetPeerHashRing` appends a new RPC method and reuses existing `GetHashRingReqPb/GetHashRingRspPb`; this avoids introducing new wire messages.
- The service implementation delegates to `WorkerOCServiceImpl::GetHashRing`, preserving existing AK/SK verification, version check, topology-to-pb conversion, and host-id map behavior in one place.
- The client API validates a positive timeout and uses existing `WorkerRemoteWorkerOCApi` RPC diagnostic wrapping.
- CMake and Bazel both exercised generated proto/service output. The earlier RED build caught the generated method-name collision when the RPC was named `GetHashRing`; the final RPC name `GetPeerHashRing` avoids the collision.
- New UTs cover rejoin isolation flag behavior, backend recreate gate behavior, local cleanup behavior, and peer hash-ring API initialization error. Existing ST smoke was run for coordinator topology watch paths, but the process-level cases are too slow for the normal <6s ST gate.

High-confidence findings: none.

## Residual Notes

- Full clang-tidy on one touched worker-worker service source remains blocked by existing third-party TBB compile-command noise; line-filter and reachable source diagnostics were clean.
- Peer-observed hash-ring background refresh is intentionally deferred and listed as a follow-up in the PR body.
