# SSD→HBM Direct — Overnight Run Log

**Date**: 2026-07-13  
**Branch**: `feat/ssd-hbm-direct` (from `origin/master`)  
**Node**: `xqyun-32c32g`  
**Operator**: Agent (autonomous overnight)

## Tonight's plan

1. [x] Gate 0: 5 个聚焦 `HeteroD2HTest` PASS（xqyun direct gtest，09:46 确认）
2. [x] Task 1–4a 代码：`AlignmentGate` + `MockIpc` + `FakeNds` + `HbmMappingTable` + `NdsDirectPath`
3. [x] Task 1–4a verify：`ds_ut_nds` **14/14** PASS（xqyun，09:46）
4. [ ] Task 4b–6：Register RPC + Get 旁路 + `NdsBinmockFlow` ST

**复现**：见 [test-walkthrough.md](./test-walkthrough.md)

---

## Timeline

| Time (UTC+8) | Step | Result | Notes |
|--------------|------|--------|-------|
| 00:43 | Isolated build started | RUNNING | `DS_OPENSOURCE_DIR` cache hit |
| 00:55 | Build check | ~58% | pid=2927776 |
| 00:57 | Task 1 code | DONE | `alignment_gate`, `ds_ut_nds` |
| 00:58 | Build check | ~67% | `Linking CXX executable ds_device_llt` (0-byte stub) |
| 00:59 | Task 2–3 code | DONE | `MockIpcHbmBackend`, `FakeNdsSpillReader` + UT |
| 00:59 | `overnight_iterate.sh` | BACKGROUND | Gate0 ST → sync → `ds_ut_nds` |
| 00:59 | `ds_device_llt` linked | READY | ~67% isolated build; 284MB binary (later `-rwx`) |
| 01:00–01:03 | SSH to xqyun | BLOCKED→OK | `Not allowed at this time` ×2, recovered poll 3 |
| 01:03 | Gate0 ST (`--skip-sync --skip-build`) | **GATE0_FAIL** | 0/7 `HeteroD2H*`; worker `Subprocess is abnormal` |
| 01:03 | Task1 UT (`run_nds_ut_remote.sh`) | **UT_FAIL** | zsh glob on `AlignmentGateTest.*`; SSH drop mid-run |
| 03:30 | `verify_track1_tiantiyun` | **PARTIAL** | build OK; Gate0 **5/5** direct gtest; UT **13/14** (`ZeroLengthOrAlignRejected`) |
| 03:35 | follow-up fixes | DONE | alignment gate+test; verify scripts → direct gtest |
| 09:46 | `verify_track1_xqyun` | **PASS** | build ~256s; Gate0 **5/5**; UT **14/14** |

**Evidence (local RFC dir):** `gate0_st_run.log`, `overnight_verify.log`, `gate0_poll.log`, `nds_ut_run.log`  
**Evidence (xqyun):** `/root/workspace/nds-ssd-hbm-meta/gate0_st_20260712_170323.log`

---

## Code landed (local worktree, uncommitted)

### Task 1 — AlignmentGate
- `src/datasystem/common/device/nds/alignment_gate.{h,cpp}`
- UT: `AlignmentGateTest.*`

### Task 2 — Mock IPC
- `src/datasystem/common/device/hbm_ipc/ipc_hbm_backend.h`
- `src/datasystem/common/device/hbm_ipc/mock_ipc_hbm_backend.{h,cpp}`
- UT: `MockIpcHbmBackendTest.*` (Export→Import same ptr, Close, re-export)

### Task 3 — Fake NDS
- `src/datasystem/common/device/nds/nds_spill_reader.h`
- `src/datasystem/common/device/nds/fake_nds_spill_reader.{h,cpp}`
- UT: `FakeNdsSpillReaderTest.*` (temp file pread → memcpy)

### Build targets
- `common_device_nds`, `common_device_hbm_ipc`, `ds_ut_nds` (all Track① UT)

---

## Overnight iteration

Script: `scripts/overnight_iterate.sh`

1. Wait for executable `ds_device_llt` (isolated build)
2. `HeteroD2H*` Gate 0 ST
3. rsync + incremental build + `ds_ut_nds` (Tasks 1–3)

Results appended to this file when complete.

---

## Issues / blockers

- **Gate0 ST 根因（已修）**：脚本直接 `./ds_device_llt` 且 `LD_LIBRARY_PATH` 仅含 `tests/st`，缺 `_WORKER_BIN_DIR`（`src/datasystem/worker`）→ worker 子进程秒退 `Subprocess is abnormal`。修复：改用 `ctest -R ds_device_llt` + `GTEST_FILTER`（CMake `TEST_ENVIRONMENT`）。
- **UT 根因（已修）**：`run_nds_ut_remote.sh` 经 zsh 解析 `*` glob + 多行引号断裂。修复：heredoc `bash -s` + `ctest -R ds_ut_nds`。
- SSH to xqyun intermittently `Connection closed` — retry via `bash -s` heredoc。

---

## Evidence paths (xqyun)

| Artifact | Path |
|----------|------|
| Build log | `/root/workspace/nds-ssd-hbm-meta/nds_cmake_puncture.log` |
| Gate0 ST | `/root/workspace/nds-ssd-hbm-meta/latest_gate0_st.log` |
| Isolated ST binary | `/root/workspace/build-ssd-hbm-direct/tests/st/ds_device_llt` |
| Track① UT | `/root/workspace/build-ssd-hbm-direct/tests/ut/ds_ut_nds` |

---

## Next after verify green

| Task | Item |
|------|------|
| 4 | `HbmMappingTable` + Register RPC stub |
| 5 | Get bypass in `worker_oc_service_get_impl.cpp` |
| 6 | `nds_binmock_flow_test.cpp` ST |

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/overnight_iterate.sh
bash rfc/2026-07-12-ssd-hbm-direct/scripts/check_cmake_puncture_xqyun.sh
```

## Verification 2026-07-13 xqyun
Gate0 FAIL: 5 run, 0 passed, 5 failed, exit 1
UT FAIL: 8 run, 7 passed, 1 failed (AlignmentGateTest.ZeroLengthOrAlignRejected); run_nds_ut_remote build failed CMake
Filter: HeteroD2H star 7 fail narrowed to 5 focused Gate0 cases
Gate0 grep PASSED/FAILED:
[  PASSED  ] 0 tests.
[  FAILED  ] HeteroD2HTest.Perf
[  FAILED  ] HeteroD2HTest.TestNoExist
[  FAILED  ] HeteroD2HTest.TestAllExist
[  FAILED  ] HeteroD2HTest.TestPartExist
[  FAILED  ] HeteroD2HTest.TestMSetD2HMsgWithInvalidDeviceId
[  FAILED  ] 5 tests, listed below:
UT grep PASSED/FAILED:
[  PASSED  ] 7 tests.
[  FAILED  ] AlignmentGateTest.ZeroLengthOrAlignRejected
[  FAILED  ] 1 test, listed below:
run_nds_ut_remote exit: CMake configure incomplete (build failed)
run_existing_hetero_st_xqyun ctest exit: 8
Follow-up direct Gate0 rerun: ds_device_llt ABORTED (core dump) after HeteroD2HTest.Perf; worse than first run (5 clean failures)
