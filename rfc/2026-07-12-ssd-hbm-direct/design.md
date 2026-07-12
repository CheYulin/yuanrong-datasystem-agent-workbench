# Design: DataSystem × NDS SSD→HBM Direct

**Status**: Draft（brainstorming in progress）  
**Date**: 2026-07-12  
**Related**: [flow-analysis.md](./flow-analysis.md), [references.md](./references.md)

## 1. Why

推理侧 KV 大对象常落在 Worker 本地 spill（SSD）。今天读路径是：

```
SSD ──LoadFromDisk──▶ DRAM ShmUnit ──(可选) H2D/RH2D──▶ HBM
```

中间经 DDR，时延与带宽都差一截。NDS（Non-Volatile Memory Direct Storage / xds）提供 **NVMe P2P 把文件 extent 直接读进 NPU 设备物理地址**，可绕过 DDR 驻留。

业务目标（讨论材料）：

- 对上仍是 **统一 KV 访问接口**
- 本机 SSD miss/hit 场景降时延（示例量级：10MB 约 30ms → 20ms，待实测校准）
- 模型侧：QWEN3.5-122B / GLM5 / Minimax-M2.7

## 2. Non-Goals（Phase-1）

| 不做 | 原因 |
|------|------|
| SSD 上对象走 RH2D / Pipeline H2D | SSD 无法作为 RH2D 源；远端 spilled 维持现状 |
| 跨机 SSD→本机 HBM 直通 | 无跨机 NVMe P2P；跨机仍走现有 remote get |
| 替换整套 spill 文件格式 | 先接现有 `SpillFileManager` location |
| 默认开启 TCP/RH2D 伪装直通 | 失败要明确 fallback，可观测 |

## 3. Scope Gate（何时走直通）

全部满足才走 NDS 直通，否则旧路径：

```text
local object table hit
&& entry->IsSpilled()
&& entry->GetShmUnit() == nullptr   // 或策略允许忽略不完整 shm
&& same-node Client↔Worker
&& HBM destination ready (registered or auto-allocated)
&& NDS/xds available
&& object already on spill file (NOT only in SpillBuffer)  // §3.2
&& AlignmentGate(file_offset, length, hbm_va+dest_off)  // 见 alignment.md
```

不满足任一项 → `LoadSpilledObjectToMemory` / 既有 Get 路径。  
**对齐失败不得强行走 xds**（公开 so 对偏移/地址敏感；错对齐风险高于多一次 DRAM）。

## 3.1 Alignment（一等约束）

完整条文见 [alignment.md](./alignment.md)。摘要：

| 侧 | Phase-1 建议门禁 | 产品下限 |
|----|------------------|----------|
| 文件 `offset` / `length` | **4096（默认）**；gflag 可降到 512 | 512 |
| HBM `addr` / `destOffset` | 与上同档，并满足页/DMA 要求 | ≥512 |
| 单次 IO | ≤128KiB/命令（xds 切片） | — |

Spill 现状不保证对象文件内对齐 → 读侧检查 + fallback；后续可评估 spill 写入 pad。

## 3.2 SpillBuffer vs 已落盘（已决）

| 对象数据位置 | 路径 |
|--------------|------|
| 仍在 `SpillBuffer`（未 flush 到文件） | **旧 DRAM 路径**（`LoadSpilledObjectToMemory` / host `Get`） |
| 已有文件 `ObjectLocation`（已落盘） | 可走 **NDS 直通**（再过 AlignmentGate / IPC 等） |

不做直通前强制 flush：避免读路径耦合写放大与 buffer 锁；小对象短窗口走 DRAM 可接受。  
判定：`SpillFileManager` 侧 `buffer_.Exist(key)` → 禁 NDS；仅 `objLocations_` 命中文件再考虑直通。
## 4. Architecture（To-Be 本地读）

```text
                    ┌─────────────────────────────────────┐
 Client 进程        │  DeviceBlob / Comm Buffer (HBM VA)   │
                    │  ① Register（自带数据 Buffer）或       │
                    │  ② SDK 代分配后再 Register             │
                    │  → Export IPC key ──RPC──┐            │
                    └──────────────────────────│────────────┘
                                               │
                    ┌──────────────────────────▼────────────┐
 Worker 进程        │  ImportByKey → local HBM VA           │
                    │  Spill location (path, offset, size)  │
                    │  → fiemap extents                     │
                    │  → xds IOCTL_READ_FILE(_BATCH)        │
                    │     NVMe READ + P2P → device PA       │
                    └─────────────────────────────────────┘
                                   ▲
                                   │
                              Local SSD (RAID0 ×3)
```

写路径 Phase-1 **不改**：仍是现有 Eviction → `WorkerOcSpill::Spill` → SSD。  
（讨论中的「HBM 满再 spill」属容量策略，可后续与多级缓存一并做。）

## 5. Approaches Considered

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A. Worker 预分配 HBM Comm Arena | Worker 分配并 Export | 生命周期集中 | 多 Client 争用；难与推理数据区合一 | 备选 |
| **B. 统一 KV + Register；未注册用专用通信内存** | `Get`/`MGet`；显式 Register；缺省 SDK 分配 **comm buffer** 再走同一套 IPC | 对上一个接口；共享机制单一 | 未注册时多一次 HBM→数据区拷贝（若业务另有 data buffer） | **Phase-1 主选** |
| C. 仅加速 SSD→DRAM | 不碰 HBM | 改动小 | 达不到「直通 HBM」目标 | 否 |

**推荐 B（API）+ Client/SDK 持有 Buffer + CANN IPC**：

- 提供 **RegisterHbmBuffer**（名待定）。
- 已注册：Worker Import 后 NDS 直写该 VA → **零拷贝**交给业务（数据 Buffer ≡ 通信区）。
- **未注册**：SDK 分配 **专用 comm buffer**，走 **同一套 IPC Export/Import**；NDS 写入后 SDK **device→device（或约定方向）拷贝到用户提供的 data buffer**；**不把 comm VA/句柄暴露给调用方**。
- 两种 Buffer 来源差异只在「谁分配 / 是否零拷贝」；**共享机制、Register RPC、Worker Import 表、NDS 写入路径必须一致**。

### 5.1 进程模型（已决）

**Phase-1：同机跨进程 + CANN IPC 共享 HBM**。

```text
Client process                         Worker process
─────────────────                      ─────────────────
① 用户 Register(data VA)  → 零拷贝路径
   或 ② 未注册 → SDK 分配专用 comm HBM（内部）
  → aclrtIpcMemGetExportKey(key[65])
  → aclrtIpcMemSetImportPid(worker_tgid)
  → RPC Register(key, deviceIdx, size, role=data|comm, ...)
                                       → aclrtIpcMemImportByKey → local VA
                                       → NDS read_file_batch 写入该 VA
  ← Get/MGet 完成通知
  → role=data：业务直接使用注册 Buffer（零拷贝）
  → role=comm：SDK 拷贝到用户 data buffer，comm 不暴露
```

约束：

- Client / Worker **同机**、各自独立进程。
- **仅 IPC** 作为 Phase-1 共享机制（`IpcMemGetExportKey` / `ImportByKey`）；VMM ShareableHandle 后置。
- 专用通信内存与用户注册内存：**同一套 Export → Register RPC → Import → NDS**，禁止为「未注册」另开同进程捷径或第二套共享协议。
- **专用 comm 对外不透明**：无 Register 时调用方只看见自己的 data buffer；comm 的分配/IPC/释放全在 SDK 内部。
- PID 白名单：优先 `aclrtDeviceGetBareTgid` + `SetImportPid`。
- xds `va_desc.hostpid` / `devid` 与 Worker Import 后映射一致。

### 5.2 对上 API（已决）

- **统一 KV**：`Get` / `MGet`（及 Hetero 若已有的 KV 门面）为主入口。
- **显式注册接口**：连接后或 Get 前调用 Register；注册后直通 **零拷贝**。
- **未注册**：内部专用 comm + IPC（与 Register 同机制）→ 完成后 **拷贝到用户 data**；不暴露 comm。
- `MGetH2D`：不作为直通主 API；可保留为薄封装/兼容层，内部转到「Register + KV Get」或「隐式 comm + 拷贝」。

### 5.3 交付语义（已决）

| 是否 Register | NDS 写入目标 | 对调用方 |
|---------------|--------------|----------|
| 是（data/comm 注册区） | 注册 VA | **零拷贝**可见 |
| 否 | 内部专用 comm | **拷贝到用户 data**；comm 不暴露 |
## 6. Components

### 6.1 Client

- **RegisterHbmBuffer(deviceIdx, ptr, size, flags)**  
  - 注册用户数据区（或显式通信区）→ 直通 **零拷贝**。  
  - Client 进程 Export IPC key → RPC 给 Worker Import。  
- **EnsureCommBuffer()**（内部，未 Register 时）：`aclrtMalloc` 专用 comm → **同一套** IPC+RPC；Get 完成后 **拷到用户 data**，调用方不可见 comm。  
- **Get / MGet**：本地 spilled 且已有 Import mapping 时走 NDS 直通；完成后按 §5.3 交付。  
- Unregister / 析构：等 in-flight 结束后 `IpcMemClose` / free（专用 comm 仅 SDK 释放）。

### 6.2 Worker

- 识别 `KeepObjectDataInMemory` / `PreProcessGetObject` 中 spilled 本地命中。
- 新旁路：**不要**先 `AllocateMemoryForObject(DRAM)`；改为解析 `ObjectLocation` → 对已 Import 的 HBM VA 调 NDS。
- 维护 `ImportedHbmMapping`：`clientId → {ipcKey, localVa, deviceIdx, size, role, refcount}`。  
  - **不**因 `role=data|comm` 切换共享或 IO 实现。
- 不修改 `LoadPayloadAndFillResponse` 的「spilled → 禁 RH2D」语义（远端仍禁）。

### 6.3 NDS / xds 适配层（新）

详见 [tech-brief-xds-nds.md](./tech-brief-xds-nds.md)。要点：

- Userspace：`read_file` / `read_file_batch` / `drain_read`（`file_p2p_api`）
- Kernel：`p2p_dev.c` NVMe READ SGL → HBM PA（`devmm_get_mem_pa_list`）
- **Worker 必须先 IPC Import，再对 imported VA 调 xds**（userspace `hostpid=getpid()`）
- 对齐 512B；单 IO 切片上限 128KiB

参考 [mycastiel/xds](https://github.com/mycastiel/xds)。

### 6.4 HBM IPC 共享（仓内现状：无 → Phase-1 新建）

详见 [tech-brief-cann-ipc-hbm.md](./tech-brief-cann-ipc-hbm.md)。要点：Export key[65] → SetImportPid → Worker ImportByKey → NDS；Register 与专用 comm **同一套机制**。

## 7. Data Flow（本地 spilled Get）

```text
1. Client: RegisterHbmBuffer(dataBlob) **或** EnsureCommBuffer()（内部，不暴露）
2. Client: KV Get / MGet(keys, userDataDest)
3. Worker: local hit + IsSpilled
4. Worker: resolve SpillFileManager location
5. Adapter: fiemap(file) → extents; map HBM VA → PA (xds)
6. Adapter: IOCTL_READ_FILE_BATCH → drain（写入 Import 后的 VA：注册区或内部 comm）
7. Worker: reply OK
8. Client:
   - 已 Register → 用户直接使用注册 Buffer（零拷贝）
   - 未 Register → SDK 将 comm 拷到 userDataDest，comm 保持内部
```

失败任一步：记录原因 → DRAM reload 旧路径（若仍需要 Host 视图）或返回明确错误。

## 8. Mapping: KV / Object / Block（讨论材料对齐）

讨论中的分层可与现有 Object 模型对齐，不必一期重做元数据：

| 讨论概念 | Datasystem 现状 | Phase-1 |
|----------|-----------------|---------|
| Key → Object | objectKey → SafeObj / ObjCacheShmUnit | 不变 |
| Object = blocks (meta/data/addr) | meta in memory；data in Shm 或 spill file | spill 仍整对象或大文件内 offset |
| layer-i / layer-j KV block | 应用层组织 | 上层用统一 KV；直通按 object 级 offset/size |
| 512B 对齐 | spill 未强制对外承诺 | 直通路径强制；否则 fallback |

## 9. Environment / Performance Context

| 项 | 值 |
|----|-----|
| Host NPU 驱动 | HDK ≥ 25.2（推荐 25.5） |
| CANN | 8.1RC0；8.5 可用 |
| Kernel | 4.19 / 5.10 已验；6.6 待验 |
| HBM 大页 | 无所谓；建议同一粒度管理 |
| SSD | RAID0 × 3；fio 约 30GB/s |
| 带宽参考 | HCCS HBM↔DDR ~70GB/s；PCIe H2D A2 ~20GB/s；直通标注 ~20GB/s |
| 时延参考 | 10MB：30ms → 20ms（待基准测试锁定） |

## 10. Observability

至少区分：

- `local_mem_hit` / `local_spill_dram_reload` / `local_spill_hbm_direct` / `direct_fallback`
- 直通失败原因：alignment / nds_unavailable / import_failed / io_error / still_in_spill_buffer
- 耗时：fiemap、ioctl issue、drain、e2e
- hit 分类增加：`local_spill_dram_reload`（含 buffer 未落盘）、`local_spill_hbm_direct`
## 11. Risks

| 风险 | 缓解 |
|------|------|
| xds 内核模块与发行版内核绑定 | 环境矩阵；不可用即 fallback |
| Client 数据 Buffer 生命周期 | Register 引用计数；Unregister 等 in-flight |
| 512B / 2MB / **4K fiemap** 对齐 | **AlignmentGate 一等约束**；见 [alignment.md](./alignment.md) |
| 与现有 RH2D 混淆 | 文档与代码路径显式分流；spilled 永不走 RH2D |
| Spill 小对象 buffer 未落盘 | **已决**：走 DRAM（§3.2），不强制 flush |

## 12. Decisions（已收口）

完整表见 [decisions.md](./decisions.md)。原 Open Questions 收口如下：

1. 进程模型 → 同机跨进程 + CANN IPC（§5.1）  
2. API → 统一 KV + Register；未注册 comm+拷贝（§5.2–5.3）  
3. SpillBuffer → 未落盘走 DRAM；仅落盘可 NDS（§3.2）  
4. 写路径 → Phase-1 **不动** HBM→SSD spill 大改  
5. 验收 → 先相对 A/B；10MB/20ms 作参考不绑死 SLO  
6. 对齐 → 可配置，**默认 4096**（下限 512）  
7. Spill 写 pad → Phase-1 **不做**；读侧门禁 + fallback  
8. XDS → `read_file`+`drain_read`；batch 后置；每请求 fd  

待实机：Import VA⊕NVMe P2P、对齐降到 512、kernel 6.6、bdev 拓扑（decisions **V1–V4**）。

**Self-review**: 设计文档已可支撑 Stage A / M1；直通编码前建议先完成 Stage A 验证。
