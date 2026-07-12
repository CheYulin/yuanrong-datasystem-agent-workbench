# Tech Brief: CANN IPC NPU HBM Shared Memory & Registration

**Date**: 2026-07-12  
**Status**: Research note（fact vs inference 已标注）  
**RFC**: [design.md](./design.md)

> Subagent 额度不足时由主会话直接深挖 CANN 文档 + Mooncake 源码整理。

## 1. What IPC shared HBM is

CANN AscendCL 提供 **Device 内存跨进程 IPC**：Owner 进程对已 `aclrtMalloc` 的 Device/HBM 指针 Export key；Peer 进程 `ImportByKey` 得到**本进程可用的 Device VA**，两侧映射同一块物理 HBM。

与 VMM ShareableHandle（`aclrtMemExportToShareableHandleV2`）是**另一条**共享栈；本 RFC Phase-1 **只采用 IPC**。

## 2. Canonical API sequence（FACT — CANN 文档）

文档流程（A = Owner/Client，B = Peer/Worker）：

**A (Client)**

1. `aclrtSetDevice(devId)`
2. `aclrtMalloc(&ptr, size, policy)`（跨 Device 时推理系列可能要 `*_P2P` policy）
3. `aclrtIpcMemGetExportKey(ptr, size, key, 65, flag)`  
   - `len` **固定 65**  
   - 文档默认 `flag=0`；Mooncake IPC 路径曾用 `ACL_RT_IPC_MEM_EXPORT_FLAG_DISABLE_PID_VALIDATION`（见下）
4. 获取 B 的 tgids：`aclrtDeviceGetBareTgid`（推荐，适配物理机/虚拟机）
5. `aclrtIpcMemSetImportPid(key, workerTgid)` 白名单
6. 经 RPC 把 `key[65]` + meta 发给 Worker  
7. **在 Peer 仍使用期间不得 `aclrtFree(ptr)`**（文档强调 Import 前/使用中内存必须存在）

**B (Worker)**

1. `aclrtSetDevice(devId)`（通常与数据所在 Device 一致；跨 Device 见约束）
2. `aclrtIpcMemImportByKey(&localVa, key, flag)` → 得到 **Worker 进程内** Device VA
3. 用 `localVa` 做后续访问（本 RFC：交给 xds 作 NDS 写目标）
4. 用完：`aclrtIpcMemClose(localVa 或 key — 以所用 CANN 版本签名为准)`  
5. Owner 侧再 Unregister / Close / Free

官方入口示例：

- Export: https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/83RC1alpha002/API/appdevgapi/aclcppdevg_03_1934.html  
- Import: https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/850alpha001/API/appdevgapi/aclcppdevg_03_1936.html  

CANN **8.5** 文档集已包含同名 Import API（与环境矩阵 CANN8.5 对齐）。

## 3. Constraints（FACT）

| 项 | 约束 |
|----|------|
| Key 长度 | 65 bytes |
| 内存类型 | Device 侧；同 Device 两进程约束少 |
| 跨 Device | 需 `aclrtDeviceEnablePeerAccess`；部分产品 malloc 需 P2P policy |
| 跨 Device memcpy | 文档限制较多（不可自动推断 H2D/D2H 等）— **同 Device 部署更安全** |
| 生命周期 | Import 前 Owner 不得释放；Close 顺序文档要求 Peer 先 Close |
| 产品支持 | A2/A3 系列 √；部分小形态 x（以目标机型表为准） |

## 4. Mooncake reference（FACT — 源码）

文件：`mooncake-transfer-engine/.../ubshmem_transport/ubshmem_transport.cpp`

| 模式 | 行为 |
|------|------|
| `MC_USE_UBSHMEM_IPC` | `aclrtIpcMemGetExportKey` → 序列化 65B key；对端 `aclrtIpcMemImportByKey`（可带 `ENABLE_PEER_ACCESS`） |
| 默认 | VMM `ExportToShareableHandleV2` / `ImportFromShareableHandleV2` + `MapMem` |

要点：

- Mooncake 传输侧用共享内存做 **D2D memcpy 源/宿**，不是 NVMe P2P。
- Export 可用 `DISABLE_PID_VALIDATION`（方便 bench）；**Datasystem 生产应走 SetImportPid**。
- Fabric / `ascend_allocator` / `shm_helper` 是 Host VMM 路径，与 Phase-1 IPC HBM **分流**。

## 5. Datasystem Register design（对齐已决产品语义）

### 5.1 两条业务路径，一套 IPC

| 路径 | Client 行为 | Worker | 对用户 |
|------|-------------|--------|--------|
| Register（零拷贝） | 用户 data VA → Export → Register RPC | Import → NDS 写 `localVa` | 直接用注册 Buffer |
| 未 Register | SDK `aclrtMalloc` **专用 comm** → **同一套** Export/Register/Import | Import → NDS 写 comm 的 `localVa` | SDK **拷到用户 data**；comm 不暴露 |

### 5.2 Recommended Register RPC fields（提案）

```text
RegisterHbmBufferReq:
  client_id / session_id
  device_idx          # logic device
  ipc_key[65]         # bytes / fixed string
  size                # bytes
  role                # DATA | COMM (internal)
  owner_tgid          # optional audit; Worker 侧 Import 不依赖此做 VA
  alignment_hint      # e.g. 512 / 2MiB
  flags

RegisterHbmBufferRsp:
  mapping_id
  status
```

Worker 侧表：`mapping_id → {client_id, localVa, size, role, device_idx, refcount}`。

### 5.3 Interaction with xds（INFERENCE → 待环境验证）

xds 用户态 `file_p2p_api.c` 写死：

```c
read->desc.hostpid = getpid();  // 调用 read_file_batch 的进程
read->desc.addr = param->addr;  // 该进程语境下的 VA
```

内核用 `(hostpid, devid, vfid, addr)` 调 `devmm_get_mem_pa_list` 解析 PA。

因此：

1. **必须由 Worker 调用 xds**（或与 Worker 同 pid 的代理），且传入的 `addr` 必须是 **ImportByKey 得到的 Worker 本地 VA**。  
2. **不要**把 Client 原始 VA + Client pid 塞进 Worker 进程的 ioctl（除非驱动明确支持代查，公开 xds **未**体现）。  
3. 同 Device + Import 成功后，Worker `getpid()` + imported VA 应能解析 PA — **需在 HDK25.5/CANN8.5 实机验证**。

## 6. Risks / unknowns

| ID | 项 | 备注 |
|----|----|------|
| R1 | Import VA 是否可被 NVMe P2P DMA 写入 | 依赖 devmm pin + NVMe SGL；需联调 |
| R2 | 跨 Device Client/Worker | 尽量禁止 Phase-1；强制同 `device_idx` |
| R3 | Unregister 与 in-flight NDS | 必须引用计数 + drain 完成 |
| R4 | 专用 comm 拷贝方向 | D2D async memcpy；注意跨 Device 文档限制 |
| R5 | Mooncake disable-pid flag | 生产禁用 |

## 7. Links

- CANN IPC Export / Import（8.3 / 8.5）见上文  
- Mooncake ubshmem: https://github.com/kvcache-ai/Mooncake/blob/main/mooncake-transfer-engine/src/transport/ascend_transport/ubshmem_transport/ubshmem_transport.cpp  
- RFC design: [design.md](./design.md) §5–§6  
- xds brief: [tech-brief-xds-nds.md](./tech-brief-xds-nds.md)
