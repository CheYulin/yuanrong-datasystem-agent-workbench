# Alignment Requirements (XDS / NDS) — First-Class Constraint

**Status**: Must-enforce for direct path  
**Why**: 当前 xds 对 **HBM 虚拟地址** 与 **文件/块偏移、长度** 要求高；不对齐会直接 ioctl/NVMe 失败或静默错读。DataSystem 直通路径必须在进 NDS 前做硬门禁，失败则 **fallback**（不重试硬怼）。

## 1. Two surfaces that must both align

```text
                    file side                         memory side
        ┌──────────────────────────┐      ┌──────────────────────────┐
        │ spill file offset        │      │ HBM VA (Import 后 local) │
        │ object readOffset        │      │ + destOffset in mapping  │
        │ readSize / length        │      │ mapping size / chunk     │
        │ fiemap fe_physical/len   │      │ PA page / DMA 切片       │
        └────────────┬─────────────┘      └────────────┬─────────────┘
                     │                                  │
                     └──────────► xds read_file(_batch) ◄──────────┘
```

任一侧不满足 → **禁止直通**。

## 2. Known requirements（FACT + 产品）

| 维度 | 要求 | 依据 |
|------|------|------|
| 文件逻辑 `offset` / `length` | **512B 对齐**（产品硬约束） | 讨论稿；sector = 512 |
| NVMe LBA / extent 物理 | 按 **sector（512）** 换算；`fe_physical`、`fe_length` 需能整除 sector | `p2p_dev.c` `>> SECTOR_SHIFT` |
| 用户态 fiemap 窗口 | 与 **4KiB** 交互：`max_num = size>>12`；并对 `bdev_offset % 4096` 修正首 extent | `file_p2p_api.c` |
| HBM `addr`（Device VA） | 须满足驱动/DMA 可解析；实践上至少 **512B**，常与 **页大小（2MiB huge / 4KiB）** 绑定 | 产品提醒 + `devmm_get_mem_page_size` |
| 单次 NVMe 命令 | ≤ **128KiB** 切片 | `HW_LIMIT_SIZE` |
| Register 基址 | Client 注册 / SDK 分配的 HBM base 应对齐，避免 `localVa+off` 破坏约束 | 集成推导 |

> **推断（待实机钉死）**：若 HBM 为大页，目标 `addr` 与 `size` 宜同时满足 `lcm(512, page_size)` 或至少 `addr % page_size` 与 DMA 页边界一致。验收矩阵需单列「对齐边界用例」。

## 3. Evidence in xds userspace

`file_p2p_api.c`（公开仓）对文件偏移有 4K 修正逻辑：

- `fm_start = bdev_offset`
- 首 extent：`fe_physical += bdev_offset % 4096`，`fe_length -= bdev_offset % 4096`

说明：**非 4K 对齐的文件偏移** 依赖这套修正；修正失败或与 512B 产品约束冲突时行为脆弱。DataSystem **不应依赖隐式修正**，应在上层保证：

```text
spill_offset % 512 == 0
read_size     % 512 == 0
(hbm_va + dest_off) % 512 == 0
```

并在联调中验证是否还需：

```text
spill_offset % 4096 == 0   // 若 so 层未完善，Phase-1 可先抬高到 4K
(hbm_va + dest_off) % PAGE == 0
```

## 4. Gate in DataSystem（设计要求）

直通 eligibility 增加 **AlignmentGate**（与 `IsSpilled` / IPC mapping 并列）：

```text
bool CanNdsDirect(loc, hbmVa, destOff, len):
  return loc.offset % ALIGN == 0
      && len % ALIGN == 0
      && (hbmVa + destOff) % ALIGN == 0
      && len > 0
      && mapping covers [destOff, destOff+len)
```

`ALIGN` Phase-1 建议配置化：

| 档位 | 值 | 用途 |
|------|-----|------|
| `nds_align_min` | 512 | 产品下限 |
| `nds_align_preferred` | 4096 | 与 fiemap/公开 so 更稳 |
| （可选）page | `devmm`/acl 页大小 | HBM huge |

**默认策略（已决 D8）**：Phase-1 `nds_align_bytes` **默认 4096**；可配置降到 512（实机 V2 验证后）。512～4K 之间走 fallback。

## 5. Spill layout implications

现有 `SpillFileManager`：

- 小对象进 `SpillBuffer` 再 flush；大对象直接写文件。  
- **未承诺** 对象在文件内 512/4K 对齐。

直通要落地，需至少选一：

1. **Spill 写入即对齐**：新 spill 对象 pad 到 `nds_align_*`（容量换直通命中率）  
2. **仅对齐对象走直通**：读时检查 location；不对齐 fallback DRAM  
3. **读侧拷贝到对齐 bounce**（违背「少 DDR」；不推荐作主路径）

**推荐 Phase-1（已决 D7）**：策略 2（检查 + fallback）；策略 1（写 pad）后置。

## 6. Register / Comm buffer implications

| Buffer | 要求 |
|--------|------|
| 用户 Register data | 基址与可用 size 满足 `nds_align_*`；Get 的 destOffset/len 再检一次 |
| 专用 comm（未 Register） | SDK `aclrtMalloc` 时按对齐分配；内部拷到用户 data 时用户 buffer 对齐由 API 契约约束 |
| IPC Import 后 VA | 以 Worker `localVa` 做门禁，不假设与 Client 原 VA 数值相同 |

## 7. Observability

失败原因枚举至少拆开：

- `align_file_offset`  
- `align_length`  
- `align_hbm_addr`  
- `align_combined`  

metrics：`local_spill_hbm_direct` vs `direct_fallback_alignment`。

## 8. Test matrix（必测）

- offset/size/addr 全 4K 对齐：直通成功  
- 仅 512 对齐、非 4K：记录是否成功（决定 `nds_align_preferred`）  
- offset 或 addr 差 512/4096±1：必须 fallback，不得踩内核  
- 跨 fiemap extent 边界、长度跨 128KiB 切片  
- Register 零拷贝与 comm+拷贝两条路径各测一遍  

## 9. Hardware unknowns（V1–V4）

见 [decisions.md](./decisions.md) **V1–V4**（Import⊕P2P、对齐降档、kernel 6.6、bdev 拓扑）。纸面默认已定，实机异常再改。
