# SSD→HBM Direct (NDS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Process:** Superpowers `writing-plans` · Spec: [README](./README.md) / [decisions.md](./decisions.md) / [work-breakdown.md](./work-breakdown.md) / [env-validation.md](./env-validation.md)

**Goal:** 在无 NPU 的 L0/L1 上用 **binmock + FakeNds** 把 Register → Get(spilled) → HBM 交付整条流程串通；L2 真机由人工跑 Agent 准备好的脚本完成 ② IPC / ③ SSD→HBM 穿刺。

**Architecture:** 业务路径只依赖两个可注入接口：`IpcHbmBackend`（②）与 `NdsSpillReader`（③）。默认测试构建通过 binmock 劫持 `AclDeviceManager::Instance()`（复用 `AclDeviceManagerMock` 的 H2D/D2H）并注入 `MockIpcHbmBackend` / `FakeNdsSpillReader`。真机换 backend，不改 Get 分支语义。

**Tech Stack:** C++17 datasystem · gtest · binmock · CANN IPC（L2）· xds `.so/.ko`（L2）· workbench harness scripts · **xqyun-32c32g**（隔离 verify）

## Global Constraints

- Phase-1 **仅本机** `IsSpilled` 已落盘；禁止跨机 SSD→HBM；**不改** spilled→RH2D 禁令。
- 统一 KV `Get`/`MGet` + `RegisterHbmBuffer`；未 Register → 专用 comm + 拷到 user，不暴露 comm。
- `nds_align_bytes` 默认 **4096**；对齐失败 → DRAM fallback，不进 xds。
- Phase-1 **不改** spill 写 pad / HBM→SSD 写路径。
- 无卡证据标 `hardware-pending`；② 不绿不上 ③。
- C/C++ 改完跑 `clang-format -i`；**verify 默认 xqyun-32c32g 隔离树**（不碰 tiantiyun 日常任务）。
- 尽量用 **binmock** 串流程；IPC/xds 真语义只在 L2 宣称绿。

## Workspace / verify hosts

| 项 | 值 |
|----|-----|
| Local worktree | `yuanrong-datasystem/.worktrees/ssd-hbm-direct` |
| Branch | `feat/ssd-hbm-direct` ← `origin/master` |
| xqyun 隔离源码 | `/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct` |
| xqyun 隔离 build | `/root/workspace/build-ssd-hbm-direct` |
| 现成 ST 二进制（Gate 0） | `/root/workspace/git-repos/yuanrong-datasystem/build/tests/st/ds_device_llt` |

---

## Gate 0 — 隔离环境构建 + 现有 ST（开干前置）

**对齐 skills：** `ds-build` / `ds-dev` + `run_worktree_verify_remote.sh` 流程；节点换 **xqyun**（见 [scripts/BUILD_VERIFY.md](./scripts/BUILD_VERIFY.md)）。

**禁止**用其他工程目录的旧 `ds_device_llt`。

- [x] Worktree `feat/ssd-hbm-direct` ← `origin/master`
- [x] 三方件：`/root/.cache/yuanrong-datasystem-third-party`（`nodes.yaml` xqyun；openssl 缓存命中 ~3s）
- [x] 隔离源码 / build：`...-ssd-hbm-direct` / `build-ssd-hbm-direct`
- [ ] 隔离编出 `ds_device_llt`（进行中）
- [ ] 对该二进制：`HeteroD2H*` PASS

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/prepare_build_and_st_xqyun.sh
```

---

## File map（拟新增 / 修改）

| Path | Responsibility |
|------|----------------|
| `src/datasystem/common/device/hbm_ipc/ipc_hbm_backend.h` | `IpcHbmBackend` 抽象 |
| `src/datasystem/common/device/hbm_ipc/mock_ipc_hbm_backend.{h,cpp}` | 同进程假共享（host `malloc` + 表）；binmock/UT 用 |
| `src/datasystem/common/device/hbm_ipc/cann_ipc_hbm_backend.{h,cpp}` | 真 CANN IPC（`USE_NPU` 门控） |
| `src/datasystem/common/device/nds/nds_spill_reader.h` | `NdsSpillReader` 抽象 |
| `src/datasystem/common/device/nds/fake_nds_spill_reader.{h,cpp}` | 读 spill 文件 → `memcpy`/`MemCopyH2D` 到 dest |
| `src/datasystem/common/device/nds/xds_nds_spill_reader.{h,cpp}` | 真 xds `read_file`+`drain_read`（`USE_NPU`+配置） |
| `src/datasystem/common/device/nds/alignment_gate.{h,cpp}` | offset/length/addr 对齐门禁 |
| `src/datasystem/worker/object_cache/hbm_mapping_table.{h,cpp}` | Import 后 mapping 表 |
| `src/datasystem/protos/...` + client/worker RPC | `RegisterHbmBuffer` / `Unregister` |
| `src/datasystem/client/...` | Register API；EnsureCommBuffer |
| `worker_oc_service_get_impl.cpp` | spilled 旁路 → NDS；fallback 旧路径 |
| `tests/st/device/nds_binmock_flow_test.cpp` | **主串通 ST**：binmock ACL + MockIpc + FakeNds |
| `tests/ut/.../alignment_gate_test.cpp` | 对齐矩阵 UT |
| `rfc/.../scripts/*.sh` | L1/L2 人工可跑脚本（本目录） |

---

## Environment roles

| Who | Where | Does |
|-----|-------|------|
| Agent | L0 改代码；L1 rsync/编译/`ds_device_llt` 过滤跑 binmock ST | 事项① |
| Human | 填 `scripts/env.local.sh`（NPU host、bdev、CANN 路径） | 一次性 |
| Human | SSH L2 跑 `run_stage_a_npu.sh` / `run_stage_b_nds.sh` | 事项②/③ |
| Agent | 根据人工回传日志改代码 / 修脚本 | 闭环 |

脚本目录：`yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/scripts/`

---

## Track ① — binmock 串通（Agent 主路径）

### Task 1: AlignmentGate 纯函数 + UT

**Files:**
- Create: `src/datasystem/common/device/nds/alignment_gate.h`
- Create: `src/datasystem/common/device/nds/alignment_gate.cpp`
- Create: `tests/ut/common/device/nds/alignment_gate_test.cpp`
- Modify: 对应 `CMakeLists.txt` / Bazel 注册（跟仓库现有 UT 模式）

**Interfaces:**
- Produces: `bool AlignmentGatePass(uint64_t fileOff, uint64_t len, uintptr_t hbmAddr, uint32_t alignBytes);` 默认 `alignBytes=4096`

- [ ] **Step 1: Write failing UT**（4K 通过；±1 失败；512 在 `alignBytes=512` 时通过）

```cpp
TEST(AlignmentGateTest, Default4kRejects512AlignedOnly) {
  EXPECT_TRUE(AlignmentGatePass(4096, 4096, 0x1000, 4096));
  EXPECT_FALSE(AlignmentGatePass(512, 512, 0x200, 4096));
  EXPECT_TRUE(AlignmentGatePass(512, 512, 0x200, 512));
}
```

- [ ] **Step 2: Implement gate**（`off%align==0 && len%align==0 && addr%align==0`）
- [ ] **Step 3: Run UT on L1** — Expected: PASS
- [ ] **Step 4: Commit** `test+feat: add NDS AlignmentGate`

---

### Task 2: `IpcHbmBackend` + `MockIpcHbmBackend`

**Files:**
- Create: `src/datasystem/common/device/hbm_ipc/ipc_hbm_backend.h`
- Create: `src/datasystem/common/device/hbm_ipc/mock_ipc_hbm_backend.h`
- Create: `src/datasystem/common/device/hbm_ipc/mock_ipc_hbm_backend.cpp`
- Test: UT 同进程 Export→Import→写 pattern→对端读

**Interfaces:**
- Produces:

```cpp
struct IpcExportHandle { std::string key; /* mock: opaque id */ };
class IpcHbmBackend {
 public:
  virtual Status Export(void *localVa, size_t size, int32_t deviceIdx, IpcExportHandle &out) = 0;
  virtual Status AllowImportPid(const IpcExportHandle &h, pid_t peerPid) = 0;
  virtual Status Import(const IpcExportHandle &h, int32_t deviceIdx, void **localVa, size_t *size) = 0;
  virtual Status Close(void *localVa) = 0;
  virtual ~IpcHbmBackend() = default;
};
```

- Mock 行为：Export 登记 `malloc` 块到全局表；Import 返回 **同一指针**（同机同测进程可串；跨进程 ST 用 shm/`mmap` 命名段 —— Phase-1 ST 先 **同测进程双角色** 或 fork 后继承表；跨真进程留 L2）。

- [ ] **Step 1: Failing UT** Export/Import/Close
- [ ] **Step 2: Mock 实现**
- [ ] **Step 3: L1 UT PASS**
- [ ] **Step 4: Commit** `feat: add MockIpcHbmBackend`

---

### Task 3: `NdsSpillReader` + `FakeNdsSpillReader`

**Files:**
- Create: `src/datasystem/common/device/nds/nds_spill_reader.h`
- Create: `src/datasystem/common/device/nds/fake_nds_spill_reader.{h,cpp}`
- Test: 临时文件写 pattern → Fake 读到 dest buffer

**Interfaces:**
- Produces:

```cpp
struct SpillFileLoc { std::string path; uint64_t offset; uint64_t size; };
class NdsSpillReader {
 public:
  virtual Status ReadToHbm(const SpillFileLoc &loc, uint64_t readOff, uint64_t readSize,
                           void *importedVa, uint64_t destOff, int32_t deviceIdx) = 0;
  virtual ~NdsSpillReader() = default;
};
```

- Fake：`pread` 文件 → 若 `importedVa` 为 mock「device」指针则 `memcpy`；可选走 `AclDeviceManager::Instance()->MemCopyH2D`（binmock 下即 host copy）。

- [ ] **Step 1–4:** TDD + commit `feat: add FakeNdsSpillReader`

---

### Task 4: Worker `HbmMappingTable` + Register/Unregister RPC（可先内测桩）

**Files:**
- Create: `src/datasystem/worker/object_cache/hbm_mapping_table.{h,cpp}`
- Modify: worker OC service + proto（新增 RPC 字段/方法名与现有 OC RPC 风格一致）
- Modify: client `KVClient`/`HeteroClient` 或 OC client：`RegisterHbmBuffer` / `UnregisterHbmBuffer`

**Interfaces:**
- Mapping: `{ mappingId, clientId, deviceIdx, size, localVa, role=DATA|COMM, inflight }`
- Unregister 时 `inflight>0` → reject

- [ ] **Step 1: UT** refcount / inflight reject（不启集群）
- [ ] **Step 2: 最小 RPC** Register 调 `IpcHbmBackend::Import` 填表
- [ ] **Step 3: Commit** `feat: HbmMappingTable and Register RPC`

---

### Task 5: Get 旁路 Eligibility + FakeNds（核心串通）

**Files:**
- Modify: `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp`（`KeepObjectDataInMemory` 旁路）
- Modify: 注入点（Worker 持有 `shared_ptr<NdsSpillReader>` / `IpcHbmBackend`，测试可替换）
- **不修改** `LoadPayloadAndFillResponse` spilled RH2D 禁令

**Logic (exact):**

```text
if local && IsSpilled && !SpillBufferPresent && mappingReady
   && AlignmentGatePass(...) && ndsReader != nullptr:
     inflight++; NdsReadSpillToHbm(...); inflight--; mark delivered via HBM path
else:
     existing LoadSpilledObjectToMemory
```

- [ ] **Step 1: 表驱动 UT/注入** SpillBuffer → DRAM；不对齐 → fallback；对齐+Fake → direct
- [ ] **Step 2: 实现旁路**
- [ ] **Step 3: L1 相关 UT PASS**
- [ ] **Step 4: Commit** `feat: local spilled Get NDS bypass with fallback`

---

### Task 6: binmock 端到端 ST（事项① Done 判据）

**Files:**
- Create: `tests/st/device/nds_binmock_flow_test.cpp`
- Reuse: `tests/st/device/dev_test_helper.h` → `UseAclMockIfNoDeviceBackend()`
- Reuse: `BINEXPECT_CALL(AclDeviceManager::Instance, ...)` 模式（见 `dev_test_helper.h`）

**Flow to assert:**

```text
1. UseAclMockIfNoDeviceBackend(true)
2. Inject MockIpcHbmBackend + FakeNdsSpillReader into worker (inject point or test setter)
3. Client: mock-malloc "HBM" → Export → RegisterHbmBuffer(workerPid)
4. Put object then force spill to file (enableSpill + fill memory) OR write spill file + meta fixture
5. Get / MGet path → FakeNds fills imported VA
6. Client MemCopyD2H (binmock) → pattern match
7. Unregister OK
8. Metrics/log: direct vs fallback 可区分（至少 LOG 关键字）
```

- [ ] **Step 1: 写 ST skeleton**（可先 DISABLED_ 若 spill fixture 未好）
- [ ] **Step 2: L1** `./ds_device_llt --gtest_filter=NdsBinmockFlow*`
- [ ] **Step 3: 同时回归** `./ds_device_llt --gtest_filter=HeteroD2HTest.*`（确认未破坏现有 mock H2D/D2H）
- [ ] **Step 4: Commit** `test: NDS binmock e2e flow ST`

**人工/Agent 共用脚本：** `scripts/run_binmock_flow_st.sh`（见下）

---

### Task 7: EnsureCommBuffer 未 Register 路径

**Files:** Client SDK EnsureComm → Register(COMM) → Get → D2H/拷到 user → 不暴露 comm 指针

- [ ] ST 扩展：不 Register data，只给 user host/device buffer；断言 API 不返回 comm VA
- [ ] Commit `feat: auto comm buffer path with copy-out`

---

### Task 8: Metrics / Perf / Trace 观测落地

**Files:**
- Modify: `src/datasystem/common/perf/perf_point.def`（`WORKER_NDS_*`、`HBM_IPC_*`）
- Modify: `src/datasystem/common/log/access_point.def`（Register / NDS access keys）
- Modify: NDS/IPC 路径埋 `PerfPoint` + AccessRecorder + `nds_*` 日志（带 TraceID）
- Spec: [observability.md](./observability.md)
- Script: `scripts/run_obs_smoke.sh`

- [ ] **Step 1:** 按 observability.md §3 增加 PerfKey / AccessRecorder def（并注册到 GetPerfKeyDefines 若需要）
- [ ] **Step 2:** Fake/真路径共用埋点助手，保证 binmock ST 也能涨 count
- [ ] **Step 3:** L1 跑 `run_binmock_flow_st.sh` 后 `bash scripts/run_obs_smoke.sh <log>` 能看到 `WORKER_NDS_` 或 `nds_` 关键字
- [ ] **Step 4:** Commit `feat: NDS observability PerfKey AccessRecorder and trace logs`

---

## Track ② — NPU IPC 穿刺（人工 + Agent 脚本）

### Task 9: `CannIpcHbmBackend`（代码可由 Agent 写，验证人工）

**Files:** `cann_ipc_hbm_backend.{h,cpp}` behind `USE_NPU`

- 真 `aclrtIpcMemGetExportKey` / `SetImportPid` / `ImportByKey` / Close
- [ ] 编译进 `BUILD_HETERO_NPU` 构建
- [ ] **不在 L1 宣称 Pass**

### Task 10: 人工跑 Stage A

- [ ] Human: 填写 `scripts/env.local.sh`（从 `env.local.sh.example` 复制）
- [ ] Human: 在 L2 执行 `scripts/run_stage_a_npu.sh`
- [ ] 验收：双向 pattern 一致；把日志贴回 Agent
- [ ] V1 异常再升级（Import VA 后续给 ③ 用）

---

## Track ③ — SSD→HBM 穿刺（人工 + Agent 脚本）

### Task 11: `XdsNdsSpillReader`

- `read_file` + `drain_read`；`addr=importedVa`；每请求 `p2p_fd`；`bdev` 来自 flags/env
- [ ] Agent 实现；L1 只链接 stub/`NOT_SUPPORTED`

### Task 12: 人工跑 Stage B（依赖 ② 绿）

- [ ] Human: `scripts/run_stage_b_nds.sh`
- [ ] B1 文件→HBM；B2 Get e2e；负例不对齐 fallback
- [ ] Perf 相对 DRAM reload 可选

---

## Scripts inventory（Agent 准备，人工执行）

| Script | Runner | Purpose |
|--------|--------|---------|
| `env.local.sh.example` | Human copy | NPU_HOST、BDEV、CANN、REPO、BUILD_DIR |
| `check_env_device.sh` | Human on L2 | `npu-smi` / `/dev/davinci*` / `/dev/p2p_device` |
| `run_binmock_flow_st.sh` | Agent or Human on L1 | 编译（可选）+ `ds_device_llt` NdsBinmock + HeteroD2H 回归 |
| `run_stage_a_npu.sh` | Human on L2 | Stage A gtest/binary filter |
| `run_stage_b_nds.sh` | Human on L2 | Stage B + bdev |
| `run_obs_smoke.sh` | Agent/Human | 从 glog 抽 Perf/nds_/fallback（见 [observability.md](../observability.md)） |
| `HUMAN_CHECKLIST.md` | Human | 逐步勾选 |

---

## Suggested commit / milestone order

1. Tasks 1–3（纯库 + Fake/Mock）  
2. Tasks 4–6（RPC + Get + **binmock ST 绿**）← 事项①可演示  
3. Task 7–8（comm + metrics）  
4. Task 9–10（人工 ②）  
5. Task 11–12（人工 ③）

---

## Self-review (writing-plans)

| Spec item | Task |
|-----------|------|
| 三事项拆分 + mock 对接 | Track ①/②/③ + File map |
| binmock 串 H2D/D2H + 新流程 | Task 6 |
| 环境分层 / 人工 L2 | Environment roles + Scripts |
| Alignment 默认 4K | Task 1 |
| Register 零拷贝 / 未 Register 拷贝 | Tasks 4, 6, 7 |
| SpillBuffer → DRAM | Task 5 |
| 不改 RH2D 禁令 | Task 5 明确 |
| XDS 接口约定 | Task 11–12 |
| V1–V4 不阻塞 ① | Track ②/③ 人工 |

无 TBD 占位；真机命令以 scripts 为准，binary 名若落地时微调，只改脚本不改任务语义。
