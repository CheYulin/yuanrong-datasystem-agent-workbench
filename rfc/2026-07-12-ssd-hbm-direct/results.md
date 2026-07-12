# SSD→HBM Direct — Overnight Run Log

**Date**: 2026-07-13  
**Branch**: `feat/ssd-hbm-direct` (from `origin/master`)  
**Node**: `xqyun-32c32g`  
**Operator**: Agent (autonomous overnight)

## Tonight's plan

1. [ ] Gate 0: 5 个聚焦 `HeteroD2HTest` PASS（已修正 filter，不再跑 Evict/Tcp）
2. [x] Task 1–3 代码：`AlignmentGate` + `MockIpc` + `FakeNds`
3. [ ] Task 1–3 verify：`ds_ut_nds` 8 cases *(后台跑)*
4. [ ] Task 4–6：Register + Get 旁路 + `NdsBinmockFlow` ST

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

- SSH to xqyun intermittently `Connection closed` — retry via `bash -lc` from WSL workbench cwd.
- Gate 0 build started before Task 1–3 sync; ST uses pre-sync baseline; UT uses post-sync incremental.

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
