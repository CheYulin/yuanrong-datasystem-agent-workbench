# Worker QueryAndGet Fast Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `local_cache=false` 的 metadata-affinity 读取中，以每个目标 Worker 一条 QueryAndGet RPC 同时完成元数据解析与数据返回，并覆盖单 key、多 key、SHM、UB/TCP 和 Worker miss。

**Architecture:** Client 保持现有 `PREFERRED_META_OWNER` 分组，通过 endpoint transporter 的 `BatchQueryAndGet` 选择 SHM/UB/TCP，并调用新增但复用 `GetReqPb/GetRspPb` 的 `WorkerOCService::QueryAndGet`。Worker 的 Get 与 QueryAndGet 共用同一个 typed handler，内部仍由 `WorkerOcServiceGetImpl`、`GetRequest` 和 `WorkerMasterOCApi` 完成全部查找与搬运。

**Tech Stack:** C++17、protobuf、ZMQ/bRPC 自定义 RPC 生成、CMake、GoogleTest、DataSystem inject framework、URMA Mock、dsbench。

**Spec:** `rfc/2026-08-19-worker-query-and-get/detailed-design.md`

## Global Constraints

- DataSystem 基线必须是官方 `main/master` `71fada0780e4f3d5475c7d7a9df1f5ae8e1bd042`；实现前已于 2026-08-20 重新 fetch 确认未移动。
- 新路径严格遵循 Client→Worker→Master；Client 不调用新路径的 Master stub。
- Client 判断同节点并通过 `QueryAndGetShmPb` 告知 Worker；Worker只验证，不反向推断 Client locality。
- 新 RPC 复用 `GetReqPb/GetRspPb`，并且只能追加到 `WorkerOCService` 最后，不能移动任何既有 RPC ordinal。
- Worker 不复制 Get、remote fetch、spill、wait、payload、SHM ref 或 deadline 算法。
- 稳态单目标成功请求：Client QAG=1，Client phase2 single/batch=0；SHM 注册/FD/heartbeat 不计入业务 RPC。
- 远端构建只用 `tiantiyun-80c128g`，CMake `-j80`，三方缓存固定为 `DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache`。
- URMA Mock 只证明功能，不替代真实 HCCS/UB 性能。
- 不 push 到 `openeuler/yuanrong-datasystem`；后续只允许经核验的 yche/yche-huawei fork。

---

### Task 1: Freeze the wire contract and RPC ordinal

**Files:**
- Modify: `src/datasystem/protos/object_posix.proto:191-212`
- Modify: `src/datasystem/protos/object_posix.proto` at the final line of `WorkerOCService`
- Test: `tests/ut/client/transport_test.cpp`
- Test: the nearest proto/RPC generator test under `tests/ut/common/rpc/`

**Interfaces:**
- Produces: `QueryAndGetShmPb`, `GetReqPb.query_and_get_shm = 15`, `GetReqPb.is_routed = 16`
- Produces: `WorkerOCService::QueryAndGet(GetReqPb) -> GetRspPb` as the last service method
- Preserves: `GetReqPb` fields 1-14 and 100-102; all existing WorkerOCService ordinals

- [ ] **Step 1: Write failing protocol tests**

Add tests that serialize/parse a `GetReqPb` containing multiple keys, `request_timeout`, AK/SK fields, Shm marker and
`is_routed`; also snapshot every existing WorkerOCService method index and assert the new method is the only appended item.

- [ ] **Step 2: Run the focused tests and verify RED**

Run the repository's focused proto/RPC UT target. Expected failure: generated `GetReqPb` has no
`mutable_query_and_get_shm`/`set_is_routed`, and service has no QueryAndGet method.

- [ ] **Step 3: Add the minimal proto changes**

```protobuf
message QueryAndGetShmPb {}

message GetReqPb {
  // Keep existing fields exactly as-is.
  QueryAndGetShmPb query_and_get_shm = 15;
  bool is_routed = 16;
}
```

Append the RPC after every existing WorkerOCService method:

```protobuf
rpc QueryAndGet(GetReqPb) returns (GetRspPb) {
  option (datasystem.unary_socket_option) = true;
  option (datasystem.recv_payload_option) = true;
}
```

- [ ] **Step 4: Regenerate through the normal build and run the focused tests**

Expected: protocol roundtrip passes; old fields and RPC ordinals are byte/number stable; QueryAndGet is last.

- [ ] **Step 5: Commit the protocol unit**

```bash
git add src/datasystem/protos/object_posix.proto tests/ut/client/transport_test.cpp tests/ut/common/rpc
git commit -m "feat: add worker query-and-get rpc contract"
```

### Task 2: Share the Worker Get handler without copying data logic

**Files:**
- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.h`
- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp:1648-1655`
- Modify: `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.h`
- Modify: `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp:203-330`
- Test: nearest Worker OC service UT under `tests/ut/worker/object_cache/`

**Interfaces:**
- Produces: `enum class GetRpcKind : uint8_t { GET, QUERY_AND_GET };`
- Produces: `Status WorkerOcServiceGetImpl::Get(ServerGetApi &serverApi, GetRpcKind kind)`
- Consumes: Task 1 `GetReqPb` and QueryAndGet generated service method

- [ ] **Step 1: Write failing Worker tests**

Cover these exact cases: legacy Get still uses `worker::Authenticate`; QAG+Shm marker requires a registered SHM client and
`is_routed=false`; QAG+routed requires no Shm marker and uses `AuthenticateRequest`; missing/contradictory combinations
return `K_INVALID`; both entrypoints enter the same processing injection point exactly once.

- [ ] **Step 2: Run focused Worker tests and verify RED**

Expected: QueryAndGet override/GetRpcKind does not exist.

- [ ] **Step 3: Add the shared typed entrypoint**

Keep `ServerUnaryWriterReader<GetRspPb, GetReqPb>` unchanged. `WorkerOCServiceImpl::Get` calls kind `GET`; the newly
generated `WorkerOCServiceImpl::QueryAndGet` calls kind `QUERY_AND_GET`. Inside the existing function, read the request
once, then select authentication:

```cpp
if (kind == GetRpcKind::GET) {
    RETURN_IF_NOT_OK(worker::Authenticate(akSkManager_, req, tenantId));
} else if (req.has_query_and_get_shm()) {
    CHECK_FAIL_RETURN_STATUS(!req.is_routed(), K_INVALID, "SHM QueryAndGet cannot be routed");
    CHECK_FAIL_RETURN_STATUS(ClientShmEnabled(clientId), K_CLIENT_WORKER_DISCONNECT,
                             "SHM QueryAndGet session is unavailable");
    RETURN_IF_NOT_OK(worker::Authenticate(akSkManager_, req, tenantId));
} else {
    CHECK_FAIL_RETURN_STATUS(req.is_routed(), K_INVALID, "Remote QueryAndGet must be routed");
    RETURN_IF_NOT_OK(worker::AuthenticateRequest(akSkManager_, req, req.tenant_id(), tenantId));
}
```

After this branch, execute the existing request initialization, task-pool dispatch, `ProcessGetObjectRequest`, response
write and payload send unchanged.

- [ ] **Step 4: Run Worker tests and the legacy Get characterization tests**

Expected: new auth matrix passes; legacy Get behavior and SHM ref behavior are unchanged.

- [ ] **Step 5: Commit the Worker facade**

```bash
git add src/datasystem/worker/object_cache tests/ut/worker/object_cache
git commit -m "feat: route worker query-and-get through get core"
```

### Task 3: Add Client RPC invocation and SHM-session QueryAndGet

**Files:**
- Modify: `src/datasystem/client/transport/rpc/worker_rpc_client.h`
- Modify: `src/datasystem/client/transport/rpc/worker_rpc_client.cpp:102-118,207-219`
- Modify: `src/datasystem/client/transport/data_plane/shm_connection.h`
- Modify: `src/datasystem/client/transport/data_plane/shm_connection.cpp:302-330`
- Modify: `src/datasystem/client/transport/data_plane/shm_transporter.h:100-147`
- Test: `tests/ut/client/transport_test.cpp`

**Interfaces:**
- Produces: `InvokeWorkerQueryAndGet(GetReqPb &, GetRspPb &, std::vector<RpcMessage> &)`
- Produces: `ShmSession::QueryAndGet(const DataGetBatchRequest &, GetRspPb &, std::vector<RpcMessage> &)`
- Produces: `ShmTransporter::BatchQueryAndGet(...)`
- Consumes: Task 1 RPC and Task 2 Worker contract

- [ ] **Step 1: Write failing Client RPC and SHM tests**

Assert control stub QueryAndGet dispatch count is one, request contains all keys in order, session client id,
`request_timeout`, Shm marker, `is_routed=false` and signature; response reuses the existing
`ValidateShmResponse/BuildResult` path and Buffer release decrements the same session ref.

- [ ] **Step 2: Run focused Client UT and verify RED**

Expected: the new invocation and session method do not exist.

- [ ] **Step 3: Implement the smallest SHM path**

Refactor the common request builder currently in `ShmSession::Get` into a private helper that accepts whether to set the
Shm marker. Keep legacy `Get` calling `InvokeClientGet`; new `QueryAndGet` sets the marker and calls
`InvokeWorkerQueryAndGet`. Reuse `ValidateShmResponse` and `BuildResult` without copying their bodies.

- [ ] **Step 4: Run the focused UT and legacy ShmTransporter tests**

Expected: QueryAndGet dispatch is one; fd/mmap/ref tests and legacy Get all pass.

- [ ] **Step 5: Commit the Client SHM transport unit**

```bash
git add src/datasystem/client/transport/rpc src/datasystem/client/transport/data_plane tests/ut/client/transport_test.cpp
git commit -m "feat: support worker query-and-get over shm"
```

### Task 4: Route owner groups directly through the Worker fast path

**Files:**
- Modify: `src/datasystem/client/transport/data_plane/i_data_transporter.h`
- Modify: `src/datasystem/client/transport/metadata/object_metadata_client.h`
- Modify: `src/datasystem/client/transport/metadata/object_metadata_client.cpp`
- Modify: `src/datasystem/client/transport/object_read/object_read_flow.cpp:77-92,267-283`
- Test: `tests/ut/client/transport_test.cpp`

**Interfaces:**
- Produces: `virtual Status BatchQueryAndGet(const DataGetBatchRequest &, DataGetBatchResult &)` with a default
  `K_NOT_SUPPORTED` implementation for transports not yet migrated
- Produces: `ObjectMetadataClient::QueryAndGet` fast path that fills `ObjectMetadataItem::inlineData`
- Consumes: `DataPlaneManager`, `TransportAdvisor`, Task 3 SHM transporter

- [ ] **Step 1: Add failing single/multi-owner flow tests**

For single and multiple keys in one owner group, mock `BatchQueryAndGet` to return data and assert Worker QAG=owner count,
metadata Master QAG=0, phase2 single=0, phase2 batch=0 and request order restored. For K_NOT_SUPPORTED before dispatch,
assert the old metadata+replica path runs exactly once with the same remaining deadline.

- [ ] **Step 2: Run the flow tests and verify RED**

Expected: metadata client still invokes Master QAG and phase2.

- [ ] **Step 3: Implement direct owner-group execution**

Build `DataGetBatchRequest` from each `ObjectMetadataBatch`, acquire the owner transport using the existing advisor hint,
call `BatchQueryAndGet`, and move each successful `DataGetResult` into the corresponding `inlineData`. Do not synthesize
locations. Preserve batch item status and index mapping. Only fall back when capability is known absent before dispatch;
transport timeout/cancel/write failure returns as the request result.

- [ ] **Step 4: Add explicit phase2 injection points and run tests GREEN**

Place `client.transport.phase2_single_enter` immediately before `ReplicaReader::Read` and
`client.transport.phase2_batch_enter` immediately before `ReplicaReader::ReadBatch`. Verify successful fast-path items
are skipped and errors remain per item.

- [ ] **Step 5: Commit the read-flow fast path**

```bash
git add src/datasystem/client/transport tests/ut/client/transport_test.cpp
git commit -m "feat: query metadata owner worker directly on get"
```

### Task 5: Complete routed TCP and UB transports

**Files:**
- Modify: `src/datasystem/client/transport/data_plane/tcp_transporter.h`
- Modify: `src/datasystem/client/transport/data_plane/tcp_transporter.cpp`
- Modify: `src/datasystem/client/transport/data_plane/ub_transporter.h`
- Modify: `src/datasystem/client/transport/data_plane/ub_transporter.cpp`
- Modify: `src/datasystem/client/transport/rpc/worker_rpc_client.cpp`
- Test: `tests/ut/client/transport_test.cpp`

**Interfaces:**
- Produces: `TcpTransporter::BatchQueryAndGet`
- Produces: `UbTransporter::BatchQueryAndGet`
- Consumes: Task 3 signed Worker QAG invocation and existing GetRsp payload semantics

- [ ] **Step 1: Write failing TCP/UB batch tests**

TCP: multi-key success, mixed found/not-found, object-index validation and payload part indices. UB: one receive buffer,
cumulative successful-item offsets, buffer-capacity fallback to TCP payload, provider error detail, cancellation and mixed
UB/TCP items. Every request must set `is_routed=true` and omit Shm marker.

- [ ] **Step 2: Run focused transport UT and verify RED**

Expected: TCP/UB use existing replica RPCs and do not expose `BatchQueryAndGet`.

- [ ] **Step 3: Implement TCP using existing GetRsp parsers**

Build complete `GetReqPb` including keys, auth context, `sub_timeout`, monotonically decreasing `request_timeout`,
`return_object_index=true` and `is_routed=true`; call Worker QAG once. Move RPC payload parts into `DataGetResult` using
the same bounds and duplicate-index checks as existing batch Get.

- [ ] **Step 4: Implement UB using existing buffer ownership**

Allocate the existing configured UB receive buffer before dispatch, set `urma_info/ub_buffer_size`, and interpret
`payload_info` in object-index order. Successful UB items consume cumulative data sizes from the registered buffer; items
with RPC payload parts use existing TCP fallback ownership. Do not claim UB when the response actually carries payload.

- [ ] **Step 5: Run TCP/UB tests and commit**

```bash
git add src/datasystem/client/transport/data_plane src/datasystem/client/transport/rpc tests/ut/client/transport_test.cpp
git commit -m "feat: support routed worker query-and-get transports"
```

### Task 6: Add capability cache, bounded fallback and DFX

**Files:**
- Modify: `src/datasystem/common/flags/common_flag_define.cpp`
- Modify: `src/datasystem/common/flags/common_flags.h`
- Modify: `src/datasystem/client/transport/data_plane/data_plane_manager.h`
- Modify: `src/datasystem/client/transport/data_plane/data_plane_manager.cpp`
- Modify: `src/datasystem/client/transport/rpc/worker_rpc_client.h`
- Modify: `src/datasystem/client/transport/rpc/worker_rpc_client.cpp`
- Modify: relevant metrics/trace definition files found by `rg -n "CLIENT_DIRECT_QUERY_AND_GET|KvMetricId" src`
- Test: `tests/ut/client/transport_test.cpp`

**Interfaces:**
- Produces: `enum class WorkerQueryAndGetCapability { UNKNOWN, SUPPORTED, UNSUPPORTED }` cached per RPC client generation
- Produces: `enable_worker_query_and_get` internal gray flag, default false
- Produces: enumerated fallback reasons and QAG/phase2/owner-group counters
- Consumes: Task 4 pre-dispatch fallback boundary

- [ ] **Step 1: Write failing capability/state-machine tests**

Cover gray flag off, supported, known unsupported, unknown, method-not-found-before-handler, timeout after dispatch,
cancel and response-write failure. Flag off/known unsupported skip dispatch; UNKNOWN method-not-found may fall back once;
all possibly-executed errors return without replay. Assert deadline never increases.

- [ ] **Step 2: Run tests RED**

Expected: no capability or explicit fallback state exists.

- [ ] **Step 3: Implement the per-connection tri-state cache**

Each `WorkerRpcClient` generation starts UNKNOWN. A successful QueryAndGet sets SUPPORTED. Only the generated RPC layer's
explicit unknown-method status—which proves the appended ordinal never entered a handler—sets UNSUPPORTED and permits
old-path fallback. Timeout, cancel, unavailable after dispatch, response write/payload failure and business status never
change capability and never replay. New connection generation resets UNKNOWN; `DataPlaneManager::Teardown` removes it.
The default-false gray flag bypasses the new path before dispatch and enables same-binary A/B.

- [ ] **Step 4: Add low-cardinality DFX and run tests GREEN**

Client counters: attempts/completed, cumulative owner-group objects, phase2 single/batch, fallback enum and actual
transport. The owner-group signal is a counter so enabled hot-path groups do not contend on the shared histogram mutex;
average objects per dispatched group can be derived from the cumulative object and attempt counters.
Worker counters: requests, complete/partial, QueryMeta/RemoteGet fanout, returned bytes and ref reconciliation. Labels are
only enums; never key/client/endpoint/token.

- [ ] **Step 5: Commit capability/DFX**

```bash
git add src/datasystem/common/flags src/datasystem/client tests/ut/client/transport_test.cpp
git commit -m "feat: gate worker query-and-get by endpoint capability"
```

### Task 7: Add focused ST use cases and exact RPC gates

**Files:**
- Modify: `tests/st/client/kv_cache/kv_client_transport_get_test.cpp`
- Modify: `tests/st/CMakeLists.txt` only if a new source/target is strictly required

**Interfaces:**
- Consumes: Tasks 1-6 complete fast path and counters
- Produces: UC1-UC9 regression coverage

- [ ] **Step 1: Add metadata-affinity full-hit single/multi-key STs**

Set writer `enableLocalCache=false` and `PREFERRED_META_OWNER`; reader `enableLocalCache=false`. Prove owner=data Worker
with ring/injection evidence. Warm SHM session, then assert QAG delta=1, both phase2 deltas=0, registration/FD delta=0,
transport=SHM and values/order correct.

- [ ] **Step 2: Add owner-miss and cross-node cases**

Use `PREFERRED_SAME_NODE` or the existing key/worker helper to place data away from owner. Assert Client QAG=1,
phase2=0, while Worker QueryMeta/RemoteGet counters prove UC3/UC4. Add same-owner partial hit and multi-owner group-count
assertions.

- [ ] **Step 3: Add failure/lifecycle cases**

Cover not-found, timeout, stale ring, invalid/expired SHM session generation, UB provider failure/TCP fallback, duplicate
keys, max batch+1, response cancellation and Buffer release/ref convergence. Avoid fixed sleeps; use bounded observable
polling where reconciliation is asynchronous.

- [ ] **Step 4: Add rolling-version tests**

Verify old Client→new Worker ignores fields, new Client→old Worker uses known-unsupported fallback before dispatch, and
ZMQ/bRPC method ordinals never misroute.

- [ ] **Step 5: Run the focused ST locally only for discovery if no build is needed; commit tests**

Actual compilation/execution is reserved for Tiantiyun in Task 8.

```bash
git add tests/st/client/kv_cache/kv_client_transport_get_test.cpp tests/st/CMakeLists.txt
git commit -m "test: cover worker query-and-get use cases"
```

### Task 8: Refresh CodeGraph, validate on Tiantiyun and measure

**Files:**
- Modify if needed: affected `BUILD.bazel`/CMake files found by exact-head dependency analysis
- Create in workbench: `rfc/2026-08-19-worker-query-and-get/validation.md`
- Update: `.repo_context` object-cache module only if source responsibilities/build/test paths changed

**Interfaces:**
- Consumes: all implementation tasks
- Produces: reproducible functional/build/performance evidence

- [ ] **Step 1: Refresh CodeGraph in the implementation worktree**

Run `codegraph sync/index`, then query/callers/impact on `WorkerOcServiceGetImpl::Get`,
`WorkerRpcClient::InvokeWorkerQueryAndGet`, `ObjectMetadataClient::QueryAndGet`, plus `affected` for every modified source
file. Record missing/stale/timed-out results and confirm with exact-head `rg` and BUILD/CMake.

- [ ] **Step 2: Run static and generated-code gates**

Run diff check, formatting/codecheck, proto ZMQ/bRPC generation and affected Bazel dependency analysis. Do not call an
incompatible Bazel bootstrap failure a product failure.

- [ ] **Step 3: Build non-URMA CMake on Tiantiyun**

Use an isolated remote source/build directory and:

```bash
DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache bash build.sh -t build -j80
cmake --build build --target ds_ut ds_ut_object ds_st_kv_cache -j80
```

Run focused Client/Worker UT plus TCP and real same-host SHM ST filters. Record cache value from `CMakeCache.txt`, target
times and logs.

- [ ] **Step 4: Build URMA Mock CMake on Tiantiyun**

Use the exact `build.sh --help` supported URMA Mock option (`-U on` if confirmed at this HEAD), same cache and `-j80`.
Run UB success/fallback/buffer/event lifecycle filters plus the SHM fixture. Clearly label this functional Mock evidence.

- [ ] **Step 5: Run dsbench SHM A/B**

Same binary, feature gate off/on, sizes 1 KiB/128 KiB/512 KiB/8 MiB, batch 1/8/32, concurrency 1x1 and 8x16, single
owner and three owners, hit and 50% miss. Warm then run at least five alternating AB/BA rounds. Record P50/P99/PMax,
TPS, MiB/s, errors and actual transport. Required single-owner target: P99 improves at least 3% and throughput at least
5%; otherwise investigate noise/regression before enabling.

- [ ] **Step 6: Run repository self-verification and commit evidence**

Run `$ds-self-verify`; confirm exact HEAD, diff scope, hot-path allocations/copies/locks, auth boundary, deadline/ref
lifetime, tests, CMake/Bazel closure and `.repo_context` freshness. Commit validation/context updates separately.

### Task 9: Issue, PR and review closure

**Files:**
- Update: workbench `validation.md` with final Issue/PR/review links and exact SHAs

**Interfaces:**
- Consumes: green Task 8 evidence
- Produces: user-fork branch, Issue, PR and closed review findings

- [ ] **Step 1: Use the repository issue/PR skills**

Create a source-backed Issue, then use `$ds-create-pr` for a Chinese PR description with architecture, PB compatibility,
use cases, RPC gates, build matrix, performance evidence, rollout and rollback.

- [ ] **Step 2: Verify push target before any push**

Resolve the selected remote URL and prove it is the user's yche/yche-huawei fork. Any openeuler URL is forbidden.

- [ ] **Step 3: Run `$ds-pr-review` and fix every accepted finding**

Use prepare→dry-run→publish→prepare, recheck PR head before publish, then rerun focused tests and self-verification after
fixes. Publish only anchored inline findings; preserve the exact review fingerprints.

- [ ] **Step 4: Final topology and evidence audit**

Verify local/fork/PR SHA equality, ahead/behind, clean worktree, terminal gate states, and distinguish Mock proof from
real SHM/HCCS performance evidence.
