# Worker-Coordinator Isolation Rejoin RFC

| Attribute | Value |
|---|---|
| Created | 2026-08-01 |
| Source branch | `feat/worker-coordinator-isolation-rejoin` |
| Source baseline | `main/master@a90f6c6b718857367575068c83fb976494f6c751` |
| Scope | Measure 2: worker keeps running during worker-coordinator isolation and rejoins after removal |
| GitCode issue | `openeuler/yuanrong-datasystem#924` |
| GitCode PR | `openeuler/yuanrong-datasystem!1798` |
| Worktree | `yuanrong-datasystem/.worktrees/worker-coordinator-isolation-rejoin-20260801` |

## Files

| File | Purpose |
|---|---|
| `as-is.md` | Current-code evidence and constraints before implementation |
| `detailed-design.md` | Detailed design aligned with `措施二.md` |
| `implementation-plan.md` | TDD+SDD task plan, commits, and validation gates |
| `validation.md` | Running validation ledger for UT, ST, CMake, Bazel, clang-format, clang-tidy, and remote evidence |

## Scope Decision

This RFC implements the Coordinator-backend worker isolation path first. ETCD-backend keepalive still contains an
independent SIGKILL fallback; changing that path would broaden this PR into a second backend compatibility change and is
recorded as a follow-up risk unless review asks to include it.

## Push And PR Rules

- Never push to `openeuler/yuanrong-datasystem`.
- Push only to the verified yche/yche-huawei fork remote.
- Create the PR only after focused UT, selected ST, CMake build, Bazel build, clang-format, and clang-tidy evidence is
  available.
- PR text must include UT/ST case names and per-case runtimes.
