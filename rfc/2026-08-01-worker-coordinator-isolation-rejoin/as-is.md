# AS IS Evidence

Baseline: `main/master@a90f6c6b718857367575068c83fb976494f6c751`.

## CodeGraph

Required shared-index commands were attempted from `/home/t14s/workspace/git-repos/yuanrong-datasystem`:

| Command | Result |
|---|---|
| `timeout 30s /home/t14s/.local/bin/codegraph status` | `unable to open database file` |
| `timeout 30s /home/t14s/.local/bin/codegraph query TopologyEngine` | `unable to open database file` |
| `timeout 30s /home/t14s/.local/bin/codegraph callers TopologyEngine::PublishBackendEvidence` | `unable to open database file` |
| `timeout 30s /home/t14s/.local/bin/codegraph impact src/datasystem/cluster/runtime/topology_engine.cpp` | `unable to open database file` |
| `timeout 30s /home/t14s/.local/bin/codegraph affected src/datasystem/cluster/runtime/topology_engine.cpp` | `unable to open database file` |

Graph node and edge counts are unavailable. The index output is not used as design authority; all conclusions below are
from exact HEAD source and build files.

## Existing Coordinator-Backend Behavior

| Evidence | Current behavior | Gap for Measure 2 |
|---|---|---|
| `src/datasystem/cluster/runtime/topology_engine.cpp:1068` | `PublishBackendEvidence` reads an exact topology snapshot and checks whether the local address exists. | Correct trigger point for detecting that the local member was removed. |
| `src/datasystem/cluster/runtime/topology_engine.cpp:1072` | Initial snapshots without the local member become `NOT_READY`. | This behavior should stay. |
| `src/datasystem/cluster/runtime/topology_engine.cpp:1085` | A previously existing local member that disappears logs `action=kill_self signal=SIGKILL` and raises `SIGKILL`. | Must be replaced with rejoin-required and admission closed. |
| `tests/ut/cluster/topology_engine_test.cpp:439` | Death test expects SIGKILL when the local member is removed. | First RED test should replace this with process-survival behavior. |
| `src/datasystem/worker/worker_oc_server.cpp:1275` | Availability handler admits business for `NORMAL` and `CONTROL_DEGRADED`, rejects other levels through `SetTopologyServingAdmission`. | Rejoin-required can reuse existing admission wiring. |
| `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp:1275` | Client-facing object-cache RPCs call `ValidateWorkerState`, which returns `K_NOT_READY` when the worker is unhealthy. | Admission closure naturally rejects ordinary business. |
| `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp:2935` | `GetHashRing` verifies AK/SK and reads the membership snapshot but does not call `ValidateWorkerState`. | Peer topology refresh can use this RPC without reopening ordinary business admission. |

## Existing Recreate And Watch Reset Behavior

| Evidence | Current behavior | Gap for Measure 2 |
|---|---|---|
| `src/datasystem/cluster/coordination_backend/ds_coordination_backend.cpp:448` | `AutoCreateKeepAliveKey` exact-reads current membership and writes the keepalive key. | Recreate is not gated on local cleanup. |
| `src/datasystem/cluster/coordination_backend/ds_coordination_backend.cpp:568` | `HandleMembershipSuccess` invalidates watches when recreated or Coordinator id changed. | Watch reset behavior should be retained after cleanup-gated recreate. |
| `src/datasystem/cluster/coordination_backend/ds_coordination_backend.cpp:638` | Keepalive `K_NOT_FOUND` and `K_TRY_AGAIN` schedule membership reconcile or call `AutoCreateKeepAliveKey(true)`. | Every recreate path must pass the cleanup gate. |

## Existing Cleanup Building Blocks

| Evidence | Current behavior | Reuse rule |
|---|---|---|
| `src/datasystem/master/object_cache/oc_metadata_manager.cpp:3585` | `RemoveMetaByWorker` removes locations and primary entries for one worker. | Reuse as OC metadata narrow cleanup. |
| `src/datasystem/master/object_cache/oc_metadata_manager.cpp:3781` | `ProcessWorkerRestart` removes metadata and async ops, then may push metadata back to the restarted worker. | Do not reuse directly for cold rejoin because reconciliation push is not part of Measure 2. |
| `src/datasystem/master/stream_cache/sc_metadata_manager.cpp:925` | `ClearWorkerMetadata` clears stream metadata for a worker. | Reuse for SC metadata narrow cleanup. |
| `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp:159` | `SubmitTopologyFailureCleanup` materializes failure-scope cleanup, then clears data and rebuilds refs asynchronously. | Do not reuse as cold-rejoin local cleanup because it is survivor failure cleanup with ref rebuild semantics. |
| `src/datasystem/worker/object_cache/service/worker_oc_service_clear_data_flow.cpp:228` | `ClearObject` and `ClearObject(vector<string>)` already remove local object table data. | Reuse the local object clear primitive for a synchronous clear-all entry. |

## Build And Test Surfaces

| Surface | Evidence |
|---|---|
| CMake topology and worker targets | `src/datasystem/cluster/CMakeLists.txt`, `src/datasystem/worker/CMakeLists.txt`, `src/datasystem/worker/object_cache/CMakeLists.txt` |
| Bazel topology and worker targets | `src/datasystem/cluster/BUILD.bazel`, `src/datasystem/worker/BUILD.bazel`, `src/datasystem/worker/object_cache/BUILD.bazel` |
| UT bucket | `tests/ut/cluster/topology_engine_test.cpp`, `tests/ut/cluster/ds_coordination_backend_session_test.cpp`, `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp`, `tests/ut/worker/object_cache/worker_get_hash_ring_test.cpp` |
| ST bucket | `tests/st/worker/object_cache/coordinator_backend_cluster_test.cpp`, Bazel target `//tests/st/worker/object_cache:coordinator_backend_cluster_test` marked `manual` |

## Remote Validation And Cache

`python3 .skills/ds-test/scripts/ds_test.py check-config` reports a configured `tiantiyun-80c128g` target with private
details redacted. Local shallow probes did not find `/home/cache` or `/home/third-party`; before remote builds, confirm
the actual Tiantiyun cache path and set `DS_OPENSOURCE_DIR` to the reusable third-party cache if available.
