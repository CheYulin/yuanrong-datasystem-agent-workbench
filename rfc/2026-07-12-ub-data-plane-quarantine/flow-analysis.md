# UB Data Plane Quarantine Flow Analysis

**Status**: Draft  
**Date**: 2026-07-12  
**Source repo**: `/tmp/yrds-main-master-ddba6454`  
**Source commit**: `ddba645424a857bbbd14d256cb0b97d3c155ac4f`  
**CodeGraph index**: 1,885 files indexed, 45,275 nodes, 141,050 edges, 1 parse failure

This note records the read/write, migration, and rebalance flows on latest
`main/master`, with emphasis on client-worker and worker-worker RPC/data-plane
paths. The goal is to find every place where UB failure can otherwise be hidden
by retry or TCP fallback, and define where quarantine should stop new writes,
remote pulls, and migration target selection.

## 1. Summary

Current code already has several useful transport signals:

- Client UB connect and reconnect failures return `K_URMA_CONNECT_FAILED` or
  `K_URMA_NEED_CONNECT`.
- Worker remote get and batch remote get already surface `K_URMA_WAIT_TIMEOUT`,
  `K_URMA_CONNECT_FAILED`, and `K_URMA_NEED_CONNECT`.
- Worker-to-client and worker-to-worker data transfers can fallback to TCP
  payloads when UB write fails.
- Migration and rebalance use existing worker connection checks, but target
  selection is still membership/resource based, not data-plane-health based.

The first implementation should introduce a per-destination
`WorkerUbPathHealth` and consult it at admission points before issuing data
movement. Once a destination worker is UB-quarantined, default behavior is:

- block new writes to that worker;
- do not silently TCP-fallback writes;
- if fallback is explicitly enabled, allow it only for payloads up to 1 MiB and
  still record the UB failure;
- skip the worker as migration/rebalance target;
- fail reads fast if the only required source is quarantined;
- fail ShmOnly/local-SHM writes instead of committing data that other
  clients/workers cannot read through UB;
- recover only after explicit probes pass.

## 2. PR1277 RDMA Resource Context

PR1277 head (`751f4cbc`, `feat: reuse URMA jetty resources`) changes the
send-side resource model in `src/datasystem/common/rdma`:

- `UrmaResource` owns process-level URMA resources and, after the PR, a pool of
  reusable local send Jettys.
- `UrmaConnection` still represents the remote peer and imported remote target
  Jetty/segments; local send Jetty ownership moves away from the connection.
- `UrmaSendLaneLease` represents one borrowed send lane for a business
  transfer. All chunks/events share the lease; the lane is released after all
  events settle or retired after timeout/failure.
- `UrmaEvent` carries remote address, operation type, data size, completion
  status, and the lane lease, which makes it a good source of structured
  operation outcome data.
- `WaitToFinish` and completion polling decide whether to release or retire the
  lane, and may trigger local Jetty recovery/recreation for selected CQE
  statuses.

Implication for UB isolation:

- `common/rdma` should keep doing local resource cleanup, lane retirement, and
  Jetty recreation. It should not decide object-cache write eligibility.
- Object/cache, migration, and rebalance callers should consume URMA outcomes
  through wrapper/admission logic and update `WorkerUbPathHealth`.
- CQE/provider ERROR 4, handshake failure, reconnect failure, and repeated
  timeout are data-plane path failures and can mark a peer `UNAVAILABLE`.
- Send Jetty pool exhaustion (`K_TRY_AGAIN` style local pressure) should be
  handled as fast failure/backpressure, not as remote worker quarantine.
- If PR1277 lands before this feature, prefer adding a small structured outcome
  accessor/callback around `UrmaEvent` instead of parsing status text in each
  business caller.

## 3. Existing Flow: Client -> Worker Write

Main path:

1. `ObjectClientImpl::Put` selects an available worker API.
2. `ObjectClientImpl::ProcessShmPut` calls `IClientWorkerApi::Create`.
3. Worker `WorkerOcServiceCreateImpl::CreateImpl` allocates object memory and,
   when client SHM is not enabled and URMA is enabled, fills `urmaDataInfo` for
   the client.
4. Client copies data:
   - SHM when local shared memory is enabled.
   - UB through `Buffer::MemoryCopyWithTransport` and
     `ClientWorkerBaseApi::SendBufferViaUb`.
   - TCP payload when UB data was not sent or fallback payload is attached.
5. Client calls `IClientWorkerApi::Publish` or `MultiPublish` to commit object
   metadata and optional payload.

Important files:

- `src/datasystem/client/object_cache/object_client_impl.cpp`
- `src/datasystem/client/object_cache/client_worker_api/client_worker_base_api.cpp`
- `src/datasystem/client/object_cache/client_worker_api/client_worker_remote_api.cpp`
- `src/datasystem/worker/object_cache/service/worker_oc_service_create_impl.cpp`
- `src/datasystem/worker/object_cache/service/worker_oc_service_publish_impl.cpp`

Current silent-failure risk:

- `Publish` and `MultiPublish` can attach TCP payload when UB memory copy did
  not complete. That makes a UB write failure observable in logs/metrics, but
  can still let future writes keep targeting the same destination.
- `UrmaSuccessRateTracker` is client-side and currently drives local client
  worker failover behavior; it does not create a worker-wide quarantine that
  stops other clients/workers from selecting the same destination.

Required quarantine hooks:

- Before `Create` and `MultiCreate` return writable URMA memory info, reject if
  the local worker is data-plane-write quarantined. This prevents handing out a
  write address for a destination that should not accept data.
- Before client `SendBufferViaUb` / `Publish` fallback payload, check
  destination write eligibility. If the destination is quarantined, return
  `K_URMA_WORKER_UNAVAILABLE` or equivalent without TCP write fallback by
  default.
- On UB write failure from client to worker, record failure against the
  destination worker address and reset cached UB transport state.
- ShmOnly/local-SHM writes must return UB data-plane abnormal status when the
  worker is UB-quarantined. A successful local SHM write would create data that
  other clients/workers cannot read, which is exactly the silent failure this
  feature is intended to prevent.

## 4. Existing Flow: Client -> Worker Read

Main path:

1. `ObjectClientImpl::Get` selects worker API and enters
   `GetBuffersFromWorker`.
2. If URMA is enabled and SHM is not used, client obtains object meta through
   worker API and allocates a UB receive buffer.
3. Client sends `Get` with UB receive info.
4. Worker `GetRequest` tries `UrmaWritePayload` to write object data into the
   client-provided buffer.
5. If UB write fails, `GetRequest::AddObjectToResponse` currently prepares TCP
   payload fallback through `ShmGuard::TrackUrmaFallbackTcp`.

Important files:

- `src/datasystem/client/object_cache/object_client_impl.cpp`
- `src/datasystem/client/object_cache/client_worker_api/client_worker_remote_api.cpp`
- `src/datasystem/worker/object_cache/worker_request_manager.cpp`

Current silent-failure risk:

- Worker-to-client UB failure can fallback to TCP payload and keep reads
  apparently successful while the client destination data plane is unhealthy.
- For user-facing reads, failure is acceptable when the required source or
  destination UB path is isolated; success through TCP should be a deliberate
  fallback policy, not the default signal.

Required quarantine hooks:

- Record worker-to-client UB write failure with operation
  `worker_to_client_get`. The failing destination is the client endpoint or
  client transport instance; this should at least affect the current client and
  metrics. It should not automatically quarantine a worker as write target.
- When reading object data from a worker that is already source-quarantined and
  no healthy replica/local copy is available, fail fast before allocating UB
  buffers.
- Gate worker-to-client TCP payload fallback behind an explicit read fallback
  policy. Even if read fallback remains enabled, it must still record UB health
  failure so the degradation is not silent.

## 5. Existing Flow: Worker -> Worker Remote Get

Main path:

1. `WorkerOcServiceGetImpl::GetObjectFromRemoteOnLock` or batch get groups
   remote locations.
2. `PullObjectDataFromRemoteWorker` builds `GetObjectRemoteReqPb`, allocates
   local receive memory, and fills URMA info with `PrepareGetRequestHelper`.
3. Source worker `WorkerWorkerOCServiceImpl::GetObjectRemote` calls
   `CheckConnectionStable`, loads local object, and writes data into the
   requester using `UrmaWritePayload`.
4. If UB write fails and `FLAGS_enable_transport_fallback` allows it,
   `HandlePayloadFallback` returns TCP payload.
5. Requester handles response payload or already-transferred data.

Important files:

- `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp`
- `src/datasystem/worker/object_cache/service/worker_oc_service_batch_get_impl.cpp`
- `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp`
- `src/datasystem/worker/object_cache/worker_worker_transport_service_impl.cpp`

Current silent-failure risk:

- `CheckConnectionStable` logs `K_URMA_NEED_CONNECT` and performs reconnect
  behavior, but it does not quarantine either side.
- `WriteViaFastTransport` records `fastTransportStatus`; `HandlePayloadFallback`
  can still transfer by TCP when fallback is enabled.
- Batch remote get has the same behavior across many keys, so one unhealthy
  destination can generate repeated slow degradation.

Required quarantine hooks:

- Before constructing `GetObjectRemoteReqPb`, filter source candidates through
  health state. If a source is quarantined and another location exists, select
  the healthy one. If no source remains, return read failure directly.
- In `CheckConnectionStable`, repeated `K_URMA_NEED_CONNECT` or failed exchange
  should record UB failure for the requester/source pair.
- In `WriteViaFastTransport`, UB write failure should record failure against
  the requester's worker address as write destination.
- If requester destination is quarantined, source worker should fail
  `GetObjectRemote` with data-plane quarantine status instead of producing TCP
  payload by default.

## 6. Existing Flow: Migration

Main path:

1. `DataMigrator::Migrate`, `MigrateToSpecificNode`, or
   `MigrateToTargetNode` chooses a target worker.
2. `ConnectAndCreateRemoteApi` checks cluster connection and creates a remote
   worker API.
3. `MigrateDataHandler` chooses transport:
   - `FastMigrateTransport`: source sends `MigrateDataDirectReqPb` containing
     URMA info for source memory; target performs remote read.
   - `FastMigrateTransport2`: source asks target to `NotifyRemoteGet`; target
     pulls data from source.
   - `TcpMigrateTransport`: source sends TCP payload.
4. Target `WorkerOcServiceMigrateImpl::MigrateDataDirect` prechecks memory,
   exiting state, and URMA enabled, then `FillDataToObjectEntries` starts remote
   reads.
5. `ProcessRemoteReadForObject` calls `UrmaRead` from source memory. Failed
   reads mark keys failed and can trigger redirect/retry.
6. `RedirectMigrateData` calls strategy `UpdateForRedirect` and selects a next
   target.

Important files:

- `src/datasystem/worker/object_cache/data_migrator/data_migrator.cpp`
- `src/datasystem/worker/object_cache/data_migrator/handler/migrate_data_handler.cpp`
- `src/datasystem/worker/object_cache/data_migrator/transport/fast_migrate_transport.cpp`
- `src/datasystem/worker/object_cache/data_migrator/transport/fast_migrate_transport2.cpp`
- `src/datasystem/worker/object_cache/data_migrator/transport/tcp_migrate_transport.cpp`
- `src/datasystem/worker/object_cache/service/worker_oc_service_migrate_impl.cpp`

Current silent-failure risk:

- Target admission only checks membership connection and resource availability.
  A worker with healthy TCP/RPC but broken UB can still be selected.
- Fast migration failures are treated as per-object migration failures and can
  retry or redirect without making the target/source data-plane state explicit.
- TCP migration can hide UB failure if chosen as fallback rather than explicit
  policy.

Required quarantine hooks:

- `ConnectAndCreateRemoteApi` should reject a quarantined target before opening
  a migration RPC.
- `NodeSelector::SelectNode`, scale-down selector, and spill selector should
  filter data-plane-quarantined targets in addition to resource and exclusion
  checks.
- `MigrateDataDirect` precheck should fail if the local worker is not an
  allowed migration destination.
- `UrmaRead` failure in target-side direct migration should record failure
  against the remote source for pull/read health and against the target if the
  failure indicates local UB inability.
- `RedirectMigrateData` should add the failed quarantined target to the strategy
  exclusion set, so retry does not bounce back to the same destination.

## 7. Existing Flow: Rebalance and Hash Ring Migration

Rebalance path:

1. Master scheduler builds source/target pairs from resource reports.
2. Worker `RebalanceExecutor::ValidateTask` validates task format and rejects
   self-target tasks.
3. `ExecuteBatch` chooses local object candidates.
4. `MigrateToTarget` calls `DataMigrator::MigrateToTargetNode`.

Hash-ring path:

1. `HashRingTaskExecutor` handles scale-up, scale-down, and recovery tasks.
2. Migration events eventually call data migrator flows.
3. Retry logic handles membership and transient failure codes.

Important files:

- `src/datasystem/master/memory_rebalance_scheduler.cpp`
- `src/datasystem/worker/rebalance_executor.cpp`
- `src/datasystem/worker/hash_ring/hash_ring_task_executor.cpp`
- `src/datasystem/worker/object_cache/data_migrator/strategy/node_selector.cpp`
- `src/datasystem/worker/object_cache/data_migrator/strategy/scale_down_node_selector.cpp`
- `src/datasystem/worker/object_cache/data_migrator/strategy/spill_node_selector.cpp`

Current silent-failure risk:

- Master/resource selection currently knows memory readiness, not UB
  writeability.
- Worker rebalance target validation does not check data-plane health.
- Hash-ring retry can keep re-attempting migration if data-plane quarantine is
  not represented as a clear retry/skip condition.

Required quarantine hooks:

- Worker-side `RebalanceExecutor::ValidateTask` or `MigrateToTarget` should
  reject a quarantined target immediately.
- `NodeSelector::ReportResource` should publish a separate data-plane
  write-ready signal, or the master scheduler should consume a new health view.
  Do not overload worker membership `READY`.
- Hash-ring migration should treat data-plane quarantine as "target currently
  not writable" and reselect or delay, not spin on the same target.

## 8. Transport Layer and Recovery

Client transport path:

- `DataPlaneManager::GetOrCreate` caches per-worker transporter entries.
- `TransportAdvisor` returns `UB_CANDIDATE` whenever URMA is enabled.
- `TransportLayer::Get` retries once on `K_URMA_NEED_CONNECT` after
  `ResetDataPlane`, and rebuilds after `K_RPC_UNAVAILABLE`.

Worker warmup path:

- `WorkerOCServiceImpl::PrepareUrmaWarmupObject` prepares a synthetic object.
- `WorkerOCServiceImpl::WarmupUrmaConnectionToPeer` performs a warmup get
  across peers.
- `worker_oc_server.cpp` already calls warmup during startup/peer handling.

Required quarantine hooks:

- `TransportAdvisor` should consult data-plane health before returning
  `UB_CANDIDATE`.
- `DataPlaneManager::ResetDataPlane` should be called on quarantine entry for
  the affected destination.
- Recovery probing should reuse the warmup object path when possible:
  handshake/exchange, tiny UB write/read, then `N` consecutive successes before
  leaving quarantine.
- Normal traffic should stay blocked while state is `PROBING`.

## 9. Use Case Matrix

| Case | Current behavior | Required behavior |
|------|------------------|-------------------|
| Client writes to worker with broken UB | May retry, fallback payload, or fail per request | Record destination failure; quarantine worker for writes; block future writes by default |
| Client reads from worker and worker cannot UB-write to client | Can fallback to TCP payload | Record failure; read fallback only if explicitly enabled; otherwise fail read |
| Worker A remote-gets from worker B, B cannot UB-write to A | Can TCP-fallback when enabled | Record failure against A as destination and/or B source; future reads skip quarantined source or fail fast |
| Migration target has broken UB | Target can still be selected by resource/member checks | Filter target before RPC; mark migration failed with explicit data-plane status |
| Direct migration target cannot `UrmaRead` from source | Per-object failures and retry/redirect | Record source/target health signal; redirect away from quarantined worker |
| Rebalance selects quarantined target | Worker eventually fails migration | Master/worker both avoid target; task fails fast with clear status if stale |
| etcd/TCP disconnected | Existing membership isolation handles it | Keep separate from UB health; do not infer data-plane state from etcd alone |
| UB recovers | Traffic resumes only after normal operation succeeds | Probe first, clear cached transports, then resume writes gradually |

## 10. Status and Observability

Recommended explicit statuses:

```text
K_URMA_WORKER_UNAVAILABLE
K_URMA_DATA_WORKER_UNAVAILABLE
```

`K_URMA_WORKER_UNAVAILABLE` is for write/migration target admission failure.
`K_URMA_DATA_WORKER_UNAVAILABLE` is for Get/read fail-fast when object data is
only on a worker whose UB data plane is quarantined. If adding two status codes
is too invasive for the first patch, add `K_URMA_DATA_WORKER_UNAVAILABLE` first
for the user-visible Get failure and use existing URMA codes with
`[UB_QUARANTINE]` logs for write/migration.

Required dimensions:

- source worker/client
- destination worker/client
- operation kind: `client_put`, `client_get`, `remote_get`, `batch_remote_get`,
  `migrate_direct`, `migrate_notify_remote_get`, `rebalance`
- transport: `UB`, `TCP_FALLBACK`, `SHM`
- state: `AVAILABLE`, `UNAVAILABLE`, `PROBING`
- last status code and quarantine epoch

## 11. Implementation Shape

Recommended first cut:

1. Add `UbFailureClassifier` to classify existing URMA status/CQE/provider
   status. `ERROR 4` / port unavailable is treated as a recoverable but
   quarantine-worthy data-plane event.
2. Add `WorkerUbPathHealth` with per-destination-worker state, failure reason,
   cooldown, unavailable epoch, and probe accounting.
3. Add `UbRecoveryProbe` using existing URMA handshake/warmup/small transfer
   paths; normal business writes remain blocked while probing.
4. Wire health checks into client-side write/read transport admission and worker-side
   object-cache services.
5. Record UB failures at `SendBufferViaUb`, `GetRequest::UbWriteHelper`,
   `WorkerWorkerOCServiceImpl::WriteViaFastTransport`, `CheckConnectionStable`,
   and migration `UrmaRead`.
6. Filter migration/rebalance targets in `ConnectAndCreateRemoteApi`,
   `NodeSelector`, and `RebalanceExecutor`.
7. Add metrics/logs before enabling cluster-wide policy.

Open policy decisions before coding:

- Whether worker data-plane health is published to master/resource report in
  the first patch or starts as worker-local plus client-local state.
- Whether to add both URMA-specific status codes in the first patch.
- Whether the 1 MiB TCP fallback cap reuses an existing size knob or gets a
  dedicated fallback cap flag.
