# PR1405 CodeCheck Triage And Codex Handoff

> **Execution note:** Use this document only after the current exact-head P0 case baseline is green. Do not mix mechanical CodeCheck cleanup, reliability fixes, and runtime slimming in one commit.

## 1. Scope And Snapshot

- Source workbook: `C:/Users/T14S/Downloads/openeuler_yuanrong-datasystem_全量导出（未解决）_20260730204945.xlsx`
- Workbook sheet: `代码问题详情(1)`
- Exported unresolved findings: **28**
- Severity: **2 serious**, **26 general**, **0 fatal**
- Exact source checked: `d1f71482d1e5fc684b4f55381931be0199c087bf`
- Worktree: `/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-oneshot-squash-20260730`
- Current local-only untracked helper files are out of scope and must remain untracked.

No CodeCheck finding in this export is direct evidence of a runtime failure. Treat each item as a maintainability signal and preserve the one-shot recovery contracts.

## 2. Executive Decision

| Disposition | Findings | Decision |
| --- | ---: | --- |
| Fix now, low-risk | 13 | Formatting, two named constants/helpers, one unused forward declaration, and explanatory handling for seven intentionally swallowed exceptions |
| Fix with an existing reliability change | 2 | Split only the two long functions whose structure overlaps real P1 correctness work |
| Request evidence-backed suppression | 13 | Eight intentional forward declarations and five cohesive/borderline long functions |
| **Total** | **28** | No blanket rule suppression |

The target is not “zero findings at any cost.” The target is zero unjustified findings without adding include cycles, reverse dependencies, one-call wrapper methods, or new files/classes.

## 3. Finding Distribution

### 3.1 By Rule

| Rule | Count | Default action |
| --- | ---: | --- |
| `G.FMT.02-CPP` / `G.FMT.06-CPP` | 3 | Fix now with repository formatting |
| `G.INC.06-CPP` forward declaration | 9 | Remove one unused declaration; suppress eight intentional declarations |
| `G.CNS.02-CPP` magic number | 2 | Fix now using semantic constants/helpers |
| `G.ERR.08-CPP` empty catch | 7 | Fix now without changing exception/control-flow semantics |
| `G.FUN.01-CPP` function length | 7 | Refactor two with P1 work; suppress five cohesive functions |

### 3.2 By File

| File | Findings | Notes |
| --- | ---: | --- |
| `worker/object_cache/worker_oc_service_impl.cpp` | 10 | One constant, five empty catches, four long functions |
| `worker/worker_oc_server.cpp` | 5 | Three formatting, two composition/builder functions |
| `worker/worker_oc_server.h` | 3 | Test friend plus two `unique_ptr` incomplete types |
| `worker/object_cache/recovery/object_cache_recovery_state.cpp` | 2 | Two catches for a terminal callback |
| Eight other files | 8 | One finding each |

## 4. Fix-Now Allowlist

Keep this as one or two mechanical commits after P0. Do not touch behavior outside these exact hunks.

### 4.1 Formatting: 3 Findings

File: `src/datasystem/worker/worker_oc_server.cpp`

- Export lines 1287, 1326, and 1373.
- Run `clang-format` only on the touched file after checking the current line locations.
- Inspect the resulting diff. Do not accept broad unrelated formatting churn.

Recommended commit:

```text
style(worker): fix CodeCheck formatting findings
```

### 4.2 Magic Numbers: 2 Findings

1. `src/datasystem/master/object_cache/master_oc_service_impl.cpp`, exported line 797

Replace `FLAGS_node_dead_timeout_s * 1000` with a chrono conversion, for example a duration cast from seconds to milliseconds. Do not introduce another generic numeric constant when the unit conversion itself expresses the intent.

2. `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`, exported line 915

Replace `businessOperationId.substr(0, 12)` with the existing `cluster::TopologyDiagnosticPrefix()` utility from `cluster/model/topology_diagnostics.h`. This keeps the same diagnostic-prefix policy as topology executor logs and avoids a second prefix-size definition.

Recommended commit:

```text
style(object-cache): name CodeCheck literals
```

### 4.3 Unused Forward Declaration: 1 Finding

File: `src/datasystem/worker/runtime/worker_recovery_controller.h`, exported line 27.

`class WorkerRuntimeFacade;` has no use in this header. Remove the declaration; do not include `worker_runtime_facade.h`. The whole controller is also a later runtime-slimming candidate, but removing this unused declaration is safe if CodeCheck cleanup lands first.

### 4.4 Empty Catch Clauses: 7 Findings

Files:

- `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`: exported lines 2851, 2856, 2862, 2883, 2888.
- `src/datasystem/worker/object_cache/recovery/object_cache_recovery_state.cpp`: exported lines 309, 310.

Minimal rule-compliant action:

- Keep the asynchronous/terminal callback boundary non-throwing.
- Add a concise reason inside each intentionally empty catch, such as “best-effort logging/completion must not escape the async task.”
- Where adjacent typed and catch-all handlers perform exactly the same no-op, collapse them to one `catch (...)` with one reason.
- Do not add nested logging merely to satisfy the rule; the existing nested catches exist because logging/completion may itself throw.
- Do not change exactly-once completion behavior in this mechanical commit.

These seven findings overlap the later `ScheduleReconciliationRequest` cleanup. If that P1 refactor is already in progress, let the semantic commit remove the catches instead of adding comments that it immediately deletes.

## 5. Refactor Only With Existing P1 Work

### 5.1 `GetReadyToWork`: 72 Lines

File: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`, exported line 1744.

This function currently combines:

- reconciliation-count readiness;
- stale-generation rejection;
- restart health publication;
- topology reconciliation completion;
- runtime evidence publication;
- an injection-point compatibility path.

It also calls `PublishRecoveryCompletionIfCurrent()` with callbacks that perform health/topology side effects. That overlaps the existing P1 requirement to avoid holding the recovery-generation mutex across external work.

Decision: **do not create a standalone CodeCheck refactor**. While fixing lock scope, split at most into two existing-class private methods:

- restart health completion;
- topology/runtime recovery completion.

No new file, class, interface, or generic “recovery completion framework.” Preserve stale-generation fencing before and after external work.

### 5.2 `ScheduleReconciliationRequest`: 76 Lines

File: `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`, exported line 2823.

This function combines scheduling, RPC construction/execution, exception translation, exactly-once completion, and best-effort logging. Its length is not just cosmetic: the nested exception paths make terminal behavior difficult to audit.

Decision: refactor only with the P1 incident/retry and completion work. Keep it in the same `.cpp`; introduce at most one private/file-local non-throwing execution helper and one completion helper. Required tests:

- callback invoked exactly once on RPC success;
- callback invoked exactly once on RPC error and exception;
- callback exception does not escape;
- scheduler exception is returned to the caller;
- shutdown/superseded generation does not reopen admission or lose terminal state.

Do not create a scheduler class or a generic exception utility.

## 6. Suppress With Evidence: Forward Declarations

Apply for suppression individually or by identical rationale group. Do not suppress `G.INC.06-CPP` repository-wide.

| File / declaration | Why include is worse | Decision |
| --- | --- | --- |
| `worker_topology_object_cache_actions.h`: `WorkerOCServiceImpl` | Used only as provider/raw pointer; implementation includes the full service header. Including the large service header reverses the adapter dependency and increases compile fanout. | Suppress |
| `worker_coordination_backend_factory.h`: `EtcdStore` | Used only as a factory pointer parameter. The factory boundary intentionally keeps worker composition from importing the concrete Etcd implementation. | Suppress |
| `worker_oc_server.h`: test fixture | A production header cannot include a test `.cpp` definition; the forward declaration is required for the friend test. | Suppress |
| `worker_oc_server.h`: `WorkerIsolationCoordinator` | Held by `unique_ptr`; destructor is out-of-line and the `.cpp` includes the definition. Including it expands the server header dependency surface. | Suppress |
| `worker_oc_server.h`: `WorkerTopologyRuntimeAdapter` | Same incomplete-type `unique_ptr` pattern; definition is consumed only in the `.cpp`. | Suppress |
| `worker_oc_service_impl.h`: `IWorkerTopologyRuntime` | Non-owning raw pointer; the `.cpp` includes the runtime header. Including the concrete runtime boundary in this already large public service header increases coupling. | Suppress |
| `worker_topology_metadata_actions.h`: `MetadataManagerHolder` | Reference member; implementation includes the holder definition. Adapter interface should not export the master composition header. | Suppress |
| `cluster/executor/cancellation_token.h`: `TopologyTaskExecutor` | Friend-only access. Including the executor would introduce the direct executor/token include cycle. | Suppress |

Suggested suppression wording:

```text
The declaration is used only through a pointer/reference/friend or an incomplete-type unique_ptr whose destructor is
defined out of line. The implementation file includes the complete type. Replacing it with an include would create a
header cycle, reverse the intended dependency direction, or materially increase public-header compile fanout without
improving type safety. Keep the forward declaration for this boundary.
```

For the test fixture, use the narrower wording:

```text
This production header declares a friend test fixture. The fixture has no production header that can be included;
including test implementation from production is invalid. The forward declaration is required.
```

## 7. Suppress With Evidence: Cohesive Long Functions

| Function | Reported size | Rationale | Decision |
| --- | ---: | --- | --- |
| `TopologyEngine::IsControlBackendReachableFromPeers` | 51 | One bounded probe loop with a single availability decision; only one line over threshold. Extraction would hide loop invariants or create a one-call helper. | Suppress |
| `WorkerOCServiceImpl::RecoverMetadataOfRestartedWorkers` | 54 | One batch-recovery transaction: normalize restart facts, select objects, recover, publish diagnostics. Borderline size and state is local. | Suppress |
| `WorkerOCServiceImpl::ReconcileNetworkRecoveryOwnership` | 60 | One generation-fenced ownership fanout transaction. Splitting registration and scheduling would make ordering/lifetime harder to audit. | Suppress |
| `WorkerOCServer::CreateObjectCacheWorkerServices` | 63 | Composition-root wiring, not an algorithm. Splitting creates more builder/wrapper functions without reducing coupling. Runtime slimming may reduce it naturally later. | Suppress |
| `WorkerOCServer::ConfigureTopologyBuilder` | 55 | Declarative builder configuration for one topology engine. Additional callback-builder abstractions would worsen the current over-design problem. | Suppress |

Suggested suppression wording:

```text
The function implements one cohesive orchestration/composition transaction and exceeds the 50-line guideline only
modestly. Its local variables encode ordering and lifetime invariants. Splitting it would add one-call helpers or
scatter a single builder/transaction without reducing cyclomatic complexity. Keep it intact; reassess only when a
behavioral change introduces a real second responsibility.
```

Do not use this wording for `GetReadyToWork` or `ScheduleReconciliationRequest`; those two have real separable responsibilities.

## 8. Codex Execution Plan

### CC0: Refresh Before Editing

1. Wait for the current P0 exact-head direct-gtest matrix to finish.
2. Re-export or re-run CodeCheck against the then-current exact head when possible; line numbers in the workbook are advisory.
3. Verify `git status`, exact HEAD, `main/master`, and remotes.
4. Run CodeGraph discovery against the shared index, then confirm every conclusion using exact-head `rg` and BUILD/CMake files. Never sync or rebuild the shared index.

### CC1: Mechanical Cleanup

Allowlist:

- `src/datasystem/worker/worker_oc_server.cpp`
- `src/datasystem/master/object_cache/master_oc_service_impl.cpp`
- `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`
- `src/datasystem/worker/object_cache/recovery/object_cache_recovery_state.cpp`
- `src/datasystem/worker/runtime/worker_recovery_controller.h`

Apply only Sections 4.1-4.4. Keep commits mechanical and behavior-neutral. If a listed hunk has already changed during P1 work, stop and re-triage instead of replaying by line number.

### CC2: Submit Suppression Evidence

Prepare 13 item-level suppression requests from Sections 6 and 7. Attach:

- exact declaration/function location;
- pointer/reference/friend/`unique_ptr` usage or cohesive responsibility;
- implementation include and out-of-line destructor evidence where applicable;
- include-cycle or dependency-direction explanation;
- statement that no runtime behavior is being waived.

### CC3: Let P1 Absorb Two Structural Fixes

Refactor `GetReadyToWork` only with recovery lock-scope correction. Refactor `ScheduleReconciliationRequest` only with terminal/retry correctness. Do not create CodeCheck-only wrapper commits for them.

### CC4: Verification

For every source edit:

```bash
git diff --check
git diff --name-only
```

Run `clang-format -i` on touched C++ files and inspect the diff. Build the nearest worker runtime/object-cache/master object-cache targets, then run focused tests covering:

- recovery generation fencing;
- exactly-once ownership fanout completion;
- local isolation and recovery;
- master reconciliation request handling.

After CC1/CC3, rerun the P0 direct-gtest case matrix. Run the repository-local sensitive scan fail-closed before any fork update.

## 9. Non-Goals And Guardrails

- Do not include concrete headers merely to make `G.INC.06-CPP` disappear.
- Do not add umbrella headers, callback interfaces, builders, or generic exception wrappers.
- Do not split a 51-60 line cohesive transaction into one-call methods.
- Do not combine CodeCheck cleanup with runtime wrapper deletion or coordination ownership movement.
- Do not weaken stale-generation, exactly-once completion, `ServingReady`, or `Complete` semantics.
- Do not touch Stream or local CLion helper files.
- Never push to `openeuler/yuanrong-datasystem`; fork-only after explicit verification. Do not issue `/retest` or alter WIP state.

## 10. Completion Report Template

```text
Exact head:
CodeCheck input/export time:

Fixed:
- formatting: x/3
- magic numbers: x/2
- empty catches: x/7
- unused forward declarations: x/1
- P1-aligned long functions: x/2

Suppression requested/approved:
- intentional forward declarations: x/8
- cohesive long functions: x/5

Diff:
- source files / lines:
- test files / lines:
- build files / lines:

Evidence:
- diff-check:
- format:
- build:
- focused UT:
- P0 ST matrix:
- sensitive scan:

Residual gaps:
```
