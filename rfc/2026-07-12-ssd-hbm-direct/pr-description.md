# PR 说明 — Track① 可注入接口 + mapping 表

**Branch**: `feat/ssd-hbm-direct` ← `main/master`
**MR**: [!1312](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1312)
**Issue**: `Fixes #12`（[#12](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/12)）
**Body 文件**: [pr-body.gitcode.md](./pr-body.gitcode.md)
**RFC**: [README.md](./README.md) · [issue-rfc.md](./issue-rfc.md)

## Summary

Phase-1 Track① 地基：NDS 直通所需可注入接口 + Worker mapping 表 + 聚焦 UT/Gate0。

- `AlignmentGatePass`（默认 4KiB）
- `MockIpcHbmBackend`（同进程 Export→Import）
- `FakeNdsSpillReader`（pread → imported VA）
- `HbmMappingTable` / `NdsDirectPath` eligibility
- `ds_ut_nds`（14 cases）

**不包含**：Register RPC、Get 旁路、`NdsBinmockFlow`、真 CANN/xds。

## 基线

MR target = openeuler `master`。特性提交须基于 **`main/master`**，不要基于 fork 陈旧 `origin/master`。

跨 fork 创建/更新 PR 时：

```bash
--head yche-huawei:feat/ssd-hbm-direct
```

一键脚本：[scripts/publish_gitcode_track1.sh](./scripts/publish_gitcode_track1.sh)（`create_pr.py` 用主仓 `.skills` 路径）。

## 验证

xqyun 隔离树：Gate0 5/5 + UT 14/14。见 [test-walkthrough.md](./test-walkthrough.md)、[results.md](./results.md)。
