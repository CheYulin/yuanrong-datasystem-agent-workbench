# References: SSD→HBM Direct

## NDS / xds（直通 IO）

- Repo: https://github.com/mycastiel/xds
- Kernel: [`p2p_dev.c`](https://github.com/mycastiel/xds/blob/main/p2p_dev.c)
  - `struct p2p_batch`
  - `do_read_io`：`NVME_CMD_READ`，`slba` / `length`，P2P 相关 request flags
- UAPI: [`p2p_dev_uapi.h`](https://github.com/mycastiel/xds/blob/main/p2p_dev_uapi.h)
  - `IOCTL_READ_FILE` / `IOCTL_READ_FILE_BATCH` / `IOCTL_DRAIN_READ` / `IOCTL_DUMP_PA`
  - `va_desc` / `read_desc`（含 fiemap extents）
- Userspace 概念：`read_file_batch`（.so → .ko）

## CANN HBM 共享（仓内尚未实现；跨进程时参考）

- IPC：`aclrtIpcMemGetExportKey` / `aclrtIpcMemImportByKey` / `aclrtIpcMemSetImportPid`
- VMM：`aclrtMallocPhysical` / `aclrtReserveMemAddress` / `aclrtMapMem` /
  `aclrtMemExportToShareableHandle(V2)` / `aclrtMemImportFromShareableHandle(V2)`
- 文档入口（社区版示例）：
  - https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/83RC1alpha002/API/appdevgapi/aclcppdevg_03_1934.html

## Mooncake（HBM / Fabric / 传输参考，非本机 NVMe P2P）

- Ascend Direct Transport: https://kvcache-ai.github.io/Mooncake/design/transfer-engine/ascend_direct_transport.html
- ubshmem via VMM: https://github.com/kvcache-ai/Mooncake/pull/1399
- `ascend_allocator.cpp` / `ubshmem_transport` / `shm_helper` / `real_client` Import shareable handle

## Datasystem 现状相关代码

- Spill：`src/datasystem/worker/object_cache/worker_oc_spill.{h,cpp}`
- Eviction spill：`worker_oc_eviction_manager.cpp` (`SpillImpl`)
- Local reload：`obj_cache_shm_unit.cpp` (`LoadSpilledObjectToMemory`)
- Get：`worker_oc_service_get_impl.cpp` (`KeepObjectDataInMemory`)
- Remote spilled：`worker_worker_oc_service_impl.cpp` (`LoadPayloadAndFillResponse`)
- RH2D：`common/rdma/npu/remote_h2d_manager.*`，Client `MGetH2D`

## 讨论材料摘要（2026-07）

**标题**：DataSystem 集成 NDS SSD 直通 HBM 能力降时延，对上暴露统一的 KV 访问接口

**栈与环境**：

- Host NPU：HDK-25.2+（HDK25.5），CANN8.1RC0 / CANN8.5
- Kernel：4.19 / 5.10 OK；6.6 待验
- HBM 大页无所谓；同一粒度管理
- RAID0 × 3；fio ~30GB/s
- 模型：QWEN3.5-122B，GLM5，Minimax-M2.7
- 时延参考：10MB 30ms → 20ms

**架构要点**：

- 用户态 `read_file_batch` → 内核 P2P IO
- KV block / layer 组织；Key→Object→block(meta|data|addr)
- 读：DDR miss 时 SSD 直通 HBM
- 写：多级缓存，HBM 满 spill SSD
- 对齐：512B；否则 fallback
