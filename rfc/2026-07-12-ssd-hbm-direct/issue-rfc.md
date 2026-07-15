# [RFC] SSD→HBM Direct — Track① PR-1

**Status**: In-Progress
**Branch**: `feat/ssd-hbm-direct` ← `main/master`
**Story**: [design-and-story.md](./design-and-story.md)
**PR**: [!1312](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1312) · **Issue**: [#12](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/12)（1 PR ↔ 1 issue）

## 背景

本机已落盘 spill 今天走 DRAM reload；产品要 NDS SSD→HBM。Phase-1：仅本机 `IsSpilled`；不改 RH2D 禁令；对齐默认 4K；无 NPU 用 binmock + Fake。

## 本 PR 范围

| Task | 内容 | 状态 |
|------|------|------|
| Gate 0 | 5× `HeteroD2HTest` | ✅ xqyun |
| 1–3 | AlignmentGate / MockIpc / FakeNds | ✅ |
| 4a | HbmMappingTable + NdsDirectPath | ✅ |
| 4b–6 | Register RPC / Get 旁路 / e2e ST | 后续 PR（[WBS](./work-breakdown.md)） |

## 复现

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh
```

详见 [test-walkthrough.md](./test-walkthrough.md)。

## 验证快照（xqyun）

| 项 | 结果 |
|----|------|
| Gate0 5 cases | PASS |
| `ds_ut_nds` 14 cases | PASS |

## 开放项（文档跟踪，非本 PR 多 issue）

1. **基线**：特性须基于 `main/master`；当前 MR 仍可能叠在 fork 旧 tip 上，需 cherry-pick 仅 NDS 提交到最新上游后再 force-with-lease。
2. **MR 描述**：确认网页上有 `Fixes #12`。
3. **后续**：Task 4b–6 各开 **1 issue + 1 PR**。

## 非目标

真 CANN/xds、跨机 SSD→HBM、本 PR 改 Worker Get 热路径、全量 `ds_device_llt`。
