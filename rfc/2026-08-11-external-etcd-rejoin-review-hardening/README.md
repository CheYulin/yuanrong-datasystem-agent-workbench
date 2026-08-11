# External ETCD Cold-Rejoin Review Hardening

## Scope

This RFC records the post-review hardening for DataSystem PR 1981. It preserves
the original cold-rejoin contract: a Worker that lost its authoritative ETCD
membership stays `RECOVERING + ROLE_ISOLATED`, clears stale local state, then
publishes `READY`; existing topology scale-out converges it to `ACTIVE`.

This change does not redesign the synchronous O(N) cleanup selected by the
original RFC. It fixes three correctness gaps found in review and three SC-only
recovery gaps exposed in sequence by the new ST:

1. successful OC cleanup left incoming migration admission permanently closed;
2. SC-only Workers had no valid membership recreation gate;
3. reconciliation could call `MarkExiting()` while holding `reconFlag_`, which
   inverted the cold-rejoin transition lock order.
4. SC-only recovery reopened topology admission without restoring the process
   health bit, so `MasterSCService` rejected scale-out metadata migration.
5. `MasterMasterSCApi` always created a ZMQ stub even when Workers ran BRPC-only,
   so an SC metadata migration could not reach the recovered Worker.
6. SC-only cleanup removed only the local Worker's producer and consumer
   entries, leaving the old stream master tables and in-memory dictionaries to
   conflict with the authoritative migration payload.

## Design

### Incoming migration admission

`WorkerOcServiceMigrateImpl` gains a narrow reopen operation. It takes the
existing admission mutex, verifies that no admitted migration remains, checks
exit intent in that same critical section, clears the close and drain-timeout
flags, and returns. `WorkerOCServiceImpl` invokes it only after metadata and
object cleanup have both succeeded.

The endpoint policy remains isolated until topology recovery publishes READY,
so reopening this internal gate does not admit migration early. Failed cleanup
and graceful exit retain the closed gate.

### Worker-level OC/SC cleanup

The membership recreation callback becomes a Worker-level cleanup operation:

- enabled OC runs its existing ordinary-RPC drain, metadata cleanup, object
  cleanup, and migration-gate reopen;
- SC-only runs the same authoritative metadata cleanup through
  `MetadataManagerHolder`, then clears local stream producer, consumer, page,
  remote-stream, and client-index state using the existing reset/force-close
  primitives;
- mixed OC+SC does not repeat metadata cleanup: OC owns the shared metadata
  callback and SC performs only its local runtime cleanup.

Every enabled service must finish before the callback returns success. A null
service for an enabled mode remains `K_NOT_READY`; disabled services are skipped.

For SC-only Workers, a serving topology transition republishes process health
before reopening topology admission. Publication failure keeps both health and
admission closed, so metadata migration cannot observe a half-ready target.

SC master-to-master migration now uses the shared RPC stub cache and selects the
same BRPC or ZMQ transport as the Worker process. The BRPC path therefore also
inherits the existing stale-connection eviction behavior.

The SC metadata cleanup contract is a full local-master reset. It stops the
asynchronous reconciliation and notification workers, clears the in-memory
stream and migration maps, drops and recreates all seven SC metadata tables,
then reinitializes the manager. Any reset or reinitialization error is returned
through the existing membership recreation gate, so READY remains blocked.

### Lock order

`GetReadyToWork()` computes whether reconciliation completed and whether exit
publication is required, but does not call `MarkExiting()`. `Reconciliation()`
releases `reconciliationMutex_` and `reconFlag_` before publishing EXITING.
Normal reconciliation still publishes readiness under the existing serialized
path.

## TDD Matrix

| Gap | RED evidence | GREEN requirement |
| --- | --- | --- |
| Migration gate | successful rejoin cleanup followed by admission acquire returns `K_NOT_READY` | acquire succeeds; failed cleanup remains closed |
| SC-only gate | two-worker external-ETCD SC-only Worker cannot return to ACTIVE after OS suspend/removal | Worker returns ACTIVE; old producer is fenced; a fresh producer/consumer exchange data |
| SC-only health | recovered Worker reaches JOINING but `MasterSCService` returns `RPC_SERVICE_UNAVAILABLE` | health is republished before admission opens and SC metadata migration completes |
| SC migration transport | BRPC-only Workers never receive `MasterSCService.MigrateSCMetadata` | migration uses a cached BRPC stub when `use_brpc=true` |
| SC stale metadata | migration reaches the target but its payload item is rejected | all local SC metadata stores are reset before authoritative migration; fresh publish/subscribe succeeds |
| Lock order | at `recover.toexiting.delay`, a probe cannot acquire `reconFlag_` | probe acquires the lock before `MarkExiting()` continues |
| OS suspend injection | RPC may race `SIGSTOP` delivery and succeed | wait for `WIFSTOPPED` before issuing the direct 500 ms worker RPC |

## Validation Boundary

- Build only on `xqyun-32c32g`, with CMake, `URMA_MOCK`, `-j32`, and
  `/home/cache/ds-thirdparty-cache`.
- Run the new tests first, then historical PR 1981 focused UT/ST.
- Preserve existing formatting outside touched lines; no clang-tidy-driven
  refactor and no performance-state-machine change.
- Review comment 184243524 remains a separate architecture item: the cleanup is
  still synchronous and O(N), matching the original RFC decision. This patch
  does not claim a bounded Controller callback.

## Final Validation

The rebased exact source was validated with CMake, the shared third-party
cache, URMA Mock, and 32-way build parallelism:

| Group | Result | Runtime |
| --- | --- | --- |
| focused cluster UT | 3/3 passed | 41 ms |
| focused object-cache UT | 8/8 passed | 45 ms |
| worker health UT | 1/1 passed | less than 1 ms |
| stream metadata reset UT | 1/1 passed | 185 ms |
| SC-only OS suspend cold-rejoin ST | 1/1 passed | 27.238 s |
| external-ETCD KV ST | 2/2 passed | 26.549 s and 16.509 s |

The first KV aggregate attempt started the binary from the source root, so the
historical OBS-backed case could not locate its test fixture and failed in
8 ms before cluster startup. Running the same binary from its CMake test
directory fixed the harness path; both KV cases then passed. This was a
validation-command error, not a product-code failure.
