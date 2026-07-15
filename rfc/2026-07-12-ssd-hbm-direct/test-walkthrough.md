# 验证复现手册（Track①）

**节点**：`xqyun-32c32g` 隔离树
**原则**：只跑本 RFC 聚焦用例，**不**扫全量 `ds_device_llt` / `ctest`。

## 一键

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh
```

Gate0（5）+ `ds_ut_nds`（14）。路径约定见 [scripts/BUILD_VERIFY.md](./scripts/BUILD_VERIFY.md)。

## Gate 0（5× HeteroD2H）

```
HeteroD2HTest.Perf
HeteroD2HTest.TestNoExist
HeteroD2HTest.TestAllExist
HeteroD2HTest.TestPartExist
HeteroD2HTest.TestMSetD2HMsgWithInvalidDeviceId
```

**不要**用 `HeteroD2H*`（会误跑 Evict/Tcp）。Filter：`scripts/gtest_filters.sh`。

```bash
bash rfc/.../scripts/run_existing_hetero_st_xqyun.sh
```

直接跑二进制时须把 `src/datasystem/worker` 放进 `LD_LIBRARY_PATH`（见 `lib_ctest_env.sh`），否则 `Subprocess is abnormal`。

## UT（14× ds_ut_nds）

| Suite | 约 cases |
|-------|----------|
| AlignmentGateTest | 3 |
| MockIpcHbmBackendTest | 3 |
| FakeNdsSpillReaderTest | 2 |
| HbmMappingTableTest | 3 |
| NdsDirectPathTest | 3 |

```bash
bash rfc/.../scripts/run_nds_ut_remote.sh   # sync + build + UT
bash rfc/.../scripts/run_ut_only_xqyun.sh   # 已编译仅 UT
```

## 尚未实现

- `NdsBinmockFlow*` e2e（Task 6）— `run_binmock_flow_st.sh` 会失败属预期
- L2 Stage A/B — 人工节点 + `run_stage_*.sh`

## 与全量 ST 的区别

本 RFC 验收 = 上表聚焦集。全量 hetero / device ST 不作为合入门禁。
