# Results — Track① PR-1

**Branch**: `feat/ssd-hbm-direct` ← `main/master`  
**MR**: [!1312](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1312)  
**Issue**: [#12](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/12)  
**Verify node**: `xqyun-32c32g`（隔离树）

## 已验证（聚焦，非全量 ST）

| 项 | Filter / 目标 | 结果 |
|----|---------------|------|
| Gate0 | 5× `HeteroD2HTest` | PASS |
| UT | `ds_ut_nds` 14 cases | PASS |
| E2E ST | NdsBinmockFlowTest.SpillFileToImportedVaPattern | PASS |

证据（xqyun）：`/root/workspace/nds-ssd-hbm-meta/latest_gate0_st.log`、`latest_ut_nds.log`。
- E2E ST: run_binmock_flow_st.sh; HEAD: 7aab2bff1

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh
```

## 本 PR 已落地（代码意图）

- `AlignmentGate` / `MockIpcHbmBackend` / `FakeNdsSpillReader`
- `HbmMappingTable` / `NdsDirectPath` eligibility
- 聚焦 UT 目标 `ds_ut_nds`

## 待办（不在本 PR 验收内）

| 项 | 说明 |
|----|------|
| 上游基线 | 确认 `feat/ssd-hbm-direct` 祖先包含最新 `main/master`（避免叠 fork 旧 tip） |
| Task 4b–6 | Register RPC、Get 旁路、`NdsBinmockFlow`（WBS） |
| L2 | Stage A/B 真机（docs/env-validation） |

历史夜间时间线已压缩；细节以 Story / issue-rfc 为准。
