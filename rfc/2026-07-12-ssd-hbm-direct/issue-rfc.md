# [RFC] SSD→HBM Direct (NDS) — Track① 可注入接口 + binmock 验证

**Status**: In-Progress  
**Branch**: `feat/ssd-hbm-direct` ← `origin/master` @ `11805014`  
**RFC**: `rfc/2026-07-12-ssd-hbm-direct/`  
**关联 PR**: _(见 pr-description.md，合入后回填)_

---

## 背景与问题

本地 **已落盘 spill** 对象当前走 DRAM reload + RH2D；产品需要 **SSD→HBM 直通**（经 NDS/xds + CANN IPC），降时延。Phase-1 范围：

- 仅本机 `IsSpilled`；无跨机 SSD→HBM；**不改** spilled→RH2D 禁令
- 统一 KV `Get`/`MGet` + `RegisterHbmBuffer`
- 对齐默认 4K；失败 → DRAM fallback

无 NPU 环境需用 **binmock + Fake backend** 先把 Track① 模块与流程串通，再 L2 换真 IPC/xds。

---

## 本 PR / 阶段目标（Track① Step 1–3）

| Task | 内容 | 状态 |
|------|------|------|
| Gate 0 | 隔离树 + 5 个聚焦 `HeteroD2HTest`（binmock 基线） | ✅ xqyun 5/5 |
| Task 1 | `AlignmentGatePass()` | ✅ 代码 + UT |
| Task 2 | `IpcHbmBackend` + `MockIpcHbmBackend` | ✅ 代码 + UT |
| Task 3 | `NdsSpillReader` + `FakeNdsSpillReader` | ✅ 代码 + UT |
| Task 4a | `HbmMappingTable` + `NdsDirectPath` | ✅ 代码 + UT（本 PR） |
| Task 4b | RegisterHbmBuffer RPC | ⏳ issue 跟踪 |
| Task 5 | Get 旁路 `worker_oc_service_get_impl.cpp` | ⏳ issue 跟踪 |
| Task 6 | `NdsBinmockFlow` e2e ST | ⏳ issue 跟踪 |

---

## 如何用（复现）

详见 **[test-walkthrough.md](./test-walkthrough.md)**。

### Gate 0 — 5 个 binmock ST（不扫全 suite）

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/run_existing_hetero_st_xqyun.sh
```

Filter：`HeteroD2HTest.{Perf,TestNoExist,TestAllExist,TestPartExist,TestMSetD2HMsgWithInvalidDeviceId}`

### Track① UT — 14 cases

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh
```

```bash
./ds_ut_nds --gtest_filter='AlignmentGateTest.*:MockIpcHbmBackendTest.*:FakeNdsSpillReaderTest.*:HbmMappingTableTest.*:NdsDirectPathTest.*'
```

---

## 验证快照（xqyun 隔离树）

| 项 | 结果 | 证据 |
|----|------|------|
| 隔离编 `ds_device_llt` | 已产出 | `/root/workspace/build-ssd-hbm-direct/tests/st/ds_device_llt` |
| Gate 0（5 cases） | _待填_ | `nds-ssd-hbm-meta/latest_gate0_st.log` |
| `ds_ut_nds`（8 cases） | _待填_ | incremental build + UT log |
| `NdsBinmockFlow` e2e | N/A | Task 6 未实现 |

> 注：曾用 `HeteroD2H*` 误跑 Evict/Tcp 变体导致 7 FAIL；已收窄 filter（`gtest_filters.sh`）。

---

## 源码修改目的

| 路径 | 目的 |
|------|------|
| `common/device/nds/alignment_gate.*` | NDS 直通对齐门禁（默认 4K） |
| `common/device/hbm_ipc/*` | CANN IPC 抽象 + 同进程 Mock（binmock/UT） |
| `common/device/nds/fake_nds_spill_reader.*` | 无 xds 时 pread spill → memcpy 到 imported VA |
| `tests/ut/.../ds_ut_nds` | Track① 聚焦 UT（不进全量 `ds_ut`） |
| `rfc/.../scripts/*` | xqyun 隔离 verify + gtest filter 约定 |

**未改**：Worker Get 旁路、Register RPC、生产热路径行为。

---

## GitCode / GitHub Issues（跟踪）

| # | 标题 | 优先级 | 说明 |
|---|------|--------|------|
| _TBD_ | [Feature] SSD→HBM Task 4: HbmMappingTable + RegisterHbmBuffer RPC | P1 | MockIpc Import 填表 |
| #13 | [Feature] SSD→HBM Task 5: local spilled Get NDS bypass + fallback | P1 | `worker_oc_service_get_impl.cpp` |
| #14 | [Feature] SSD→HBM Task 6: NdsBinmockFlow e2e ST | P1 | binmock 全链路判据 |
| #15 | [Feature] SSD→HBM Task 9: CannIpcHbmBackend（L2 Stage A） | P2 | 真 CANN IPC |
| #17 | [Feature] SSD→HBM Task 10: XdsNdsSpillReader（L2 Stage B） | P2 | 依赖 Stage A |
| #18 | [Tech Debt] observability: WORKER_NDS_* PerfKey + access keys | P2 | Task 8 |

_PR 合入后由 maintainer/agent 在 GitCode 创建 issue 并回填 `#` 列。_

---

## 非目标（本 PR）

- 真 CANN IPC / 真 xds ioctl（L2）
- 跨机 SSD→HBM
- spill 写侧 pad / HBM→SSD 写路径
- 全量 `ds_device_llt` / `ctest` 扫描

---

## 建议结论

本 PR 为 **Track① 第一阶段**：落地三个可注入接口 + 聚焦 UT/Gate0 脚本，为后续 Register/Get 旁路/e2e ST 提供稳定地基。合入后继续 Task 4–6 小步 PR。
