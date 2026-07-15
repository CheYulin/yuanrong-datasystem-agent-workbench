# UB Data Plane Quarantine — Initial Design

**Status**: Draft  
**Date**: 2026-07-12  
**Primary goal**: avoid silent data-plane degradation when UB failures reduce
write success rate.

---

## 1. Problem

The system has both UB and TCP paths. UB is mainly used for one-sided data
movement. When UB multi-port or connection state is unhealthy, data operations
can degrade without a clean node-level failure:

- Local node UB failure can make local data pull/write support fail.
- Remote node UB failure can cause other workers or clients to keep writing to
  a destination that is no longer a reliable data sink.
- Migration and rebalance can keep selecting an unhealthy target and repeatedly
  fail.
- Existing TCP/etcd isolation handles membership and control-plane disconnects,
  but data access still needs a data-plane isolation policy.

The core failure to prevent is: UB is unhealthy, success rate drops, but the
system continues to send writes or migration traffic to the same destination.

## 2. Design Principles

| Principle | Meaning |
|-----------|---------|
| No silent fallback by default | UB isolation should not be hidden by TCP fallback unless explicitly enabled. |
| Destination write quarantine | UB data-plane failure isolates the destination worker from new writes by default. |
| Separate membership from data plane | A worker can remain `READY` in etcd while being unavailable as a write destination. |
| Fast fail before timeout | Quarantined destinations should fail or reselect quickly instead of waiting for UB/TCP timeouts. |
| Recovery by probing | Normal writes resume only after explicit recovery probes succeed. |
| Read failure is acceptable | For reads from an isolated destination, return failure directly unless another healthy replica is selected by existing logic. |

## 3. Scope

In scope:

- Client-to-worker data writes that can use UB.
- Worker-to-worker data movement, remote get, and UB handshake paths.
- Data migration and rebalance target selection.
- Failure recording, quarantine state, recovery probing, metrics, and logs.

Out of scope for the first implementation:

- Replacing worker membership or etcd lifecycle state.
- Making TCP fallback the default behavior.
- Fine-grained UB port/device quarantine. The first design isolates at
  destination-worker write level.

## 4. Default Semantics

Quarantine means **data-plane eligibility isolation**, not worker membership
removal. A quarantined worker can still be alive, registered, and reachable by
TCP/RPC; it is just not eligible as a UB-backed write destination, migration
target, or sole read source.

When UB failure crosses the isolation threshold for worker `W`:

1. `W` becomes UB data-plane quarantined.
2. New data writes to `W` are rejected or reselected before issuing UB/TCP data
   transfer.
3. TCP write fallback is not used by default.
4. Data migration and rebalance do not choose `W` as target.
5. Reads whose data only exists on `W` fail fast with an explicit UB data worker
   unavailable status. Reads that can select another healthy replica may
   continue through existing replica selection.
6. `W` becomes writable again only after recovery probing succeeds.

ShmOnly/local-shared-memory writes:

- If a worker is UB-quarantined, it must not silently accept a local SHM-only
  write that other clients/workers cannot later read through UB.
- The worker should return a UB data-plane abnormal status to the client before
  committing the write as successful.
- Local SHM reads of already-written data may continue for the same colocated
  client when no UB data movement is needed, but they must not imply the object
  is globally readable.

Optional policy:

- A future flag may allow explicit TCP fallback for specific operations, but it
  must be opt-in and observable.
- Even when fallback is enabled, only payloads up to 1 MiB may fallback to TCP.
  Larger payloads fail fast and keep the UB failure signal visible.
- Fallback success does not clear quarantine or suppress UB failure accounting.

Relationship with TCP/etcd self-healing:

- TCP/etcd self-healing owns worker local service state, membership
  revalidation, metadata cleanup, primary/local-copy/L2 ownership
  reconciliation, and the decision that a worker can return to
  `WorkerServiceMode=RUNNING`.
- UB data-plane quarantine owns only UB path eligibility. It decides whether
  the current client/worker should keep using UB to reach destination worker
  `W`.
- `WorkerServiceMode != RUNNING` is a stronger service-level block. Such a
  worker must not be selected for normal writes, migration, rebalance target, or
  normal data provider service regardless of UB path state.
- `WorkerUbPathHealth != AVAILABLE` is a data-plane block. It can happen while
  TCP/RPC and membership are healthy; in that case the worker may stay in
  cluster membership, but UB-backed writes/migration and sole-source reads must
  fail fast or reselect.
- Recovery is independent and conjunctive: TCP reconnect and metadata
  reconciliation do not clear UB quarantine; UB probe success does not make a
  `RECOVERING` worker `RUNNING`.

Combined admission:

```text
CanWriteOrMigrateTo(worker)
  = WorkerServiceMode(worker) == RUNNING
    && WorkerUbPathHealth(worker) == AVAILABLE
```

## 5. State Machine

```text
AVAILABLE -> UNAVAILABLE -> PROBING -> AVAILABLE
```

| State | Behavior |
|-------|----------|
| `AVAILABLE` | Destination worker UB path is eligible for writes, reads, and migration targets. |
| `UNAVAILABLE` | Destination worker UB path is not usable; writes/migration are blocked and reads that require it fail fast. |
| `PROBING` | Background recovery probes run; normal writes are still blocked. |

Transition guidance:

- `AVAILABLE -> UNAVAILABLE`: `ERROR 4` is the clear URMA port-unavailable
  signal and should mark the destination worker unavailable immediately. Other
  failures, such as failed reconnect, repeated timeout, or failed handshake,
  can still use threshold/confirmation policy.
- `UNAVAILABLE -> PROBING`: cooldown expires.
- `PROBING -> AVAILABLE`: consecutive recovery probes succeed.
- `PROBING -> UNAVAILABLE`: probe fails, with exponential backoff.

## 6. Failure Signals

Only UB data-plane failures should feed this isolation decision. TCP/RPC
membership errors should remain in the existing membership path.

Candidate UB signals:

- `K_URMA_ERROR`, especially provider/CQE `ERROR 4` that explicitly means the
  UB port is unavailable
- `K_URMA_WAIT_TIMEOUT`
- `K_URMA_CONNECT_FAILED`
- UB handshake/finalize failures
- UB completion/poll/write/read failures
- repeated `K_URMA_NEED_CONNECT` that fails to recover

Do not count ordinary TCP/RPC timeout or etcd disconnect as UB health failures.

Recommended status code model:

| Status | When to use |
|--------|-------------|
| existing `K_URMA_ERROR` / `K_URMA_WAIT_TIMEOUT` / `K_URMA_CONNECT_FAILED` | Actual UB operation failure signal, used for health accounting. |
| new `K_URMA_WORKER_UNAVAILABLE` | A write or migration target is blocked because its UB path is unavailable. |
| new `K_URMA_DATA_WORKER_UNAVAILABLE` | A Get/read fails fast because the object data is only on a worker whose UB data plane is quarantined. |

If only one new code is acceptable in the first patch, prefer
`K_URMA_DATA_WORKER_UNAVAILABLE` for the user-visible Get failure, and use
existing URMA failure codes plus log prefix `[UB_QUARANTINE]` for blocked
writes/migration.

## 7. Components

### 7.1 Minimal Module Set

To keep the change small, the first patch should add only three new logical
modules and reuse existing URMA calls, warmup, resource reporting, and RPC
paths.

| Module | Purpose | Scope |
|--------|---------|-------|
| `UbFailureClassifier` | Convert URMA operation status, CQE status, and handshake failures into retryable/non-retryable health events. | Common utility, no background thread. |
| `WorkerUbPathHealth` | Hold simple per-destination-worker UB path state, admission decisions, cooldown, and metrics. | One instance per client/worker process; worker instance can also publish local self UB health. |
| `UbRecoveryProbe` | Periodically probe quarantined workers after cooldown using existing handshake/warmup/small transfer paths. | Worker process first; client may probe lazily or rely on worker-published health. |

No new business RPC is required for the first cut. Existing write/get/migration
RPCs return explicit URMA status codes, and existing resource/cluster reporting
can be extended later if cluster-wide propagation is required.

Module ownership is split by three data-flow roles. This is the important
abstraction because some paths directly execute URMA locally, while other paths
only send an RPC that asks the peer to execute URMA.

| Role | Who owns the decision | What it does |
|------|------------------------|--------------|
| Coordinator | The client or worker that starts a business action or sends an RPC that may cause URMA somewhere else. | Checks `WorkerUbPathHealth` before selecting source/target, sends RPC, consumes returned URMA status, and fast-fails or reselects. |
| URMA Operator | The process/thread that actually calls `UrmaWritePayload` or `UrmaRead`. | Knows the concrete URMA failure first, classifies it, updates local health, and returns the error to the coordinator/endpoint. |
| Endpoint | The process whose memory or worker data is being written/read, or the worker that would receive new writes/migration. | Learns remote URMA failures through RPC/status/health publication, then gates new writes/migration until recovery. |
| Recovery probe owner | Worker first, client lazily if needed. | Tests an `UNAVAILABLE` worker path after cooldown and marks it `AVAILABLE` only after probe success. |

The key signal flow is **sender reports, peer gates**:

1. The side that initiates the URMA one-sided operation is the first side that
   knows the exact failure reason. It can classify ERROR 4, timeout, reconnect
   failure, or handshake failure and return quickly.
2. The peer that is being written to or read from cannot infer the same detail
   by itself. It learns through the current RPC response/status, fallback
   tracking, or later worker health publication.
3. After learning that its UB path is not recovered, that peer must avoid
   accepting new writes and migration target traffic.
4. Reads may still be attempted, but they must use the health view to fail fast
   when the object data is only on an unavailable worker.

Important flow ownership:

- Client-worker write: client is both coordinator and URMA operator for the
  client-to-worker UB write.
- Worker-client Get: client is coordinator, but worker is the URMA operator
  because the worker writes into client memory.
- Worker-worker remote get: requester worker is coordinator, source worker is
  URMA operator/provider because it writes into requester memory. There is only
  provider one-sided write on this data path.
- Direct migration: source/driver worker is coordinator for selecting target;
  target worker is URMA operator when it issues `UrmaRead` from source memory.
- Rebalance and migration exist only in worker-worker flows. Rebalance executor
  and data migrator are coordinators for target selection and retry.

### 7.2 URMA/RDMA Layer Boundary

The `common/rdma` layer should remain responsible for URMA resource lifecycle
and operation completion, not object-cache policy. This boundary matters more
after PR1277, because send Jetty resources are pooled and a single business
transfer may borrow a send lane rather than own a per-connection send Jetty.

Key concepts from `rdma/urma_resource` and `rdma/urma_manager`:

| Concept | Meaning for this design |
|---------|-------------------------|
| `UrmaResource` | Owns process-level URMA resources: context, JFC/JFCE, Jetty creation/registry, async Jetty cleanup, and after PR1277 the send Jetty pool. It should recover/retire local resources, but should not decide whether an object worker is writable. |
| `UrmaConnection` | Represents a remote peer connection and imported remote target Jetty/segments. After PR1277, the remote target Jetty remains connection-scoped, while local send Jettys can be borrowed from a process-level pool. |
| `SendJettyPool` | Holds reusable local send Jettys. Pool exhaustion is local resource pressure and should return fast, but it should not by itself mark a remote worker UB-unavailable. |
| `UrmaSendLaneLease` | Request-level lease for one borrowed send Jetty. All chunks/events of one transfer share the lease; the lane is released only after all events settle, or retired if any event/request asks for retirement. |
| `UrmaEvent` | Carries request id, operation type, remote address/instance, data size, completion status, and the lane lease. This is the natural place to expose structured failure metadata upward. |
| `WaitToFinish` / completion polling | Converts timeout and failed completion into `Status`, releases or retires the lane, and can trigger Jetty recreation policy. This should emit enough metadata for health classification. |

Recommended layering:

```text
Object/cache business flows
  -> Coordinator adapters: write admission, source/target filtering, fallback policy
  -> URMA Operator wrappers: call UrmaWritePayload/UrmaRead and report structured outcome
  -> common/rdma: connection, target Jetty, send lane pool/lease, events, completions
```

Minimal first-cut integration should avoid pushing quarantine policy into
`UrmaResource` or `UrmaManager`. Instead, the code that already calls
`UrmaWritePayload` / `UrmaRead` should report the returned status into
`UbFailureClassifier` and `WorkerUbPathHealth`. PR1277 makes a cleaner future
hook possible: `UrmaManager` can expose an optional operation outcome structure
without knowing the object-cache policy.

Suggested structured outcome:

```cpp
struct UbOpOutcome {
    HostPort peer;
    OperationKind operation; // READ or WRITE
    Status status;
    std::optional<int> cqeStatus;
    std::optional<uint32_t> localJettyId;
    uint64_t dataSize;
    bool laneRetired;
    FailureReason reason; // connect, timeout, cqe_error, post_failed, pool_exhausted
};
```

Classification guidance with PR1277:

- CQE/provider status such as ERROR 4, failed handshake, failed reconnect, and
  repeated no-progress timeout should mark the peer path `UNAVAILABLE`.
- `K_TRY_AGAIN` from send Jetty pool exhaustion is local resource pressure.
  Return fast and count it separately; do not quarantine the remote worker only
  from this.
- Lane retirement/recreation is local resource recovery. It should still happen
  inside `common/rdma`, but normal business writes remain gated by
  `WorkerUbPathHealth` until recovery probe succeeds.
- When a path becomes `UNAVAILABLE`, callers should reset or bypass cached UB
  connection state where existing APIs allow, so the later probe does not reuse
  stale transport state.

### 7.3 UbFailureClassifier

The sender side already knows why UB failed. A completion/provider error whose
status is `ERROR 4` is treated as the explicit UB port-unavailable signal. That
error is serious enough to stop normal traffic immediately, but it is not
permanent: the port may recover and should be tested by probe.

Classifier output:

| Result | Example signals | Effect |
|--------|-----------------|--------|
| `SUCCESS` | UB operation completed | Count as probe success or clear local failure streak. |
| `CONNECT_OR_PATH_FAILURE` | `K_URMA_NEED_CONNECT` after reconnect fails, handshake/finalize failure, connect failed | Mark the destination worker UB path unavailable for this observer. |
| `PORT_OR_PATH_UNAVAILABLE` | CQE/provider status `ERROR 4`; or thresholded `K_URMA_WAIT_TIMEOUT` / repeated no-progress write/read | Mark the destination worker UB path unavailable. `ERROR 4` is immediate; timeout/no-progress is thresholded. |
| `LOCAL_UB_UNAVAILABLE` | local URMA disabled unexpectedly, local device/port error, warmup to multiple peers fails | Mark local worker UB unavailable; reject ShmOnly writes and publish local self-health if available. |
| `NON_UB_FAILURE` | TCP/RPC unavailable, etcd disconnect, auth, object not found, no space | Do not feed UB quarantine. |

Minimum threshold recommendation:

- `ERROR 4` / port unavailable: mark the destination worker UB path unavailable
  immediately. Continuing to send real writes will only create visible
  success-rate loss.
- `K_URMA_NEED_CONNECT`: allow one reset/reconnect attempt first; quarantine
  only if reconnect fails or repeats for the same destination worker.
- `K_URMA_WAIT_TIMEOUT`: quarantine after consecutive timeout or low success
  rate. A single timeout can be kept as a warning if there are enough recent
  successes.
- `K_TRY_AGAIN` caused by send Jetty pool exhaustion: treat as local resource
  pressure, not remote UB failure. It may require retry/backpressure, but should
  not make a worker unavailable for all peers.

### 7.4 WorkerUbPathHealth

New lightweight component, conceptually owned by worker/object-cache common
code. The goal is not to model all provider/receiver directions. The first
version only answers one practical question:

```text
Should this process continue using UB to reach destination worker W?
```

Key:

```text
destination worker address
```

Tracked fields:

- state
- last failure code and message
- last URMA provider/CQE status when available
- consecutive failure count
- first and last failure time
- quarantine deadline
- next probe time
- recovery success count
- quarantine epoch
- failure reason: remote port unavailable, local port unavailable, path/connect
  failure, timeout
- reporter role: one-sided operation initiator, data provider, migration target,
  or recovery probe
- learned-from: local URMA completion, RPC response, fallback tracking, or
  worker health publication
- transport metadata when available: operation type, request id, CQE/provider
  status, local Jetty id, lane-retired flag, data size

State:

```text
AVAILABLE -> UNAVAILABLE -> PROBING -> AVAILABLE
```

There is no separate intermediate state in the first implementation. If a
signal is strong enough to avoid silent failure, mark the worker UB path
`UNAVAILABLE`. Weak signals can be counted as warnings without changing
admission.

Main APIs:

```cpp
Status CheckUbReachable(const HostPort &worker) const;
bool IsUbReachable(const HostPort &worker) const;
void MarkUbFailure(const HostPort &worker, OperationKind op, const Status &rc, UbFailureReason reason);
void MarkProbeSuccess(const HostPort &worker);
void MarkProbeFailure(const HostPort &worker, const Status &rc, UbFailureReason reason);
std::vector<HostPort> FilterUbReachableWorkers(std::vector<HostPort> candidates) const;
```

How to interpret the key:

- Client/worker writing data to worker `W`: key is `W`.
- Migration/rebalance selecting target `W`: key is `W`.
- Get/read whose only data location is worker `W`: key is `W`; fail fast if
  `W` is unavailable.
- Worker serving data to requester `R` and UB write to `R` fails: key is `R`,
  because the observed failure is the path to the receiver.
- Worker-worker remote get source writing back to requester `R`: key is `R`.
  Remote get requester-side source selection still uses the source worker key
  when deciding whether the source can be read.

This intentionally covers both cases:

- the destination worker's UB port is actually down;
- the current client/worker cannot reach that destination worker through UB due
  to an intermediate path, connection, or local-port problem.

### 7.5 UbRecoveryProbe

The probe should be lightweight and not share normal business write semantics.

Possible probe sequence:

1. UB connection stability or handshake check.
2. Small warmup write/read against a synthetic object or existing warmup
   mechanism.
3. Mark healthy after `N` consecutive successes.

The probe must be rate-limited with exponential backoff.

Probe logic:

1. Enter `PROBING` only after `quarantine_until` expires.
2. Clear or bypass stale cached UB connection state for the target.
3. Run handshake/exchange against the target.
4. Run an existing warmup object path or a tiny synthetic UB transfer.
5. Require `N` consecutive successful probes before `AVAILABLE`.
6. Any probe failure returns the worker to `UNAVAILABLE` and increases backoff.

Normal business writes remain blocked while `PROBING`.

## 8. Detailed Flow Adaptation

### 8.1 Client -> Worker Write

Minimal hook sequence:

1. Client is the coordinator and URMA operator. Before choosing or using a
   worker for `Create`, `Put`, `Publish`, or
   `MultiPublish`, call `CheckUbReachable(workerAddr)`.
2. If blocked, return `K_URMA_WORKER_UNAVAILABLE` and do not call `Create`.
3. If allowed and UB is used, call existing `UrmaWritePayload`.
4. On sender-side UB failure, classify the error and call
   `MarkUbFailure(workerAddr, client_put, rc, reason)`.
5. If the state transitions to `UNAVAILABLE`, reset the cached UB transporter
   for that worker.
6. Do not attach TCP fallback payload by default. If fallback flag is enabled,
   allow only when payload size is `<= 1 MiB`; still record the UB failure.

Worker-side admission:

- `CreateImpl` / `MultiCreateImpl` must also check local UB receive/write
  eligibility before returning URMA remote address.
- For ShmOnly/local SHM writes, if the worker knows its UB data plane is
  quarantined, it returns URMA data-plane abnormal status before committing the
  object. This prevents local-only success that other workers/clients cannot
  read.

### 8.2 Worker -> Client Get

Minimal hook sequence:

1. Client is the coordinator and sends Get with client UB receive info.
2. Worker is the URMA operator/provider. Before `UrmaWritePayload`,
   optionally check `CheckUbReachable(client)` if the client identity/transport
   instance can be mapped and has local failure history.
3. On UB write failure, classify and record `worker_to_client_get` against the
   receiver path.
4. If fallback is disabled or payload is `> 1 MiB`, return the URMA failure.
5. If fallback is enabled and payload is `<= 1 MiB`, return TCP payload but keep
   the failure in metrics and health state.

This path should not automatically quarantine the data worker as a write target;
the failed receiver is the client. It does, however, prevent silent read
fallback and gives client-side receive failures a visible signal.

Feedback requirement:

- The worker provider should include the URMA failure status in the Get response
  or existing fallback tracking path.
- The client learns that its receive path failed and should avoid immediately
  advertising the same UB receive path again until it is recovered or retried by
  policy.
- If fallback is disabled or payload is larger than 1 MiB, the client sees a
  fast URMA failure instead of a silent TCP success.

### 8.3 Worker -> Worker Remote Get

Coordinator/requester side:

1. Requester worker is the coordinator. Before constructing `GetObjectRemoteReqPb`,
   filter candidate data locations
   with `IsUbReachable(sourceAddr)`.
2. If no healthy source remains, return `K_URMA_DATA_WORKER_UNAVAILABLE`.
3. If the requester itself is locally UB-unavailable, do not advertise UB
   receive info; return failure unless explicit small TCP fallback is allowed.

URMA operator/source side:

1. Source worker is the URMA operator/provider. `CheckConnectionStable`
   continues to do the existing reconnect path for
   `K_URMA_NEED_CONNECT`, but failed or repeated reconnect records UB failure.
2. Before `WriteViaFastTransport`, check whether requester is allowed as a UB
   receiver if the source has a local health record for it.
3. On `UrmaWritePayload` failure, classify provider status such as `ERROR 4`
   and record failure against the requester as receiver/destination.
4. Do not create TCP payload fallback by default; if enabled, enforce the
   `<= 1 MiB` cap.

Feedback requirement:

- Source worker, as the one-sided UB sender/provider, knows the concrete send
  failure first and returns it through `GetObjectRemoteRspPb` or stream status.
- Requester learns that source-to-requester UB failed. If its own receive path
  is not recovered, it should avoid issuing more remote get requests that
  advertise the same broken receive memory and should fail fast when no
  alternate source exists.

### 8.4 Migration and Rebalance

Coordinator/source side:

1. Worker is the coordinator for migration/rebalance target selection.
   `NodeSelector` filters target candidates through
   `FilterUbReachableWorkers`.
2. `DataMigrator::ConnectAndCreateRemoteApi` performs a second admission check
   before opening the remote API.
3. `RedirectMigrateData` adds a quarantined failed target to the strategy
   exclusion set before selecting the next node.

URMA operator/target side for direct migration:

1. `MigrateDataDirect` precheck rejects the request if local UB receive/read
   ability is quarantined.
2. `ProcessRemoteReadForObject` classifies `UrmaRead` failures and records both
   source-read and local-target signals where possible.
3. Failed objects return explicit status; primary is not replaced for failed
   objects.

Migration feedback requirement:

- In direct migration, the target worker initiates `UrmaRead`; therefore the
  target knows the concrete read failure first.
- The target returns failed object ids and URMA status to the source/driver
  worker.
- The source/driver uses that information to avoid retrying migration to the
  same target while the path remains `UNAVAILABLE`, and to fail fast or reselect
  another target.

Rebalance/hash-ring:

- `RebalanceExecutor::ValidateTask` or `MigrateToTarget` performs a stale-target
  admission check. A stale task can fail fast with
  `K_URMA_WORKER_UNAVAILABLE`.
- Master/resource selection may later consume worker-published UB data-plane
  readiness to avoid assigning bad targets, but worker-side validation is still
  required.

### 8.5 Health Propagation, Minimal First Cut

There are two levels:

1. **Local immediate protection**: the process that observes UB failure
   immediately stops sending writes/migrations to that target. This is the
   minimum change and prevents repeated silent failures from the same sender.
2. **Cluster-aware protection**: worker health is published through an existing
   resource/reporting path so other workers and clients can avoid the target
   before they hit it. This can be added after local protection, but migration
   and rebalance should use it as soon as available.

For minimum-risk implementation:

- Start with worker-local and client-local health managers.
- Add worker self-health publishing for `IsUbReachable(local)` so ShmOnly
  writes and migration target selection can fail before data is committed.
- Extend cluster/resource report only for worker data-plane readiness; do not
  modify membership state.

## 9. Integration Points Confirmed From CodeGraph

The latest `main/master` source was indexed at commit
`ddba645424a857bbbd14d256cb0b97d3c155ac4f`. Detailed flow notes are in
[`flow-analysis.md`](./flow-analysis.md).

| Area | Candidate files | Expected hook |
|------|-----------------|---------------|
| Client write API | `client/object_cache/client_worker_api/*`, `object_client_impl.cpp` | Before UB/TCP payload write, check destination write eligibility; after UB result, record health. |
| Worker-worker data transfer | `worker/object_cache/worker_worker_oc_service_impl.cpp`, `worker_worker_transport_api.*` | Record UB handshake/write/read failures and block writes to quarantined destinations. |
| Worker get/read path | `worker/object_cache/service/worker_oc_service_get_impl.cpp`, `worker_oc_service_batch_get_impl.cpp` | For reads whose selected source is quarantined, fail fast or select another healthy replica. |
| Publish/write path | `worker/object_cache/service/worker_oc_service_publish_impl.cpp`, `worker_oc_service_multi_publish_impl.cpp` | Ensure writes do not select a quarantined destination. |
| Migration | `worker/object_cache/data_migrator/*` | Filter target nodes and avoid retrying quarantined destinations. |
| Rebalance | `worker/rebalance_executor.*`, hash-ring task code | Avoid selecting quarantined workers as migration targets; fail/retry with explicit reason. |
| Cluster/membership | `worker/cluster_manager/*`, topology membership | Keep worker `READY`; expose separate data-plane write availability. |

## 10. Use Case Matrix

| Use case | Desired behavior |
|----------|------------------|
| Local worker UB failure, local write support | Fail fast; do not silently route data writes through TCP unless explicitly configured. |
| Local worker UB failure, local read support | If data requires failed UB path, return failure; local shared-memory-only reads may continue if safe. |
| Remote worker UB failure from client or worker | Quarantine remote destination for writes; new writes do not target it. |
| Remote worker UB failure during migration | Mark target unavailable for migration; choose another target or return retryable migration failure. |
| Quarantined worker recovers | Recovery probe transitions it back to writable; normal traffic resumes gradually. |
| etcd disconnect but UB healthy | Existing membership isolation remains responsible; do not infer UB health from etcd alone. |
| UB unhealthy but TCP/RPC healthy | Worker can remain membership-ready, but data writes are blocked by data-plane quarantine. |

## 11. Observability

Required logs and metrics:

- Quarantine enter/exit events with destination, reason, op kind, and counts.
- Fast-fail count for writes blocked by quarantine.
- Probe attempts, successes, and failures.
- Per-destination state gauge.
- UB failure code breakdown.
- Optional TCP fallback count if explicit fallback policy is enabled.

Log prefix proposal:

```text
[UB_QUARANTINE]
```

## 12. Open Questions

1. Should the first version expose quarantine status through an admin/diagnostic
   RPC, logs/metrics only, or both?
2. Should blocked writes/migration also get a dedicated
   `K_URMA_WORKER_UNAVAILABLE`, or should the first patch add only
   `K_URMA_DATA_WORKER_UNAVAILABLE` for reads?
3. Should recovery probing reuse the existing URMA warmup object path or add a
   dedicated health probe operation?

## 13. Next Step

Review the RFC and settle three policy choices before coding:

- whether worker data-plane health is published to master/resource report in
  the first patch or starts as worker-local plus client-local state;
- whether to add one or two URMA-specific status codes;
- how to expose the 1 MiB TCP fallback policy in flags and metrics.
