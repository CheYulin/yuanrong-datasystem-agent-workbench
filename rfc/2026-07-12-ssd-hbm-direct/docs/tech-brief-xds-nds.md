# Tech Brief: xds / NDS SSD → HBM Direct I/O

**Date**: 2026-07-12  
**Status**: Research note  
**Repo**: https://github.com/mycastiel/xds  
**RFC**: [design.md](./design.md)

> 公开仓是 **NVMe P2P 读文件 → NPU 物理地址** 的 .so/.ko 样例；**不含** KV/Object 语义（那是 DataSystem 层）。

## 1. Architecture split

| 层 | 路径 | 职责 |
|----|------|------|
| Userspace .so | `file_p2p/file_p2p_api.c` | open 文件/bdev、`FS_IOC_FIEMAP`、组 `read_desc`、ioctl |
| Kernel .ko | `p2p_dev.c` | VA→PA（devmm）、发 NVMe READ SGL 到 PA、batch drain |
| UAPI | `p2p_dev_uapi.h` | ioctl + `va_desc` / `read_desc` / batch |
| PA helper | `p2p_mem_query.h` | `devmm_get_mem_pa_list` / `put` / page size |

**产品叙述中的「KV block / layer / Key→Object」不在 xds 仓内** — 由 DataSystem Object/Spill 映射后，把 `(file, offset, size, hbm_va)` 喂给 xds。

## 2. Userspace API（FACT）

`file_p2p/file_p2p_api.h`：

```c
struct read_parameter {
  const char *file_name;
  const char *bdev_name;
  unsigned long bdev_offset;  // 文件内逻辑偏移（再经 fiemap → 物理）
  unsigned short devid;
  unsigned short vfid;
  unsigned int size;
  unsigned long addr;         // HBM/Device VA（调用进程地址空间）
};

int new_p2p_fd(void);                    // open /dev/p2p_device
int read_file(int, struct read_parameter *);
int read_file_batch(int, struct read_parameter *, int param_num);
int drain_read(int);
```

典型调用：

```text
fd = new_p2p_fd()
read_file_batch(fd, params, n)   // 异步提交
drain_read(fd)                   // 等待 batch 完成
close_p2p_fd(fd)
```

## 3. UAPI（FACT）

| ioctl | 作用 |
|-------|------|
| `IOCTL_DUMP_PA` | VA → PA 调试 |
| `IOCTL_READ_FILE` | 单次读 |
| `IOCTL_READ_FILE_BATCH` | 批量 VA 列表 + 共享 extents |
| `IOCTL_DRAIN_READ` | 等待 batch 内 IO 完成 |

`va_desc`：`hostpid`, `devid`, `vfid`, `addr`, `size`  
`read_desc`：va_desc + `bdev_fd` + `nsid` + `file_fd` + `fiemap_extent[]`  
batch：`va_desc_ba` 带 `addr[]`/`size[]`/`count`

## 4. Kernel path（FACT）

1. ioctl 入口取 `p2p_batch`（open 时分配，idr 管理）。  
2. `get_pa_list*`：用 `devmm_svm_process_id{hostpid,devid,vfid}` + VA 调 `devmm_get_mem_pa_list`。  
3. `do_read_ios*`：按 fiemap extent 的 **物理 LBA** 与当前 PA 切片。  
4. `do_read_io`：
   - `cmd->rw.opcode = nvme_cmd_read`
   - `slba` / `length`（0-based NLB）
   - `dptr.sgl.addr = paddr`，SGL DATA DESC
   - `req->rq_flags |= RQF_NVME_PT`（私有 flag，配合 trace hook）
   - `blk_execute_rq_nowait` + completion  
5. 单次 IO 上限：`HW_LIMIT_SIZE = 128KiB`（按 sector 切片）。

## 5. Constraints table

| 项 | 值 / 行为 | 来源 |
|----|-----------|------|
| **对齐（重点）** | 见 [alignment.md](./alignment.md)：文件 offset/length **≥512B**；公开 so 含 **4K** fiemap 修正；HBM VA 同样要对齐 | 产品提醒 + `file_p2p_api.c` + sector 路径 |
| 单命令上限 | 128 KiB | `HW_LIMIT_SIZE` |
| hostpid | userspace **`getpid()`** | `file_p2p_api.c` |
| bdev | 需同时给 `file_name` 与 `bdev_name` | API |
| nsid | userspace 写死 `1` | `file_p2p_api.c` |
| RAID0 | 对 xds 透明（底层 bdev）；带宽来自盘阵 | 讨论材料 |
| Kernel | 讨论：4.19/5.10 OK；6.6 待验 | 讨论材料 |

**集成原则**：DataSystem **先做 AlignmentGate，再调 xds**；不对齐只 fallback，不依赖 so 内隐式修正。

## 6. Integration with SpillFileManager

Spill location：`ObjectLocation{path, offset, size}`。

建议适配步骤：

```text
1. Worker 已 Import Client HBM → localVa (+ object offset into mapping)
2. 校验 offset/size 512B 对齐；否则 fallback DRAM 路径
3. 准备 read_parameter:
     file_name   = spill 文件 path（或聚合文件）
     bdev_name   = 该文件系统所在 NVMe 块设备（部署配置）
     bdev_offset = location.offset (+ 对象内 readOffset)
     size        = readSize
     addr        = localVa + destOffset
     devid/vfid  = Client/Worker 约定的 NPU 身份
4. read_file_batch([...]) ; drain_read()
5. 成功 → 按 role 零拷贝或 Client 侧拷贝交付
```

**注意**：当前公开 `read_file_batch` 假设同一 `file_name`/`bdev` 上连续/批量 param；多 object 不同文件需多次调用或扩展 API（INFERENCE）。

## 7. Map product architecture → code

| 产品模块 | xds/DataSystem |
|----------|----------------|
| 模块1 `read_file_batch` .so→.ko | **xds** `file_p2p_api` + `p2p_dev` |
| 模块2 Key→Object→block | **DataSystem** meta/object；xds 无感知 |
| 模块3 多级缓存 / Spill | **DataSystem** eviction + `WorkerOcSpill`；读 miss 时走 xds 直通 |
| HBM↔DDR↔SSD HCCS 70GB/s | 非 xds；既有 HCCS/RH2D 辅助路径 |
| SSD 直通 HBM ~20GB/s | **xds NVMe P2P** 主读路径 |
| 统一 KV 接口 | DataSystem Client API |

## 8. Gaps in public repo

- 文档/README 几乎无；生产打包、权限、`/dev/p2p_device` 部署未说明  
- `nsid=1`、RQF 私有 bit、`tp_nvme_setup_cmd_addr` module_param 偏实验性  
- userspace 错误处理/对齐校验不完整（产品说后续 so 层补齐）  
- 与 CANN IPC imported VA 的联调 **不在仓内**  
- Python binding 仅 test 级  

## 9. Risks for DataSystem

| ID | 风险 | 缓解 |
|----|------|------|
| X1 | hostpid 必须与 VA 所属进程一致 | Worker Import 后再调 so |
| X2 | Spill 小对象仍在 SpillBuffer | **已决**：buffer 走 DRAM；仅落盘走 NDS（design §3.2） |
| X3 | bdev_name 发现 | 部署注入或 `/sys` 解析 |
| X4 | **对齐：文件偏移 + HBM 地址** | 一等门禁，见 [alignment.md](./alignment.md)；默认建议 4K |
| X5 | 与 DRAM reload 双路径一致性 | 同一 object version 门闩 |

## 10. Links

- https://github.com/mycastiel/xds  
- https://github.com/mycastiel/xds/blob/main/p2p_dev.c  
- https://github.com/mycastiel/xds/blob/main/file_p2p/file_p2p_api.c  
- https://github.com/mycastiel/xds/blob/main/p2p_dev_uapi.h  
- IPC brief: [tech-brief-cann-ipc-hbm.md](./tech-brief-cann-ipc-hbm.md)
