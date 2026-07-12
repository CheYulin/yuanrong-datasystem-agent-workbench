# Decision Log (Phase-1 Defaults)

**Date**: 2026-07-12  
**Rule**: 已讨论项直接收口；仅实机/驱动未知项列入「待验证」，不阻塞文档与 Stage A 实施设计。

## 产品与范围

| ID | 决策 | 备注 |
|----|------|------|
| D1 | 仅本机 `IsSpilled` SSD→HBM 直通 | 远端 spilled / SSD RH2D 不做 |
| D2 | 统一 KV `Get`/`MGet` + 显式 `RegisterHbmBuffer` | `MGetH2D` 可后置薄封装 |
| D3 | 同机跨进程；HBM 共享 = **CANN IPC only** | VMM ShareableHandle 后置 |
| D4 | 已 Register → 零拷贝；未 Register → 专用 comm + **拷到 user data**，不暴露 comm | 两套业务，**一套 IPC** |
| D5 | SpillBuffer 未落盘 → DRAM；仅文件 location → 可 NDS | 不强制读路径 flush |
| D6 | Phase-1 **不改** HBM→SSD 写/spill 大路径 | 多级缓存写优化后置 |
| D7 | Spill **写侧暂不 pad**；读侧 AlignmentGate + fallback | 提高命中率的 pad 后置评估 |

## 对齐

| ID | 决策 | 备注 |
|----|------|------|
| D8 | `nds_align_bytes` **可配置**；Phase-1 **默认 4096** | 产品下限 512；实机验证后再考虑降默认 |
| D9 | 门禁覆盖：file offset/length **与** HBM addr/destOffset | 失败不进 xds |

## XDS 接口用法

| ID | 决策 | 备注 |
|----|------|------|
| D10 | Worker 调 so；`addr` = Import 后 localVa | `hostpid=getpid()` 与公开 so 一致 |
| D11 | Phase-1 Adapter：`read_file` + `drain_read` | `read_file_batch` 多 key 优化时再上 |
| D12 | `p2p_fd`：**每请求 open/close**（或短生命周期） | 先正确后性能；池化后置 |
| D13 | `bdev_name` 部署配置注入；`nsid` 先跟 so=1 | 多 ns 再配置化 |
| D14 | 内部 API：`NdsReadSpillToHbm(...)` 封装 xds | 便于 mock / Stage A 隔离 |

## 验证与观测

| ID | 决策 | 备注 |
|----|------|------|
| D15 | **先 Stage A（IPC+H2D/D2H pattern），再 Stage B（XDS 直通）** | A 不绿不上 B |
| D16 | Stage A 主证据：Worker `aclrtMemcpy`/D2D 写 pattern ↔ Client 校验 | RH2D 仅可选对照 |
| D17 | 验收先做 **相对 A/B**（DRAM reload vs NDS）；10MB/20ms 作参考不绑死正式 SLO | 数字进 results 后再定 SLO |
| D18 | Metrics 区分：direct / dram_reload / fallback{align,buffer,nds} | 见 verification-observability.md |
| D19 | 观测三件套：**PerfKey + Metrics/AccessRecorder + Trace**；清单见 [observability.md](./observability.md) | 实现挂 Task 8；报表用现有 gen_kv_perf_report / obs smoke 脚本 |

## 待实机验证（有问题再找人）

| ID | 项 | 为何不能纸上拍死 |
|----|-----|------------------|
| V1 | Import VA 作 NVMe P2P DMA 目的是否稳定 | 依赖 HDK/devmm/xds |
| V2 | 默认对齐能否从 4096 降到 512 | 依赖 so/fiemap 实机 |
| V3 | kernel 6.6 | 讨论材料待验 |
| V4 | RAID0 多盘 + 具体 `bdev_name` 拓扑 | 部署相关 |

有 V1–V4 异常再升级讨论；其余按本表推进。
