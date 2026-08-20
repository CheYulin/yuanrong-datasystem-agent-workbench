# Task 8 final validation record

Validation date: 2026-08-20. Implementation SHA: `f6f5561d9adcd50bbe72f3b392e552b082d174bf`.
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
