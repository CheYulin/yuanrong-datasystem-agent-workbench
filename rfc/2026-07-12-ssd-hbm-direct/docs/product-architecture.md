# Product Architecture Notes (Discussion Material)

**Source**: 内部讨论稿「DataSystem 集成 NDS SSD 直通 HBM…统一 KV 接口」  
**Mapped into**: [design.md](../design.md), [tech-brief-xds-nds.md](./tech-brief-xds-nds.md)

## Module map

1. **Userspace ↔ Kernel（read_file_batch）** → xds `.so` / `.ko`（IO only）  
2. **KV Block ↔ Object（Key→Object[meta|data|addr]）** → DataSystem object/spill meta  
3. **File R/W + multi-tier cache** → DataSystem；读 DDR miss 时走 SSD→HBM 直通  

## Bandwidth narrative（讨论数字，待基准锁定）

| 路径 | 角色 | 量级 |
|------|------|------|
| HBM↔DDR（HCCS） | 辅助 | ~70GB/s |
| SSD→HBM 直通（NVMe P2P / PCIe） | **主读** | ~20GB/s |
| Spill 写 SSD RAID0×3 | 写/容量 | fio ~30GB/s；讨论标注 ~20GB/s |

## Alignment

offset/length **512B**；否则 fallback（未来 so 层可补齐）。

## Env（讨论）

HDK ≥25.2（推荐 25.5），CANN 8.1RC0 / 8.5；Kernel 4.19/5.10 OK，6.6 TBD。
