# Env Split: AI Host vs NPU / NDS Nodes

**Date**: 2026-07-12  
**Status**: Operating default  
**Why**: Cursor/Agent 所在环境通常 **无 NPU**；CANN IPC（Stage A）与 SSD→HBM（Stage B）都在 **其他节点**。不能等本机有卡再开工。

## 1. 三层环境（对应三事项）

| 层 | 典型机器 | ① 模块改造 | ② IPC 穿刺 | ③ 直通穿刺 |
|----|----------|------------|------------|------------|
| **L0 AI Host** | Cursor / WSL | 编码、mock UT | 契约/stub | FakeNds |
| **L1 Build** | tiantiyun | 编译 + 无卡 UT | — | — |
| **L2 Device** | NPU（±xds） | 换真 backend 联调 | **Stage A** | **Stage B** |

三事项定义见 [work-breakdown.md](./work-breakdown.md)。

## 1.1 现有 ST：无 NPU 能否跑 mock H2D/D2H？（已核实源码）

**可以。** 仓库已有完整路径，不必为「事项①」从零造 device ST。

| 项 | 结论 |
|----|------|
| 二进制 | `ds_device_llt`（`tests/st/CMakeLists.txt`）；**即使 `BUILD_HETERO=off` 也会编**，依赖 mock |
| 触发条件 | `DeviceManagerFactory::ProbePhysicalBackend() == UNKNOWN`（无 `/dev/davinci*`、无可用 GPU）时，`DevTestHelper::UseAclMockIfNoDeviceBackend()` 生效 |
| Mock | `tests/st/device/mock/ascend_device_manager_mock.cpp`：`MallocDeviceMemory`→`malloc`；`MemCopyH2D/D2H/D2D`→host `memcpy`；经 **binmock** 劫持 `AclDeviceManager::Instance()` |
| 覆盖 API | `HeteroClient`/`KVClient` 的 **`MSetD2H` / `MGetH2D`**（如 `hetero_d2h_test.cpp`）：拷贝在 **client 进程**（`HostDataCopy2Device` / `DeviceDataCreate`），worker 只走 host shm，故 mock 足够 |
| 跑法 | 无加速器节点上：`ds_device_llt --gtest_filter='HeteroD2H*' `（或具体 case） |

**做不到 / 不要误会的：**

- Mock **没有** CANN IPC（`IpcMemExport/Import`）→ **事项② Stage A 不能靠现有 mock 宣称绿**。
- **RH2D / 真 HBM / xds** → 仍要 L2。
- 若机器上有 **GPU**（`nvidia-smi` 可见），`ProbePhysicalBackend` 会走真 CUDA，**不会**自动 mock；无卡 CI/tiantiyun 才是 mock 主场。
- `hetero_client_mock_test.cpp` 主要 mock **订阅/RPC 侧**，不是 MemCopy 层。

对 NDS：事项①可复用 `DevTestHelper` + FakeNds；事项②/③仍按 L2 穿刺。

约定：

- **L0/L1 永远不阻塞设计与主路径编码。**
- **L2 只验证「设备语义」**，不在 L2 上从零摸索产品逻辑。
- 默认 build 仍走 L1；device ST 单独脚本/profile，不塞进日常 smoke。

## 2. 工作能开到哪一步（无卡也能做）

```text
L0/L1（无 NPU）                          L2（有 NPU）
─────────────────                        ─────────────
• Register/Unregister RPC + mapping 表    • Stage A：真 IPC Export/Import
• AlignmentGate 纯函数 UT                 • pattern H2D/D2H 双向校验
• Get 分支：eligible → NdsAdapter         • Stage B：真 xds read_file/drain
• NdsAdapter 接口 + FakeNdsAdapter        • V1 Import VA ⊕ P2P
• fallback 原因码 / metrics 桩            • V2 对齐 4K→512
• 编译开关：无 CANN 时跳过 device 符号     • V3/V4 kernel / bdev
• M3 e2e 逻辑用 Fake 灌通（内容一致）
```

**Fake 契约（必须与真实现同签名）**

```text
Status NdsReadSpillToHbm(loc, readOff, readSize, importedVa, destOff, deviceIdx);
```

- `FakeNdsAdapter`：把 spill 文件（或测试 fixture）经 **Host 读 + 可选 H2D** 写入 `importedVa`（L2 有卡时）或仅在 UT 里写 host buffer 模拟。
- 生产路径链接真 xds；UT/L1 默认 Fake 或空实现返回 `NOT_SUPPORTED` → 走 DRAM fallback（行为可测）。

## 3. Agent 默认推进方式（不等人盯着）

| 阶段 | Agent 默认动作 | 需要你提供 / 允许的 |
|------|----------------|---------------------|
| 日常编码 | L0 改代码；需要时 L1 `rsync` + 编译 + 无 NPU UT | 现有 tiantiyun SSH（已有惯例即可） |
| Stage A | 写好 ST 二进制/脚本 + 文档；**在 L2 上跑** | **NPU 节点**：SSH 可达、CANN 可用、Worker/Client 同机或同机双进程 |
| Stage B | Adapter + harness；**在 L2-NDS 上跑** | 同上 + `/dev/p2p_device` + bdev 名 + spill 盘拓扑 |
| 失败升级 | 只把 V1–V4 / 驱动报错甩给你 | 日志 + 节点型号/驱动版本 |

原则：**Agent 先把「可在 L0/L1 证明的部分」做绿；L2 只跑短清单。**

## 4. 推荐 harness（最小接口）

在 workbench 侧后续落地（名称可调）：

```bash
# L1：无 NPU
scripts/.../run_nds_ut_remote.sh          # AlignmentGate + Fake adapter + Get 分支

# L2：有 NPU（Stage A）
scripts/.../run_hbm_ipc_stage_a_remote.sh --host <npu-host>

# L2：有 NPU + xds（Stage B）
scripts/.../run_nds_stage_b_remote.sh --host <npu-host> --bdev /dev/nvmeXnY
```

Agent 需要的只是：

1. **`--host` / SSH alias**（或写进 `profiles.yaml` 的 `npu-stage-a` / `npu-nds`）  
2. 远端工作目录约定（可与 tiantiyun 一样 `/root/workspace/git-repos`）  
3. 一次成功的「环境自检」命令输出（`npu-smi` / `ls /dev/p2p_device` / CANN 版本）

有这三项，Agent 就可以：改代码 → rsync → 远程跑指定脚本 → 读日志改代码，**不必本机有卡**。

## 5. 你这边只需拍板的环境信息（有了就能开工）

不阻塞编码；有空填进本文件或私聊给 Agent：

| 项 | 用途 |
|----|------|
| Stage A 节点 SSH（host/user/跳板） | 真 IPC ST |
| 该节点 CANN/HDK 版本 | 对齐文档假设 |
| Stage B 是否同一节点，还是另一台 | 是否拆两个 profile |
| `bdev_name` 示例 + spill 目录所在盘 | Stage B |
| Agent 是否允许对该节点 `ssh`/`rsync`（密钥已配？） | 无人值守验证 |

在这些信息到位前，Agent **默认只做 L0/L1 + Fake 路径**，不假装 Stage A/B 已绿。

## 6. 与现有里程碑的映射

| 里程碑 | 主要环境 | 完成判据 |
|--------|----------|----------|
| M1 代码 + Fake/分支 UT | L0/L1 | 编译过；无卡 UT 绿 |
| M1 Stage A Pass | **L2** | 真 IPC pattern 一致 |
| M2 Alignment + Adapter | L0/L1 逻辑；L2 B1 | Fake UT +（有卡时）文件→HBM |
| M3 Get e2e | L0/L1 Fake e2e；L2 真 e2e | 两套都要有证据 |
| M4 Perf A/B | **L2** | 相对 DRAM reload |

## 7. 风险边界

- L1 绿 **≠** Stage A 绿；README/PR 必须写清证据来自哪一层。  
- Fake 不得掩盖对齐/IPC 失败；真路径错误码与 Fake 分支原因枚举分开。  
- 无 L2 访问时，PR 标注 `hardware-pending`，不宣称直通可用。
