# [RFC] SSD→HBM Direct (NDS) — Track① 可注入接口 + binmock 验证

**Status**: In-Progress  
**Branch**: `feat/ssd-hbm-direct` ← `origin/master`  
**Story**: [design-and-story.md](./design-and-story.md)  
**RFC**: `rfc/2026-07-12-ssd-hbm-direct/`  
**关联 PR**: [openeuler/yuanrong-datasystem !1312](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1312)（`feat/ssd-hbm-direct` → `master`，`Fixes #12`）

---

## 背景与问题

本地 **已落盘 spill** 对象当前走 DRAM reload + RH2D；产品需要 **SSD→HBM 直通**（经 NDS/xds + CANN IPC），降时延。Phase-1 范围：

- 仅本机 `IsSpilled`；无跨机 SSD→HBM；**不改** spilled→RH2D 禁令
- 统一 KV `Get`/`MGet` + `RegisterHbmBuffer`
- 对齐默认 4K；失败 → DRAM fallback

无 NPU 环境需用 **binmock + Fake backend** 先把 Track① 模块与流程串通，再 L2 换真 IPC/xds。

---

## 本 PR / 阶段目标（Track① PR-1）

| Task | 内容 | 状态 |
|------|------|------|
| Gate 0 | 隔离树 + 5 个聚焦 `HeteroD2HTest`（binmock 基线） | ✅ xqyun 5/5 |
| Task 1 | `AlignmentGatePass()` | ✅ 代码 + UT |
| Task 2 | `IpcHbmBackend` + `MockIpcHbmBackend` | ✅ 代码 + UT |
| Task 3 | `NdsSpillReader` + `FakeNdsSpillReader` | ✅ 代码 + UT |
| Task 4a | `HbmMappingTable` + `NdsDirectPath` | ✅ 代码 + UT（本 PR） |
| Task 4b | RegisterHbmBuffer RPC | ⏳ 后续 PR（见 WBS） |
| Task 5 | Get 旁路 `worker_oc_service_get_impl.cpp` | ⏳ 后续 PR（见 WBS） |
| Task 6 | `NdsBinmockFlow` e2e ST | ⏳ 后续 PR（见 WBS） |

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
| Gate 0（5 cases） | **PASS** 5/5 | `nds-ssd-hbm-meta/latest_gate0_st.log` |
| `ds_ut_nds`（14 cases） | **PASS** 14/14 | `nds-ssd-hbm-meta/latest_ut_nds.log` |
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

## GitCode Issue（本 PR）

**仓库**：`openeuler/yuanrong-datasystem`（PR 从 fork `yche-huawei/yuanrong-datasystem` `feat/ssd-hbm-direct` 提交）  
**约定**：**1 PR ↔ 1 issue**。本 PR 只关联下列 issue；Task 4b–6 / L2 等在 [work-breakdown.md](./work-breakdown.md) 跟踪，**不**另开 issue。

| # | 标题 | PR |
|---|------|-----|
| [#12](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/12) | SSD→HBM Direct Track① — injectable interfaces + binmock verify | **本 PR** `Fixes #12` |

> 若曾误建 #13–#18 子 issue，可关闭；后续小步 PR 各自再开 **1 issue / 1 PR** 即可。

---

## 非目标（本 PR）

- 真 CANN IPC / 真 xds ioctl（L2）
- 跨机 SSD→HBM
- spill 写侧 pad / HBM→SSD 写路径
- 全量 `ds_device_llt` / `ctest` 扫描

---

## 建议结论

本 PR 为 **Track① 第一阶段**：落地三个可注入接口 + 聚焦 UT/Gate0 脚本，为后续 Register/Get 旁路/e2e ST 提供稳定地基。合入后继续 Task 4–6 小步 PR。
