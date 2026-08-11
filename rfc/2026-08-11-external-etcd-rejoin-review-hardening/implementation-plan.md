# External ETCD Cold-Rejoin Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three correctness gaps reported after PR 1981 without changing its synchronous cleanup architecture.

**Architecture:** Keep TopologyEngine as the recovery state owner and make the existing Worker callback compose enabled OC and SC cleanup. Reuse current migration admission, stream reset, and exit publication mechanisms, changing only their lifecycle and lock boundaries.

**Tech Stack:** C++17, GoogleTest, DataSystem ExternalCluster ST, CMake, URMA Mock.

## Global Constraints

- TDD: every production change follows a focused failing test.
- Preserve `cleanup success -> READY`; never publish READY after partial cleanup.
- Do not introduce a background cleanup state machine or deadline redesign.
- Validate on `xqyun-32c32g` with `-j32` and `/home/cache/ds-thirdparty-cache`.

---

### Task 1: Reopen migration admission after successful cleanup

**Files:**
- Modify: `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp`
- Modify: `src/datasystem/worker/object_cache/service/worker_oc_service_migrate_impl.{h,cpp}`
- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.{h,cpp}`

- [x] Add a UT proving successful cleanup reopens admission and exit intent keeps it closed.
- [x] Run the focused UT on baseline and retain the expected `K_NOT_READY` RED result.
- [x] Add the mutex-protected reopen operation and check exit intent in the same critical section.
- [x] Run the focused and neighboring migration-drain UTs to GREEN.

### Task 2: Support SC-only cold rejoin

**Files:**
- Modify: `tests/st/client/stream_cache/stream_dfx_test.cpp`
- Modify: `src/datasystem/worker/stream_cache/{client_worker_sc_service_impl,stream_manager}.{h,cpp}`
- Modify: `src/datasystem/worker/worker_oc_server.{h,cpp}`

- [x] Add a two-worker SC-only ST that suspends one Worker until ETCD removes it, resumes it, and verifies ACTIVE plus a fresh publish/subscribe exchange.
- [x] Run the ST on baseline and retain the expected recovery timeout RED result.
- [x] Add local SC reset/force-close cleanup and compose it in a Worker-level membership recreation callback.
- [x] Restore SC-only process health before reopening topology admission.
- [x] Reproduce the BRPC-only migration failure and route master-to-master SC migration through the shared transport-aware stub cache.
- [x] Add a Rocks stream metadata reset UT and reset persistent plus in-memory SC master state before membership recreation.
- [x] Run the new ST and focused OC-disabled recovery cases to GREEN.

### Task 3: Remove the transition/reconciliation lock cycle

**Files:**
- Modify: `tests/ut/worker/object_cache/worker_oc_service_impl_test.cpp`
- Modify: `src/datasystem/worker/object_cache/worker_oc_service_impl.{h,cpp}`

- [x] Add a deterministic UT that pauses at exit publication and probes `reconFlag_`.
- [x] Run on baseline and retain the expected lock-probe failure.
- [x] Return the exit-publication decision from the locked helper and call `MarkExiting()` after both reconciliation locks are released.
- [x] Run reconciliation and cold-rejoin concurrency UTs to GREEN.

### Task 4: Regression and delivery evidence

**Files:**
- Update: this RFC with exact commands, case counts, durations, and limitations.
- Update: PR 1981 description and review replies after verification.

- [x] Run CMake build with URMA Mock and the shared cache on xqyun.
- [x] Run all new UT/ST plus PR 1981 historical focused cases.
- [x] Check touched lines with `git clang-format --diff` and `git diff --check`; avoid history-wide tidy churn.
- [x] Squash the validated DataSystem change to one commit; push only to the verified yche-huawei fork.
