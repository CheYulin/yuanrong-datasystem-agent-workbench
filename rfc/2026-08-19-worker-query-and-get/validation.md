# Task 8 final validation record

Validation date: 2026-08-20. Final implementation SHA: `aaef87b2b29e199d56269ea2f6782b66b40ca2c2`,
based on `master@18bbb2051f2ef7390d0b6c8086d644a53b09284d`.
All remote configure/build/test commands used `tiantiyun-80c128g`, CMake, `-j80`, and
`DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache`; sources/builds were isolated. No production edit occurred.

## Source and CodeGraph gates

The exact implementation worktree was clean and at the required SHA. Exact source inspection verifies the
append-only `QueryAndGetShmPb`, `GetReqPb` fields 15/16 and `WorkerOCService::QueryAndGet` in
`src/datasystem/protos/object_posix.proto`; proto CMake generation; and
`worker_query_and_get_adapter.{h,cpp}` in both client CMake and Bazel registrations.

`timeout 30s codegraph index .` did not finish (stopped during parsing); the immediate `codegraph status .`
reported the exact-worktree index up-to-date: 2,224 files, 57,182 nodes, 183,081 edges. Thirty-second-capped
query/callers/impact completed for `WorkerOcServiceGetImpl::Get`,
`WorkerRpcClient::InvokeWorkerQueryAndGet`, `ObjectMetadataClient::QueryAndGet`, and each SHM/TCP/UB
`BatchQueryAndGet`. It finds expected client/transport tests but under-approximates generated RPC and virtual
dispatch. Its modified-production depth-5 `affected` result was `No test files affected`; it is discovery only,
not selection proof. Exact source/build closure is authoritative.

## CMake evidence

| Configuration | Cache | Result | Duration |
| --- | --- | --- | --- |
| URMA Mock fresh | `BUILD_WITH_URMA=off`, `BUILD_WITH_URMA_MOCK=on` | `build.sh -t build -U mock -B build-urma -i on -j80` reached 99%, then `ds_ut` link hit `No space left on device` | 10m51.15s, disk/infrastructure failure |
| URMA Mock resume | preserved isolated cache/source | `cmake --build build-urma -j80` passed | 20.45s |
| URMA Mock required | same | `cmake --build build-urma --target ds_ut ds_ut_object ds_st_kv_cache -j80` passed | 6.53s |
| non-URMA fresh | `BUILD_WITH_URMA=off`, `BUILD_WITH_URMA_MOCK=off` | `build.sh -t build -U off -B build-nourma -i on -j80` passed | 11m23.34s |
| non-URMA required | same fresh isolated cache/source | `cmake --build build-nourma --target ds_ut ds_ut_object ds_st_kv_cache -j80` passed | 7.38s |

The initial `ds-test run-remote` wrapper is excluded: its remote script assigned zsh's read-only `status` name.
One later local output decoder also failed. Both are harness/capture failures, not product evidence. Accepted
commands used binary capture decoded with `errors=replace` and preserved the formal roots until completion.

## Functional results

| Configuration | Focus | Result |
| --- | --- | --- |
| URMA Mock | `*WorkerQueryAndGet*:*BatchQueryAndGet*` | 32 passed, 0.40s |
| URMA Mock | legacy WorkerRpc/ObjectMetadata/ObjectRead/TCP/UB/SHM filters | 151 passed, 0.61s |
| URMA Mock | isolated Worker metrics case | 1 passed, 0.05s |
| URMA Mock | Task 1/7 exact Worker matrix | 8 passed, 0.07s |
| URMA Mock | `KVClientWorkerQueryAndGetStTest.*`, `TEST_SRCDIR`/`TEST_WORKSPACE=.` | 3 passed, 18.323s |
| non-URMA | focused TCP/SHM/object-read QAG UT | 15 passed, 0.16s |
| non-URMA | legacy TCP/SHM/object-meta/object-read UT | 76 passed, 0.16s |
| non-URMA | real same-host SHM single+same-owner batch ST | 1 passed, 8.493s |

The real non-URMA ST uses `enableLocalCache=false` and `PREFERRED_META_OWNER`; it asserts actual `SHM`, one
Worker QAG per owner group, client phase2 zero, and no extra SHM registration/FD RPC. TCP coverage is non-URMA
component/UT evidence, not a TCP end-to-end ST. URMA Mock remains functional/lifecycle only, not HCCS performance.

An exploratory broad Worker filter combined unrelated global injection state and aborted with signal 6. The exact
metrics case and explicit Task 1/7 matrix pass in isolation, identifying cross-test injection state rather than a
reproducible product defect; no production fix was made.

## Static/self-review and remaining acceptance

- `git diff --check 71fada0780e4f3d5475c7d7a9df1f5ae8e1bd042 HEAD` and `git diff --check HEAD`: clean.
- `python3 scripts/ai_context/validate_module_metadata.py`: `validated 18 module metadata files`.
- Exact diff review covered hot-path latency/allocations/copies, synchronization/lifetime, remaining deadline,
  no-replay fallback, metrics cardinality, compatibility/ordinal, and CMake/Bazel parity.
- Diff/commit-message sensitive scan found only schema/test identifiers containing `token`, not credential material.
  Generated proto/bRPC/ZMQ closure is exercised by the successful CMake builds and registrations above.
- No relevant repository `codecheck` target was found; no synthetic substitute is claimed.

Builds were idle for the audit. Existing `kvtest` can set `enable_local_cache=false` and
`PREFERRED_META_OWNER`, but it has no checked-in public-Client OFF/ON QAG harness with identical workload and
throughput/mean/P50/P99/max samples; the current QAG ST fixture always forces gate ON. The required alternating
performance experiment is therefore not represented by this validation and is handed off for the planned minimal
public-Client benchmark harness. No performance benefit or hardware/URMA claim is made here.

Rollout remains Draft and the gate defaults off. Rollback is `enable_worker_query_and_get=false`; no schema or
service-ordinal rollback is appropriate after release.

## Task 8 manual public-Client SHM performance gate

The focused manual gate is in final rebased commit `783f1196d4f21d597efd7487b01e42eaa5d4af9a`
(range-diff equivalent to pre-rebase commit `80fdce9b03b7f4614032e1d844579f9dcfe0d16f`) as
`KVClientWorkerQueryAndGetStTest.DISABLED_WorkerQueryAndGetShmOffOnPerformanceGate`. It is a disabled ST, so normal
CTest/default suites do not run it. The implementation reuses the existing real-cluster QAG ST fixture rather than
adding a private RPC or synthetic transporter: both writer and reader have `enableLocalCache=false` and
`PREFERRED_META_OWNER`; owner-write injections prove the selected metadata owner is the data Worker; every measured
public `KVClient::Get` verifies value/order and asserts actual `SHM` transport.

### TDD and build evidence

All commands below ran only on `tiantiyun-80c128g`, using the isolated non-URMA CMake tree, `-j80`, and
`DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache`.

| Gate | Command/result |
| --- | --- |
| tests-only RED | Added only the disabled test calling the absent measurement helper; `cmake --build build-nourma --target ds_st_kv_cache -j80` failed after 10.03 s with the expected undeclared-helper diagnostic. |
| Green compile | The same target compiled and linked after the helper was added (17.69 s compilation, 6.80 s link). |
| Focused correctness before | `TEST_SRCDIR=$PWD TEST_WORKSPACE=. ./build-nourma/tests/st/ds_st_kv_cache --gtest_filter="KVClientWorkerQueryAndGetStTest.SameNodeShmSingleAndBatchUseOneWorkerQueryAndGetWithoutPhase2OrReRegistration"`: 1/1 passed. |
| Manual performance gate | The same binary with `--gtest_filter="KVClientWorkerQueryAndGetStTest.DISABLED_WorkerQueryAndGetShmOffOnPerformanceGate" --gtest_also_run_disabled_tests`: 1/1 passed. |
| Focused correctness after | The same focused correctness filter: 1/1 passed. |

The performance process was launched only after a remote process probe found no active `build.sh`, `cmake --build`,
`make`, `ninja`, or `ctest` job (only unrelated completion-monitor shells). No production source/configuration changed.

### Fixed workload and per-run output

Each workload uses payload `131072` bytes, 100 API calls, concurrency 1, and either one key or eight keys selected to
the same metadata owner. Objects, metadata route, worker connection and SHM session are warmed before measurements.
The one binary and cluster switch only `enable_worker_query_and_get`; order is OFF/ON, ON/OFF, OFF/ON.

| Rep | Gate | Workload | elapsed us | ops/s | mean us | P50 us | P99 us | max us | Worker QAG | Master QAG | phase2 single | phase2 batch |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | OFF | single | 162220.884 | 616.443 | 1621.127 | 1621.918 | 1998.516 | 2055.584 | 0 | 100 | 100 | 0 |
| 1 | OFF | same-owner 8-key | 353794.872 | 282.650 | 3536.755 | 3442.802 | 4338.420 | 4423.236 | 0 | 100 | 0 | 100 |
| 1 | ON | single | 90456.530 | 1105.503 | 903.856 | 902.059 | 1044.230 | 1080.425 | 100 | 0 | 0 | 0 |
| 1 | ON | same-owner 8-key | 235042.985 | 425.454 | 2349.640 | 2302.468 | 2907.349 | 2958.043 | 100 | 0 | 0 | 0 |
| 2 | ON | single | 90397.156 | 1106.229 | 903.323 | 923.366 | 1109.951 | 1119.345 | 100 | 0 | 0 | 0 |
| 2 | ON | same-owner 8-key | 241451.278 | 414.162 | 2413.670 | 2326.655 | 3084.341 | 3141.268 | 100 | 0 | 0 | 0 |
| 2 | OFF | single | 158423.843 | 631.218 | 1583.050 | 1646.890 | 2058.134 | 2069.916 | 0 | 100 | 100 | 0 |
| 2 | OFF | same-owner 8-key | 369153.132 | 270.890 | 3690.223 | 3683.505 | 4381.041 | 4448.072 | 0 | 100 | 0 | 100 |
| 3 | OFF | single | 130885.813 | 764.025 | 1308.057 | 1307.661 | 1450.457 | 1554.801 | 0 | 100 | 100 | 0 |
| 3 | OFF | same-owner 8-key | 320726.051 | 311.793 | 3206.169 | 3157.223 | 3867.992 | 4034.091 | 0 | 100 | 0 | 100 |
| 3 | ON | single | 90855.983 | 1100.643 | 907.847 | 905.170 | 1053.765 | 1054.671 | 100 | 0 | 0 | 0 |
| 3 | ON | same-owner 8-key | 214392.921 | 466.433 | 2143.017 | 2084.267 | 3269.299 | 4248.477 | 100 | 0 | 0 | 0 |

### Median summary and interpretation

| Gate | Workload | median ops/s | median mean us | median P50 us | median P99 us | median max us |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| OFF | single | 631.218 | 1583.050 | 1621.918 | 1998.516 | 2055.584 |
| ON | single | 1105.503 | 903.856 | 905.170 | 1053.765 | 1080.425 |
| OFF | same-owner 8-key | 282.650 | 3536.755 | 3442.802 | 4338.420 | 4423.236 |
| ON | same-owner 8-key | 425.454 | 2349.640 | 2302.468 | 2907.349 | 3141.268 |

All 12 runs satisfy the routing invariant: ON makes one Worker QAG per same-owner group per public API call and no
legacy Master QAG/phase2 call; OFF makes no Worker QAG and observes the legacy Master QAG plus the appropriate phase2
single or batch call. The three alternating repetitions are directionally consistent (ON has higher throughput and
lower mean/P50/P99 for both workloads), but this is not a statistically strong result and does not establish a hardware,
multi-concurrency, URMA, or HCCS performance claim. Scheduler/cache/environment noise remains unquantified.

## Final latest-master refresh

After the PR was rebased a second time, `git merge-base HEAD main/master` was exactly
`18bbb2051f2ef7390d0b6c8086d644a53b09284d`; `git rev-list --left-right --count main/master...HEAD` was `0 9`, and
range-diff showed all nine feature/test/context commits equivalent to the immediately preceding rebase. The final
fork and PR head both resolve to `aaef87b2b29e199d56269ea2f6782b66b40ca2c2`.

The preserved Tiantiyun source was synchronized to this exact head. Using CMake, `-j80`, and
`DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache`, the latest-base required-target rebuilds passed:

| Configuration | Required targets | Result |
| --- | --- | --- |
| URMA Mock | `ds_ut ds_ut_object ds_st_kv_cache` | PASS, 8m38.10s |
| non-URMA | `ds_ut ds_ut_object ds_st_kv_cache` | PASS, 8m31.21s |

Final focused executions on the same exact source also passed:

| Configuration | Focus | Result |
| --- | --- | --- |
| URMA Mock | Client `*WorkerQueryAndGet*:*BatchQueryAndGet*` | 32/32, 0.28s |
| URMA Mock | Worker `QueryAndGet`/`GetRequest` matrix | 7/7, 0.08s |
| URMA Mock | real-cluster `KVClientWorkerQueryAndGetStTest` excluding disabled gate | 3/3, 17.94s |
| non-URMA | disabled OFF/ON performance gate | 1/1, 7.77s |

The final performance rerun kept the same 128 KiB, concurrency-1, 100-call, three-alternating-run contract. Its
median results were: single key OFF 623.4 versus ON 1129.1 ops/s, P99 1985.8 versus 1150.3 us; same-owner eight-key
OFF 262.8 versus ON 468.7 ops/s, P99 4322.8 versus 2591.1 us. Every run retained the routing-count contract. An
independent static review of the exact final performance commit was CLEAN. These remain directional same-host SHM
figures only, with no statistical-significance, multi-concurrency, URMA/HCCS, or hardware claim.

Final CodeGraph `sync .` completed in 5.3s and reported up-to-date: 2,237 files, 57,497 nodes, 179,209 edges. Query,
callers and depth-5 impact located the RPC implementation plus TCP/UB callers and focused tests, but still omitted the
exact-source SHM call; `affected` returned no tests for the changed production file set. These are documented graph
under-approximations; exact-source, CMake registration, and the executions above remain the acceptance evidence.

## Independent PR review remediation

The six-round exact-source review found one duplicated P1 and three P2 issues. Follow-up commit
`ae7358bc6a83457adae71aef0663733bcc9a92ca` closes them as follows:

- endpoint generation churn now checks the caller API deadline on every lease reacquisition and yields between
  `K_TRY_AGAIN` attempts. The tests-first RED exhausted 10,000 injected retries, incorrectly entered the operation,
  and returned success after 83 ms; the fixed deadline/reset/teardown set passes 3/3 in 0.10s;
- known capability generations use an atomic steady-state reservation path, while first-probe waiters use bthread
  mutex/condition-variable primitives;
- owner-group observability is a lock-free cumulative object counter, replacing the global histogram mutex on every
  enabled fast-path group; and
- reservation construction/destruction and reserve/run operations are restricted to the Worker RPC client and its
  `DataPlaneManager` friend, preventing callers from separating a raw reservation pointer from its owner lifetime.

After these changes the exact final source passed URMA Mock required targets in 2m34.95s and non-URMA required targets
in 7m13.58s. Focused Client UT passed 33/33 (0.29s), Worker UT 7/7 (0.07s), real-cluster SHM ST 3/3 (22.83s), and the
non-URMA disabled performance gate 1/1 (8.57s). Its final directional medians were single-key OFF 604.6 versus ON
1039.5 ops/s (P99 2019.9 versus 1194.8 us), and same-owner eight-key OFF 247.7 versus ON 424.6 ops/s (P99 4717.3
versus 3076.2 us). The scope limitations above remain unchanged.

The post-fix CodeGraph sync completed and reported 2,237 files, 57,499 nodes and 179,218 edges. The GitCode review
bundle was re-prepared at the final remote head, but its API patch for `transport_test.cpp` remained truncated
(bundle +1656/-0 versus exact diff +1685/-29). Reviewers therefore used the bundle for policy/range and exact
base-to-head source for complete evidence; no finding is published from a missing bundle fragment.

The follow-up concurrency/API re-review then found a second P1 inside `AcquireWorkerQueryAndGetLease`: its internal
observe-then-validate retry could stay inside the helper and bypass the outer deadline/yield. Commit
`84ae47d6daec6786f1760c15152738ae146f929e` adds deadline checks and bthread yield only on the failed-validation and
rebuild retry paths, leaving the steady successful lease path without an added time read or lock. A deterministic
test-only transporter invalidation between the two validations reproduced the old behavior: after deadline expiry it
returned success and executed the operation once. The same test is GREEN after the fix. The first version used a
production inject hook to coordinate the race; review identified its test-build shared-lock cost, so final commit
`aaef87b2b29e199d56269ea2f6782b66b40ca2c2` removes that hot-path hook and keeps the seam entirely in the fake
transporter. On the exact final source, incremental
URMA Mock and non-URMA `ds_ut` builds passed; Client QAG/BatchQAG passed 34/34 in each configuration (0.29s each),
Worker QAG passed 8/8 (0.07s), and the real same-host SHM ST passed 3/3 (18.09s). Final CodeGraph sync reports 2,237
files, 57,500 nodes and 179,224 edges; depth-5 impact includes both deadline tests and the reset/teardown tests.

## Final completion revalidation

On 2026-08-20 the two preserved Tiantiyun build roots were audited before reuse. Their CMake caches still selected
the required configurations, but twelve post-`f6f5561d` source files differed from final `aaef87b2`. The complete
tracked final tree was therefore synchronized into both isolated roots. A SHA-256 aggregate over all 41 PR-changed
files matched the local exact worktree in both roots before building.

Both rebuilds ran on `tiantiyun-80c128g` with CMake, `-j80`, and
`DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache`:

| Configuration | Required targets | Fresh result |
|---|---|---|
| URMA Mock (`BUILD_WITH_URMA_MOCK=on`) | `ds_ut ds_ut_object ds_st_kv_cache` | PASS, 8m42.52s |
| non-URMA (`BUILD_WITH_URMA_MOCK=off`) | `ds_ut ds_ut_object ds_st_kv_cache` | PASS, 8m44.25s |

Fresh focused executions on the same synchronized source passed:

| Configuration | Focus | Result |
|---|---|---|
| URMA Mock | Client QAG/BatchQAG | 34/34, 0.29s |
| URMA Mock | Worker `QueryAndGet*` | 7/7 |
| URMA Mock | real-cluster SHM hit, owner miss, partial/multi-owner | 3/3, 21.53s |
| non-URMA | Client QAG/BatchQAG | 34/34, 0.29s |
| non-URMA | real SHM correctness before and after performance | 1/1 each |
| non-URMA | disabled OFF/ON public-Client gate | 1/1, 8.16s |

The fresh performance medians remained directionally positive and exceeded the scoped PR thresholds:

| Workload | OFF ops/s | ON ops/s | Throughput change | OFF P99 | ON P99 | P99 change |
|---|---:|---:|---:|---:|---:|---:|
| single key | 766.971 | 946.026 | +23.3% | 1481.579 us | 1229.408 us | -17.0% |
| same-owner 8-key | 284.478 | 414.024 | +45.5% | 4270.698 us | 3217.938 us | -24.6% |

Every OFF run recorded Worker QAG 0, Master QAG 100, and the matching phase2 count 100. Every ON run recorded
Worker QAG 100, Master QAG 0, and both phase2 counts 0. This is the required local-cache-disabled,
metadata-affinity, real same-host SHM one-RPC evidence. It remains a three-run directional gate, not the extended
scale matrix or a real URMA/HCCS result; those release boundaries are retained in `detailed-design.md` §8.3.
