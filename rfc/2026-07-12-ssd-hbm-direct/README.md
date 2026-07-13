# SSD→HBM Direct (NDS)

**Status**: In-Progress  
**Branch**: `feat/ssd-hbm-direct` ← `main/master`（openeuler）  
**PR**: [!1312](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1312) · **Issue**: [#12](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/12)（`Fixes #12`）  
**Story**: [design-and-story.md](./design-and-story.md)

## 一句话

本机已落盘 spill 走 NDS SSD→HBM 直通；统一 KV + Register；SSD 不接 RH2D。本 PR 只落可注入接口 + mapping 表 + 聚焦 UT/Gate0。

## 读什么

| 用途 | 文档 |
|------|------|
| **交接** | [HANDOVER.md](./HANDOVER.md) |
| Story / 验收 | [design-and-story.md](./design-and-story.md) |
| 设计 / 决策 / WBS | [design.md](./design.md) · [decisions.md](./decisions.md) · [work-breakdown.md](./work-breakdown.md) |
| 验证 | [test-walkthrough.md](./test-walkthrough.md) · [scripts/README.md](./scripts/README.md) · [results.md](./results.md) |
| 交付 | [issue-rfc.md](./issue-rfc.md) · [pr-description.md](./pr-description.md) · [pr-body.gitcode.md](./pr-body.gitcode.md) |
| 深度参考 | [docs/](./docs/README.md) |

## 当前进度

| 项 | 状态 |
|----|------|
| Task 1–5 + 单进程/集群 E2E 代码 | ✅ 合入 `ad74e989f` |
| GitCode MR !1312 | ✅ 已开 |
| 集群 ST xqyun 验证 | ⏳ SSH 恢复后跑 `run_cluster_spill_rw_xqyun.sh` |
| 基线对齐 `main/master` | ⏳ 合入前 rebase |
| Task 7–8 / L2 | ⏳ 见 [HANDOVER.md](./HANDOVER.md) |

## 验证

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh
```

## 三事项

| # | 内容 | L0/L1 | L2 |
|---|------|-------|-----|
| ① | 模块 + binmock | **本 PR** | 换真 backend |
| ② | CANN IPC | MockIpc | Stage A |
| ③ | SSD→HBM | FakeNds | Stage B |
