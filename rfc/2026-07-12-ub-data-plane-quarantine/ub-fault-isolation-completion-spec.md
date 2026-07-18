# UB Fault Isolation Completion Spec

Last updated: 2026-07-17

## Goal

Close every executable acceptance item in
`ds-worker-isolation-ub-tcp-boundary-20260716.html` on the latest `main/master`.
The completed behavior must prevent silent UB data-plane failure:

- a process that observes its own UB sender failure stops issuing UB writes until a probe proves recovery;
- reads that resolve to an unavailable data worker fail as one endpoint group, without per-object timeout amplification;
- a worker that cannot receive writes or migration data is not selected or admitted as a target;
- provider-side UB write failures are returned as explicit structured status, while an RPC timeout remains only suspect;
- membership/topology GlobalFact and process-local UB admission are both required for write and migration admission;
- recovery requires both a successful UB probe and a still-writable GlobalFact;
- each worker propagates only its own UB health summary, so cluster state remains O(N), never a peer matrix;
- all acceptance paths have focused UT plus URMA Mock ST evidence where the HTML requires UT + ST.

## State Ownership

The implementation keeps three state classes separate.

1. Client-local UB sender capability belongs to one `TransportLayer` instance. A client-side ERROR 4/9 must not mark
   the destination worker globally or quarantine that worker for other clients.
2. `PeerUbAdmission` is a process-local observation of a data source or migration/write target. It never changes
   cluster membership and must preserve operation direction when the same endpoint has different source/target health.
3. GlobalFact comes from membership/topology and is an upper bound. Local UB availability cannot override a worker
   that is not globally admissible.

The effective decision is always:

```text
GlobalFact allows role
AND local sender capability allows UB
AND peer source/target admission allows the endpoint
```

## Structured Failure Contract

Provider-side UB failures use a backward-compatible optional protobuf detail carrying:

- DataSystem status code;
- raw URMA/CQE status when available;
- failed UB destination endpoint;
- failure side (`provider_local_ub_write`);
- worker address that executed the failed write.

Only this explicit detail can hard-quarantine a provider after an RPC response. RPC timeout, disconnect, or an old
peer response without the detail remains suspect and cannot be synthesized into ERROR 4. A successful TCP fallback
may return business success but cannot clear UB health.

## Recovery Contract

Business requests never serve as recovery probes. A quarantined local sender or peer target remains blocked until:

1. cooldown/backoff permits a probe;
2. the role-specific GlobalFact pre-check permits probing;
3. the small UB probe succeeds for the configured threshold;
4. the GlobalFact post-check still permits the role;
5. only then the state becomes `AVAILABLE`.

If either GlobalFact check fails, the probe is not run or its success is not committed. Probe failure increases the
observable backoff deadline and leaves the state unavailable.

## Health Propagation Contract

Network propagation must use a self-only summary API. It contains exactly one worker identity/incarnation, state,
epoch, reason, and backoff deadline. The existing peer-state collection must never be serialized into heartbeat or
coordination state.

- Client-worker heartbeat carries the serving worker's self summary to connected clients.
- Worker-to-cluster propagation stores one lease/TTL-backed value per worker and applies epoch/incarnation fencing.
- Consumers update only their local candidate admission cache; ordinary Put/Get does not synchronously query etcd.
- A 1000-worker test must prove 1000 single-worker records, not 1000 peer vectors.

## Phased TDD Plan

| Phase | Behavior | Required RED evidence | Green acceptance |
| --- | --- | --- | --- |
| P18 | Rebase and latest-main baseline | Existing suite/build break after rebase if incompatible | CLion CMake cached build plus current focused UT/ST pass on `ce485a006` |
| P19 | Client-local sender isolation and Direct/Batch Direct Read grouping | Client ERROR4 currently reaches UB again and wrongly poisons worker; unavailable direct-read endpoint still executes transport | Same client fails fast before a second UB write; worker self admission stays healthy; one endpoint decision rejects all same-worker batch items with zero endpoint calls; healthy groups continue |
| P20 | Provider structured UB status for UC-2/UC-4 | Responses lack the five explicit fields; requester cannot distinguish provider ERROR4 from RPC timeout | Unary and batch provider responses encode/consume explicit detail; only explicit provider failure hard-quarantines; timeout remains suspect |
| P21 | GlobalFact AND local admission and guarded recovery | UB `AVAILABLE` currently permits paths that are INITIAL/JOINING/PRE_LEAVING/LEAVING/FAILED; successful probe can race a deny transition | Role matrix is enforced for write, incoming migration, outgoing migration, and redirect; GlobalFact deny prevents or cancels recovery commit |
| P22 | Self-only health propagation | Current summary contains observed peers and heartbeat has no UB field | Client heartbeat and one-per-worker lease state carry only self summary with epoch/incarnation fencing; 1000-worker test is O(N) |
| P23 | End-to-end URMA Mock acceptance | Current single ST covers timeout-to-recovery only | URMA Mock covers client write ERROR4 fail-fast/recovery, provider write ERROR4 explicit status, RPC timeout boundary, migration target rejection/recovery, and self-summary propagation |
| P24 | Final build, PR, and gate | Any failing CMake/Bazel/GitCode check | xqyun CLion CMake path passes, Bazel 7.4.1 passes with `/home/cache/bazel-ds`, branch is pushed, GitCode PR build is explicitly `success` |

## HTML Acceptance Matrix

| HTML case | Completion phase | Required end-to-end signal |
| --- | --- | --- |
| UC-1 Client writes Worker | P19, P23 | First local ERROR4 quarantines only that client sender; next UB write does not call the UB operator; probe restores it |
| UC-2 Client Get written back by Worker | P20, P23 | Client sees explicit provider endpoint/operator/URMA status; pure RPC timeout is distinguishable |
| UC-3 Client Direct Read / Batch Direct Read | P19 | Same unavailable data worker is checked once; all grouped objects fail fast; no N timeouts |
| UC-4 Worker RemoteGet | P20, P23 | Requester consumes provider detail and does not infer ERROR4 from RPC timeout |
| UC-5 Migrate / Move / Rebalance | Existing coverage, P21, P23 | Stale/unavailable target receives zero migrated bytes; alternative or explicit failure; recovery is gated |
| UC-6 Scale / drain / recovery | P21, P23 | GlobalFact and UB state must both allow; probing/exiting states reject; successful guarded probe reopens admission |
| UC-7 Destination Worker self UB fault | P22, P23 | Self-only heartbeat/lease summary reduces new read/write/migration traffic; payload/storage remains O(N) |

## Build And Test Constraints

- Remote host: `xqyun-32c32g`.
- CLion entrypoint: `scripts/clion_remote_build.sh`.
- Third-party cache: `DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache`.
- Ordinary CMake tree: `/home/worktrees/ub-fault-isolation-main/.clion-remote/build`.
- URMA Mock tree: `/home/worktrees/ub-fault-isolation-main/.clion-remote/build-urma-mock`.
- Bazel binary: `/home/cache/tools/bazel-7.4.1`.
- Bazel cache: `--output_user_root=/home/cache/bazel-ds`.
- Every new test is reported with its count and process time. Focused UT should stay below 2 seconds per target;
  individual URMA Mock ST should stay below 30 seconds unless a documented infrastructure wait dominates.

## Completion Gate

This work is not complete while any matrix row is `Partial`, `Deferred`, or backed only by an admission-unit test when
the HTML requires an end-to-end case. Completion also requires a clean worktree, pushed commits, and observable
GitCode build status `success` for the PR head SHA.
