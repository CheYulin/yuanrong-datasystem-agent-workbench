# Gate 0 / Track① 用例复现手册

**节点**：`xqyun-32c32g`（隔离树首选）；**xqyun SSH 不可用时**用 `tiantiyun-80c128g` + `scripts/verify_track1_tiantiyun.sh`（build：`/home/cache/build-ssd-hbm-direct`）  
**原则**：只跑与本 RFC 相关的用例，**不**跑全量 `ds_device_llt` / `ctest`。

---

## 已达成效果（截至 2026-07-13 凌晨）

| 层级 | 状态 | 效果 |
|------|------|------|
| **隔离编译** | 进行中/已出 llt | xqyun 独立 worktree + build 能编出 `ds_device_llt` |
| **Task 1 代码** | 已落地 | `AlignmentGatePass()` — 4K 默认对齐门禁，不对齐返回 false |
| **Task 2 代码** | 已落地 | `MockIpcHbmBackend` — Export→Import 同指针，模拟 CANN IPC |
| **Task 3 代码** | 已落地 | `FakeNdsSpillReader` — pread spill 文件 → memcpy 到 imported VA |
| **Task 4a 代码** | 已落地 | `HbmMappingTable` + `NdsDirectPath` eligibility（Register/inflight UT） |
| **Task 4b–6** | 未做 | Register RPC、Get 旁路、`NdsBinmockFlow` ST 尚未串通 |
| **端到端 SSD→HBM** | 未达成 | 尚无 Worker Get 旁路；binmock 全链路 ST 待 Task 6 |

> **注意**：此前 Gate 0 用 `HeteroD2H*` 误跑了 `HeteroD2HTestEvcit` / `HeteroD2HThroughTcpTest`（7 FAIL）。已收窄为 **5 个核心 binmock D2H 用例**。

---

## 路径约定

| 角色 | 路径 |
|------|------|
| 本地 worktree | `yuanrong-datasystem/.worktrees/ssd-hbm-direct` |
| xqyun 源码 | `/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct` |
| xqyun build | `/root/workspace/build-ssd-hbm-direct` |
| 日志 | `/root/workspace/nds-ssd-hbm-meta/` |

---

## 1. Gate 0 — binmock 基线（无 NPU）

**目的**：确认隔离树 + `AclDeviceManagerMock` 下 MSetD2H/MGetH2D 仍可用（本 RFC 的地基）。

**跑哪些用例**（仅此 5 个）：

```
HeteroD2HTest.Perf
HeteroD2HTest.TestNoExist
HeteroD2HTest.TestAllExist
HeteroD2HTest.TestPartExist
HeteroD2HTest.TestMSetD2HMsgWithInvalidDeviceId
```

**不跑**：`HeteroD2HTestEvcit.*`（spill 专项）、`HeteroD2HThroughTcpTest.*`（双 worker TCP）。

### 一键（推荐）

```bash
# 全量：sync + 编 + 聚焦 ST
bash rfc/2026-07-12-ssd-hbm-direct/scripts/prepare_build_and_st_xqyun.sh

# 仅 ST（build 已有）
bash rfc/2026-07-12-ssd-hbm-direct/scripts/prepare_build_and_st_xqyun.sh --skip-sync --skip-build
```

### 手动（xqyun 上）— 必须用 ctest 带上 CMake 的 LD_LIBRARY_PATH

```bash
export GTEST_FILTER='HeteroD2HTest.Perf:HeteroD2HTest.TestNoExist:...'
ctest --test-dir /root/workspace/build-ssd-hbm-direct --output-on-failure -R '^ds_device_llt$' -j 1
```

**不要**只设 `LD_LIBRARY_PATH=tests/st` 直接 `./ds_device_llt` — worker 二进制在 `src/datasystem/worker/`，缺库会导致 `Subprocess is abnormal`。

**binmock 机制**：`hetero_d2h_test.cpp` 在 `ASCEND_HOME_PATH` 未设置时，`BINEXPECT_CALL(AclDeviceManager::Instance, ...)` 返回 `AclDeviceManagerMock`，H2D/D2H 走 host memcpy。

**日志**：`/root/workspace/nds-ssd-hbm-meta/latest_gate0_st.log`

---

## 2. Track① UT — Task 1–4a 单元测试

**目的**：验证对齐门禁、Mock IPC、Fake NDS、HbmMappingTable、NdsDirectPath eligibility（不启集群）。

**跑哪些用例**（`ds_ut_nds`，共 **14** 个）：

| Suite | Case | 验证点 |
|-------|------|--------|
| `AlignmentGateTest` | `Default4kRejects512AlignedOnly` | 4K 通过；512 仅在 align=512 时通过 |
| `AlignmentGateTest` | `OffByOneFails` | offset/len/addr ±1 拒绝 |
| `AlignmentGateTest` | `ZeroLengthOrAlignRejected` | len=0 或 align=0 拒绝 |
| `MockIpcHbmBackendTest` | `ExportImportSamePointer` | Export→AllowImport→Import 同指针 |
| `MockIpcHbmBackendTest` | `CloseRemovesExport` | Close 后 Import 失败 |
| `MockIpcHbmBackendTest` | `ReExportSameVaReturnsSameHandle` | 同 VA 重复 Export 同 handle |
| `FakeNdsSpillReaderTest` | `ReadToHbmCopiesFileBytes` | 临时文件 pattern → dest buffer |
| `FakeNdsSpillReaderTest` | `ReadWithFileOffsetAndDestOff` | file offset + destOff 正确 |
| `HbmMappingTableTest` | `RegisterAndLookupDataMapping` | Register DATA mapping + Lookup |
| `HbmMappingTableTest` | `UnregisterRemovesMapping` | Unregister 后 Lookup 失败 |
| `HbmMappingTableTest` | `UnregisterRejectedWhileInflight` | inflight>0 时 Unregister → `K_TRY_AGAIN` |
| `NdsDirectPathTest` | `SpillBufferPresentFallsBack` | SPILL_BUFFER 存在 → DRAM fallback |
| `NdsDirectPathTest` | `MisalignedOffsetFallsBack` | 不对齐 offset → fallback |
| `NdsDirectPathTest` | `AlignedWithMappingSelectsDirect` | 对齐 + mapping → DIRECT |

### 一键（xqyun 或 tiantiyun fallback）

```bash
# xqyun 隔离（首选）
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh

# xqyun SSH 不可用时 — Gate0 + UT 一次跑完
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_tiantiyun.sh

# 仅 UT
bash rfc/2026-07-12-ssd-hbm-direct/scripts/run_nds_ut_remote.sh
# tiantiyun: REMOTE=tiantiyun-80c128g BUILD=/home/cache/build-ssd-hbm-direct bash ...
```

### 手动 filter

```bash
export GTEST_FILTER='AlignmentGateTest.*:MockIpcHbmBackendTest.*:FakeNdsSpillReaderTest.*:HbmMappingTableTest.*:NdsDirectPathTest.*'
ctest --test-dir /root/workspace/build-ssd-hbm-direct --output-on-failure -R '^ds_ut_nds$' -j 1
```

---

## 3. 目标端到端 ST（Task 6，尚未实现）

**目的**：Register → spill → Get 旁路 → FakeNds 填 HBM → Client D2H 校验 pattern。

**将来跑的 filter**（仅 2 组，不扫全 suite）：

```bash
./ds_device_llt --gtest_filter='NdsBinmockFlow*:HeteroD2HTest.TestAllExist'
```

脚本：`scripts/run_binmock_flow_st.sh`（xqyun 默认隔离路径）

**当前**：`NdsBinmockFlow*` 用例尚不存在；脚本会失败。Gate 0 通过后可用 `ALLOW_HETERO_ONLY=1` 仅回归 1 个 Hetero case 作 smoke。

---

## 4. 夜间一键（Gate0 + UT，聚焦）

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/overnight_iterate.sh
```

顺序：等 llt → **5 个 HeteroD2HTest** → sync → 增量编 → **ds_ut_nds 8 cases**。

---

## 5. 查看进度 / 证据

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/check_cmake_puncture_xqyun.sh
tail -30 /root/workspace/nds-ssd-hbm-meta/latest_gate0_st.log   # xqyun
cat rfc/2026-07-12-ssd-hbm-direct/results.md                     # 本地时间线
```

---

## 6. 与全量 ST 的区别

| 命令 | 范围 | 本 RFC 是否使用 |
|------|------|----------------|
| `ctest` 全扫 | 所有 ST | **否** |
| `ds_device_llt` 无 filter | 全部 device ST | **否** |
| `HeteroD2H*` | 含 Evict/Tcp 变体 | **否**（已修正） |
| Gate0 5 cases | binmock D2H 基线 | **是** |
| `ds_ut_nds` 8 cases | Track① 接口 UT | **是** |
| `NdsBinmockFlow*` | 本特性 e2e | **待 Task 6** |
