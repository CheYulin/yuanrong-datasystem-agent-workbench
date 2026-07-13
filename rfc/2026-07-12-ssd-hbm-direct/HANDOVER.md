# SSD→HBM Direct (Track①) — 交接文档

**更新日期**: 2026-07-14  
**交接时点 HEAD**: `ad74e989f`  
**特性分支**: `feat/ssd-hbm-direct` ← `main/master`（openeuler）  
**跟踪**: [MR !1312](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1312) · [Issue #12](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/12)（`Fixes #12`，1 PR ↔ 1 issue）

---

## 1. 一句话目标

本机 **已落盘 spill** 对象在 Client 已 `RegisterHbmBuffer` 时，Get 走 **NDS SSD→HBM 直通**（L0/L1 用 FakeNds + MockIpc）；不满足条件则 **DRAM fallback**（现有 `LoadSpilledObjectToMemory`）。Phase-1 **不改** spilled→RH2D 禁令、**不做**跨机 SSD→HBM。

---

## 2. 当前进度总览

| 阶段 | 交付物 | 状态 | 证据 |
|------|--------|------|------|
| Task 1 | `AlignmentGate` + UT | ✅ | `ds_ut_nds` |
| Task 2 | `MockIpcHbmBackend` + UT | ✅ | 同上 |
| Task 3 | `FakeNdsSpillReader` + UT | ✅ | 同上 |
| Task 4a | `HbmMappingTable` + `NdsDirectPath` | ✅ | 同上 |
| Gate0 | 5× `HeteroD2HTest` binmock 基线 | ✅ xqyun | `verify_track1_xqyun.sh` |
| E2E-单进程 | `NdsBinmockFlowTest` | ✅ xqyun | `run_binmock_flow_st.sh` |
| Task 4b | `RegisterHbmBuffer` / `UnregisterHbmBuffer` RPC + Client API | ✅ 已合入分支 | 代码 + 待集群 ST 绿 |
| Task 5 | Get 旁路 `NdsWorkerRuntime::TryLoadSpilledViaNdsDirect` | ✅ Phase-1（含 DRAM bridge） | 见 §5 |
| Task 6 | 集群 ST `NdsClusterSpillRwTest` | ⏳ **代码已提交，xqyun 未跑通** | SSH 中断 |
| Task 7 | 未 Register → comm buffer + 拷出 | ⏳ 未做 | WBS |
| Task 8 | PerfKey / 观测 | ⏳ 未做 | WBS |
| L2-A/B | 真 CANN IPC / 真 xds | ⏳ 人工 | `run_stage_a_npu.sh` 等 |

**结论**：L0/L1 **可编译、可测、可演示** 的读写主路径已接通；**集群 E2E ST 待 xqyun 验证**；MR 可继续迭代或拆后续 PR（需与 Issue 策略对齐）。

---

## 3. 仓库与工作区

| 项 | 路径 |
|----|------|
| 本地 worktree | `yuanrong-datasystem/.worktrees/ssd-hbm-direct` |
| Workbench RFC | `yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/` |
| 远程 fork | `git@gitcode.com:yche-huawei/yuanrong-datasystem.git` |
| 上游基线 | `git@gitcode.com:openeuler/yuanrong-datasystem.git` → `main/master` |
| xqyun 隔离源码 | `/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct` |
| xqyun 隔离 build | `/root/workspace/build-ssd-hbm-direct` |
| 三方件缓存 | `/root/.cache/yuanrong-datasystem-third-party` |
| 验证日志目录 | `/root/workspace/nds-ssd-hbm-meta/` |

**注意**：特性分支曾 **behind `main/master` 十余个提交**；合入前需再 rebase/cherry-pick 到最新上游（历史用过 `scripts/archive/fix_baseline_cherry_pick.sh`，勿把 fork 独有 URMA 等提交叠进去）。

---

## 4. 架构与数据流（As-Is 实现）

### 4.1 可注入接口（业务只依赖这两个）

```text
IpcHbmBackend     — Export / Import / Close（L0: MockIpc；L2: CannIpc）
NdsSpillReader    — ReadToHbm（L0: FakeNds pread+memcpy；L2: xds）
```

### 4.2 集群端到端读写（已实现）

```text
写路径:
  Client RegisterHbmBuffer(file-backed VA, 4K)
    → RPC → Worker NdsWorkerRuntime Import → HbmMappingTable
  Client MSetD2H(NONE_L2_CACHE_EVICT)
    → Worker 驻留 → 内存压力 → spill 落盘

读路径:
  Client MGetH2D → Worker Get → KeepObjectDataInMemory
    → LoadSpilledObjectToMemory
    → TryLoadSpilledViaNdsDirect (有 clientId + mapping + 对齐)
    → FakeNds 读 spill 文件 → imported HBM VA
    → Phase-1 bridge: memcpy HBM → DRAM shm（兼容现有 MGetH2D）
    → Client HostDataCopy2Device → 校验 pattern
```

### 4.3 Phase-1 有意妥协

- **DRAM bridge**：NDS 写入 imported VA 后，仍分配 DRAM shm 并 `memcpy`，以便现有 Get/MGetH2D 响应路径不变。真零拷贝需后续改 Get 响应语义（参考 RH2D 空 shm 路径）。
- **跨进程 Mock IPC**：`DS_NDS_MOCK_CROSS_PROC=1` 时 Export 使用 `mock-file:<path>`；Client 用 `CreateMockIpcFileRegion` 分配共享文件映射。

### 4.4 关键源码地图

| 模块 | 路径 |
|------|------|
| 对齐门禁 | `src/datasystem/common/device/nds/alignment_gate.*` |
| Fake NDS | `src/datasystem/common/device/nds/fake_nds_spill_reader.*` |
| 直通判定/加载 | `src/datasystem/common/device/nds/nds_spill_direct_path.*` |
| Mock IPC | `src/datasystem/common/device/hbm_ipc/mock_ipc_hbm_backend.*` |
| 跨进程文件区 | `src/datasystem/common/device/hbm_ipc/mock_ipc_file_region.*` |
| Mapping 表 | `src/datasystem/worker/object_cache/hbm_mapping_table.*` |
| Worker 运行时 | `src/datasystem/worker/object_cache/nds_worker_runtime.*` |
| Get 挂钩 | `src/datasystem/worker/object_cache/obj_cache_shm_unit.cpp`（`LoadSpilledObjectToMemory`） |
| clientId 传递 | `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp`（`PreProcessGetObject`） |
| Proto/RPC | `src/datasystem/protos/object_posix.proto`（`RegisterHbmBuffer` / `UnregisterHbmBuffer`） |
| Client API | `object_client_impl.*`、`hetero_client.*`、`client_worker_*_api.*` |
| UT | `tests/ut/common/device/nds/*` → 目标 `ds_ut_nds` |
| ST 单进程 | `tests/st/device/nds_binmock_flow_test.cpp` |
| ST 集群 | `tests/st/device/nds_cluster_spill_rw_test.cpp` |

---

## 5. 验证命令（接手后第一件事）

默认节点：**xqyun-32c32g**（隔离树，勿用日常 tiantiyun 任务抢占）。

```bash
# 1) Gate0 + UT（回归基线）
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh

# 2) 单进程 E2E + Gate0 片段回归
bash rfc/2026-07-12-ssd-hbm-direct/scripts/run_binmock_flow_st.sh

# 3) 集群读写 E2E（当前阻塞项）
bash rfc/2026-07-12-ssd-hbm-direct/scripts/run_cluster_spill_rw_xqyun.sh
```

**GTest filter**（见 `scripts/gtest_filters.sh`）：

| 套件 | Filter |
|------|--------|
| Gate0 | 5× `HeteroD2HTest.*`（排除 Evcit/TCP） |
| UT | `AlignmentGateTest.*` … `NdsDirectPathTest.*`（14 cases） |
| 单进程 E2E | `NdsBinmockFlowTest.*` |
| 集群 E2E | `NdsClusterSpillRwTest.*` |

**已验证快照**（截至交接）：Gate0 ✅、UT 14 ✅、单进程 E2E ✅；集群 E2E ⏳。

---

## 6. 提交历史（语义节点）

| SHA（短） | 说明 |
|-----------|------|
| `9dc0bf76c` | Track① 可注入接口骨架 |
| `ffc7e68a3` | mapping 表 + 聚焦 UT |
| `8a148be1a` | cherry-pick CMake 冲突修复 |
| `7aab2bff1` | 单进程 `NdsBinmockFlowTest` |
| `ad74e989f` | **集群 E2E**：Register RPC + Get 旁路 + `NdsClusterSpillRwTest` |

---

## 7. 已知问题与风险

| # | 问题 | 影响 | 建议 |
|---|------|------|------|
| R1 | xqyun SSH `Connection closed` | 集群 ST 未实跑 | 恢复后先跑 `run_cluster_spill_rw_xqyun.sh`，更新 `results.md` |
| R2 | 分支 behind `main/master` | MR 冲突/基线漂移 | 合入前 rebase；仅 cherry-pick NDS 相关提交 |
| R3 | Phase-1 DRAM bridge | 非最终零拷贝语义 | 产品验收需区分「路径正确」vs「性能达标」 |
| R4 | `clientId` 未覆盖全部 `ReadObjectKV` 构造点 | 部分 Get 路径仍 DRAM fallback | 集群 ST 主路径已接；扩展需审计 get_impl/batch_get |
| R5 | teardown mock 告警 | `AclDeviceManager::Shutdown` gmock 未调用 | 已知噪声，gtest RC=0 |
| R6 | Issue/MR 文案滞后 | `issue-rfc.md` / `README.md` 仍写「4b–6 后续 PR」 | 以本交接文档 + `results.md` 为准；合入前刷新 MR 描述 |

---

## 8. 接手人待办（优先级）

### P0 — 阻塞合入

1. 恢复 xqyun SSH，执行 `run_cluster_spill_rw_xqyun.sh`，确认 `NdsClusterSpillRwTest.SpillWriteThenNdsDirectRead` PASS。
2. 将 `results.md` 集群 E2E 行改为 PASS，记录日志路径与 HEAD。
3. Rebase 到最新 `main/master`，解决冲突后 force-with-lease push，刷新 MR !1312 描述与验证证据。

### P1 — 合入后或同 MR 收尾

4. **Task 7**：未 Register 时 comm buffer 路径 ST（不暴露 comm VA）。
5. **Task 8**：`PerfKey` / AccessRecorder（`nds_direct_hit`、`dram_fallback` 等，见 `decisions.md` D18–D19）。
6. 更新 `issue-rfc.md`、`README.md`、`design-and-story.md` 场景 2 状态为 ✅/⏳ 一致。

### P2 — L2 真机（人工）

7. 填 `scripts/env.local.sh`，跑 `run_stage_a_npu.sh`（CANN IPC）。
8. Stage A 绿后再跑 `run_stage_b_nds.sh`（真 xds）。

---

## 9. 不可违反的约束（决策摘要）

摘自 [decisions.md](./decisions.md)，实现与评审时必须遵守：

- 仅本机已落盘 spill；SpillBuffer 命中 → **禁 NDS**，走 DRAM。
- `AlignmentGate` 默认 **4KiB**；失败不得硬调 xds。
- spilled 对象 **禁止** 走 RH2D（`LoadPayloadAndFillResponse` 不改）。
- 未 Register：专用 comm + 拷到 user data，**API 不返回 comm VA**。
- ② IPC 不绿不上 ③ xds；无卡证据标 `hardware-pending`。

---

## 10. 文档索引

| 文档 | 用途 |
|------|------|
| [design-and-story.md](./design-and-story.md) | Story + 验收矩阵 |
| [work-breakdown.md](./work-breakdown.md) | 三事项 WBS |
| [docs/implementation-plan.md](./docs/implementation-plan.md) | Agent 实施步骤 |
| [docs/flow-analysis.md](./docs/flow-analysis.md) | As-Is / To-Be 挂钩点 |
| [test-walkthrough.md](./test-walkthrough.md) | 逐步复现 |
| [scripts/README.md](./scripts/README.md) | 脚本清单 |
| [results.md](./results.md) | 验证快照（运维更新） |
| [pr-body.gitcode.md](./pr-body.gitcode.md) | MR 正文模板 |

---

## 11. 联系方式与仓库策略

- **GitCode MR**：[!1312](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1312)（目标分支以 MR 页为准，应为 openeuler 上游）
- **Issue 策略**：本特性 datasystem PR 对应 **仅 Issue #12**（`Fixes #12`）；后续 Task 7/8 是否新开 issue 需与维护者确认
- **Workbench**：`yuanrong-datasystem-agent-workbench` `master` 含 RFC 与脚本；datasystem 代码在 fork/worktree

---

*本文档为交接时点快照；源码与 `results.md` 为最终真相源。有冲突时以 xqyun 实测与 `git log feat/ssd-hbm-direct` 为准。*
