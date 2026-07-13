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
| E2E ST | `NdsBinmockFlowTest.SpillFileToImportedVaPattern` | PASS |
| Cluster E2E | `NdsClusterSpillRwTest.SpillWriteThenNdsDirectRead` | ⏳ xqyun（待 SSH 恢复） |

证据（xqyun）：`/root/workspace/nds-ssd-hbm-meta/latest_gate0_st.log`、`latest_ut_nds.log`。

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh
bash rfc/2026-07-12-ssd-hbm-direct/scripts/run_binmock_flow_st.sh
bash rfc/2026-07-12-ssd-hbm-direct/scripts/run_cluster_spill_rw_xqyun.sh
```

**HEAD**: `ad74e989f`（集群 E2E 读写路径）

## 本 PR 已落地（代码意图）

- `AlignmentGate` / `MockIpcHbmBackend` / `FakeNdsSpillReader`
- `HbmMappingTable` / `NdsDirectPath` eligibility
- 聚焦 UT 目标 `ds_ut_nds`
- `RegisterHbmBuffer` RPC + `NdsWorkerRuntime` Get 旁路（FakeNds + DRAM bridge）
- 集群 ST：`NdsClusterSpillRwTest`（写 spill → NDS 直通读）

## 待办（不在本 PR 验收内）

| 项 | 说明 |
|----|------|
| 上游基线 | 确认 `feat/ssd-hbm-direct` 祖先包含最新 `main/master`（避免叠 fork 旧 tip） |
| xqyun 验证 | `run_cluster_spill_rw_xqyun.sh`（当前 SSH 不通） |
| Task 7–8 | EnsureCommBuffer 未 Register 路径、观测 PerfKey |
| L2 | Stage A/B 真机（docs/env-validation） |

历史夜间时间线已压缩；细节以 Story / issue-rfc 为准。
