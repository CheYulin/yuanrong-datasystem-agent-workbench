# PR1405 validation status at 861fa07df

## Revisions

- Upstream base: `main/master=7507f139fdb1222b29eb77542223a68dea6cd19f`
- Behavioral candidate: `861fa07dfac02d394219d62cb3275e461eb4d899`
- Current fork source: `cafea7a90fc90a7c684bffd184095562fcf53dfc` (one non-behavioral CodeCheck cleanup commit)
- Rebase state after fresh fetch: `0 behind / 73 ahead`; no replay was required.
- Fork source: `yche-huawei/feat/worker-self-healing-main-20260716`
- Verified remote source SHA: `cafea7a90fc90a7c684bffd184095562fcf53dfc`

## Results

| Scope | Revision | Passed | Failed | Disabled or static | Runtime |
|---|---|---:|---:|---:|---:|
| CMake `ds_ut_object`, `ds_st`, `ds_st_kv_cache`, `cluster_topology_contract_ut` | `d1ab83a10` product-equivalent | build PASS | 0 | 0 | 215.71s |
| Final CMake `ds_ut_object` rebuild | `861fa07df` | build PASS | 0 | 0 | 62.65s |
| Ownership consolidation focused UT | `861fa07df` | 3 | 0 | 0 | 0.30s |
| CI 8722 active gate cases | `d1ab83a10` product-equivalent | 7 | 0 | 1 baseline-disabled | 19.70s |
| One-shot design and related ST | `d1ab83a10` product-equivalent | 27 | 0 | 0 | 151.07s |
| ServingReady/Complete, bounded cleanup, best-effort callbacks | `861fa07df` | 8 | 0 | 0 | 0.37s |
| Bazel 7.4.1 `worker_oc_service_impl` source target | `d1ab83a10` product-equivalent | build PASS | 0 | 0 | 239.62s |

The changes from `d1ab83a10` to `861fa07df` are confined to the ownership-planner UT fixture: scanner-safe test data and explicit metadata-mode construction. Product files, ST files, CMake, and Bazel targets are unchanged. The final focused UT was rebuilt and rerun at the exact final candidate.

The changes from `861fa07df` to `cafea7a90` only clarify no-throw recovery boundaries, name an existing log-prefix constant, use an equivalent chrono conversion, and add line-level scanner-test suppressions. Focused Python tests passed 18/18 and focused C++ tests passed 2/2 before the commit was replayed unchanged onto the source branch.

The additional eight-case matrix dynamically verifies both backend variants of `ServingReady` before `Complete`, migration opening only after current Complete evidence, the shared clear-data retry budget, and best-effort topology callback progression with and without an earlier failure. It does not claim that an already-entered synchronous persistence operation or the complete worker shutdown path is bounded.

## CI 8722 closure

1. Resource projection: restored latest snapshot promotion and current rebalance budget semantics.
2. Etcd local isolation case 1: corrected the injected lease-expiry action from continue-after-call to return-value override.
3. Etcd local isolation case 2: same deterministic injection correction, preserving the first lease-rebuild failure path.
4. Get timeout resource release: released request-owned SHM state and refreshed resource admission.
5. Cross-worker SetNX: limited existing-key location repair to recovered metadata.
6. Routed transport set: restored the TCP expectation for a fixture that explicitly disables SHM.
7. Direct metadata-owner set: restored the same transport contract.
8. Metadata-owner connect-options experiment: restored the upstream `DISABLED_` scope. It is not counted as a dynamic pass.

The first CTest invocation omitted `TEST_SRCDIR` and produced two harness failures before product execution because the mock service script could not be located. The corrected invocation explicitly exported `TEST_SRCDIR`, `TEST_WORKSPACE`, and `DS_OPENSOURCE_DIR`; all seven enabled cases passed. The failed harness run is preserved under `d1ab83a10/gate8.*` and is not counted as product evidence.

## Static gates

- `git diff --check`: PASS.
- Repo-local sensitive scan over the exact staged range: PASS, fail-closed with no bypass.
- `ds-pr-review prepare 1405`: 205 files, 25,918 changed lines, no unscannable file.
- Independent N1 review: no remaining P0/P1/P2.
- No push to the upstream repository and no manual CI retest command.
- Discussions `182319189`, `182319331`, and `182540005` were replied to with verification evidence and re-fetched as resolved.

## Remaining gaps

- This result closes the eight failures reported by the old aarch64 CI run; it does not close all remaining PR review P1 items.
- One baseline transport experiment remains disabled by upstream design.
- ST case latency is still above the desired six-second target for several cluster cases; functional correctness is green, harness optimization remains separate.
- Full repository UT/ST regression was intentionally not repeated for this narrow consolidation. The focused one-shot and affected-case matrices were used.
