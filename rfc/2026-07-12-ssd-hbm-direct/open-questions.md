# Open Questions → Closed / Escalate

> 产品默认已收口。仅 **V1–V4** 需实机验证；失败再升级决策。

## Closed（见 decisions.md）

| ID | 结论 |
|----|------|
| Q1 | 仅本机 IsSpilled；禁止跨机 SSD→HBM |
| Q2 | 统一 Get/MGet + RegisterHbmBuffer；MGetH2D 可选薄封装 |
| Q3 | CANN IPC；VMM 延后 |
| Q4 | Register→零拷贝；无 Register→comm+拷贝；不暴露 comm |
| Q5 | SpillBuffer 仍在 buffer → 旧路径；NDS 只吃盘上 location |
| Q6 | offset/length/addr 对齐门控；默认 4K；失败 fallback |
| Q7 | Phase-1 不改 spill pad / HBM→SSD |
| Q8 | Worker 调 xds；addr=Import VA；read_file+drain；每请求 p2p_fd；bdev 配置；nsid=1 |

## Escalate only on failure（实机）

| ID | 问题 | 失败时影响 |
|----|------|------------|
| V1 | Import 后 Worker VA 能否作 NVMe P2P 目的地 | 整条 NDS 不可用 |
| V2 | 对齐能否从 4K 降到 512 | 仅影响命中率，可保持 4K |
| V3 | kernel 6.6 / 驱动兼容 | 可能锁平台版本 |
| V4 | bdev / RAID / fiemap 拓扑 | 运维约束或禁用 NDS |

## Explicitly out of Phase-1

- 跨机 SSD→HBM / RH2D+NDS
- VMM ShareableHandle
- batch_file_p2p_read / 长生命周期 p2p_fd
- spill pad / HBM→SSD 写路径改造
- 暴露内部 comm buffer 给用户
