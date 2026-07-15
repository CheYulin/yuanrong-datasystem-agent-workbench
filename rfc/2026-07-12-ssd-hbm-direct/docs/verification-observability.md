# Verification & Observability Strategy

**Date**: 2026-07-12  
**Status**: Draft  
**Related**: [work-breakdown.md](../work-breakdown.md), [tech-brief-cann-ipc-hbm.md](./tech-brief-cann-ipc-hbm.md), [tech-brief-xds-nds.md](./tech-brief-xds-nds.md), [alignment.md](./alignment.md)

## 1. 分阶段打通（先共享，后直通）

```text
Stage A  HBM IPC + Register 可观测验证
         （先不依赖 xds / spill）
              │ 用常规 H2D / D2H / D2D 证明 Import VA「可写可读」
              ▼
Stage B  SSD → HBM 直通
         （对齐门禁 + 冻结 XDS 接口用法 + 集成 Spill location）
```

原则：

- **Stage A 失败 → 不进入 Stage B**（否则直通问题与 IPC 问题缠在一起）。
- Stage A **刻意不用 NDS**，降低变量。
- Stage B 单独打开对齐、fiemap、bdev、xds 变量。

---

## 2. Stage A — HBM 共享内存与注册

### 2.1 目标

证明：Client Export → Register RPC → Worker Import 后，**Worker 持有的 localVa 与 Client 侧物理页一致且可安全访问**。

### 2.2 验证手段（常规 H2D/D2H，非 xds）

| 步骤 | 谁 | 做什么 | 期望 |
|------|----|--------|------|
| A1 | Client | `aclrtMalloc`（或用户 Register data）→ `IpcMemGetExportKey` → `SetImportPid(worker)` → Register RPC | Worker 返回 `mapping_id` OK |
| A2 | Worker | `IpcMemImportByKey` → 得到 `localVa` | Import 成功；记 device_idx/size |
| A3 | Worker | 用 **常规 ACL**：`aclrtMemcpy` / 异步 D2D，把已知 pattern 写入 `localVa` | 写成功 |
| A4 | Client | 对**同一逻辑 buffer**（注册 VA 或经拷贝后的 user data）做 D2H 或 Device 侧校验 pattern | pattern 一致 → **共享成立** |
| A5 | 反向 | Client 先 H2D 写 pattern → Worker 从 `localVa` D2H/校验 | 双向一致 |
| A6 | 生命周期 | Unregister / Close 顺序；in-flight 与 free | 无 UAF；错误码明确 |
| A7 | 未 Register 路径 | SDK 专用 comm → 同 IPC → Worker 写 → Client **拷到 user data** | 用户不见 comm；内容正确 |

也可用现有 **RH2D / Pipeline H2D**（DRAM Shm → Device）作对照，但 Stage A 主结论应落在 **IPC mapping 本身**，不要和「spill 读盘」绑死。

### 2.3 Stage A 可观测点

| 信号 | 说明 |
|------|------|
| `hbm_ipc_register_total` / `_fail` | Register 次数与失败原因（export/import/pid/device） |
| `hbm_ipc_import_latency` | Import 耗时 |
| `hbm_ipc_mapping_count` | 当前活跃 mapping |
| `hbm_ipc_unregister_inflight_reject` | 有 in-flight 时拒绝 unregister |
| 日志 | `mapping_id`, `client_id`, `device_idx`, `size`, `role=DATA\|COMM`（COMM 仅内部） |

**Stage A Pass 标准（建议）**

1. Register 零拷贝路径：Worker 写入 ↔ Client 读回 pattern 100% 一致。  
2. 未 Register 拷贝路径：user data 一致且 API 不泄露 comm。  
3. 同 device；跨 device 明确失败。  
4. 上述 metrics 可在单测/ST 日志中捞到。

---

## 3. Stage B — SSD→HBM 直通

### 3.1 目标

在 Stage A 已绿的前提下：本地 **已落盘** spill 对象 → AlignmentGate → **XDS** 写入 Worker `localVa` → Client 按 Register/拷贝语义交付。

### 3.2 前置条件

- Stage A Pass  
- `/dev/p2p_device` + xds `.ko/.so` 可用  
- spill 文件所在 `bdev_name` 已配置  
- 对象 **不在 SpillBuffer**（仅文件 location）  
- AlignmentGate 通过（见 [alignment.md](./alignment.md)）

### 3.3 XDS 接口用法（冻结草案）

公开 API（`file_p2p/file_p2p_api.h`）：

```c
int new_p2p_fd(void);
void close_p2p_fd(int dev_fd);
int read_file(int dev_fd, struct read_parameter *param);
int read_file_batch(int dev_fd, struct read_parameter *params, int param_num);
int drain_read(int dev_fd);

struct read_parameter {
  const char *file_name;      // spill 文件路径
  const char *bdev_name;      // 对应 NVMe 块设备，如 /dev/nvme0n1
  unsigned long bdev_offset;  // 文件内逻辑偏移（= ObjectLocation.offset [+ readOffset]）
  unsigned short devid;
  unsigned short vfid;
  unsigned int size;          // 字节长度
  unsigned long addr;         // Worker Import 后的 HBM VA (+ destOffset)
};
```

#### 推荐调用约定（DataSystem Adapter）

```text
1. 打开一次会话（可按线程/请求复用，注意并发与 drain 语义）:
     p2p_fd = new_p2p_fd()

2. 填 read_parameter（单对象最小路径先用 read_file；批量再 batch）:
     file_name   = loc.path
     bdev_name   = FLAGS_nds_bdev_name   // 部署配置，禁止猜
     bdev_offset = loc.offset + readOffset
     size        = readSize
     addr        = importedLocalVa + destOffset
     devid/vfid  = 与 Register 时 device 一致

3. AlignmentGate(bdev_offset, size, addr) 失败 → 不调用 xds，fallback DRAM

4. rc = read_file(p2p_fd, &param)     // 或 read_file_batch
   if (rc) → 记 nds_io_error，fallback 或失败

5. rc = drain_read(p2p_fd)            // 等待提交的 IO 完成
   if (rc) → 同上

6. close_p2p_fd / 归还 fd 池
```

#### 必须钉死的语义

| 项 | 约定 |
|----|------|
| 谁调用 so | **Worker 进程**（`hostpid=getpid()` 要求） |
| `addr` | **仅** ImportByKey 得到的 Worker VA，禁止 Client 原始 VA |
| `bdev_offset`/`size`/`addr` | 满足 AlignmentGate；Phase-1 默认建议 4K（待 T0.1） |
| SpillBuffer | 不调用 xds（§3.2） |
| `nsid` | 跟 so 现状先为 1；若多 namespace 再配置化 |
| 并发 | 同一 `p2p_fd` 上 batch 未 drain 前的并发规则：先 **一请求一 fd** 或加锁，避免未定义行为 |
| 多文件 | Phase-1 单 object 单 `read_file`；跨文件 batch 后置 |

#### Adapter 伪接口（DataSystem 内部）

```text
Status NdsReadSpillToHbm(
    const ObjectLocation& loc,
    uint64_t readOffset, uint64_t readSize,
    void* importedHbmVa, uint64_t destOffset,
    int32_t deviceIdx);

// 内部: AlignmentGate → fill read_parameter → read_file → drain_read
```

对外不直接暴露 xds 类型，便于 mock（Stage A/UT）与替换。

### 3.4 Stage B 验证阶梯

| 阶梯 | 场景 | 验证 |
|------|------|------|
| B1 | 手工对齐文件 + 已知 HBM（Stage A mapping） | xds 读文件 pattern == HBM |
| B2 | Spill 写出**刻意对齐**的大对象 + Register 零拷贝 Get | e2e 直通 |
| B3 | 未 Register + 专用 comm + 拷贝 | 用户 data 正确 |
| B4 | SpillBuffer 未落盘 | **不走** xds，走 DRAM，metrics 可区分 |
| B5 | 故意不对齐 offset/addr | AlignmentGate 拦截，fallback，**零** xds ioctl |
| B6 | 无 `.ko` / ioctl 失败 | fallback 或明确错误，不崩溃 |
| B7 | Perf A/B | 同 object：DRAM reload vs NDS direct |

### 3.5 Stage B 可观测点

| 信号 | 说明 |
|------|------|
| `local_spill_hbm_direct` | 直通成功次数 |
| `local_spill_dram_reload` | 含 SpillBuffer / 主动 fallback |
| `direct_fallback_alignment` | 对齐拒绝（再拆 file vs hbm） |
| `direct_fallback_still_in_buffer` | 未落盘 |
| `direct_fallback_nds_error` | xds/ioctl/drain 失败 |
| `nds_read_issue_latency` / `nds_drain_latency` / `nds_e2e_latency` | 分段时延 |
| `nds_bytes_total` | 直通字节 |
| 日志 | `objectKey`, `path`, `file_off`, `len`, `hbm_va`, `align`, `mapping_id`, `rc` |

**Stage B Pass 标准（建议）**

1. B1–B3 功能正确；B4–B6 安全降级。  
2. 直通成功路径日志可证明调用了 xds（issue+drain），且未建不必要 DRAM 驻留（或策略文档写明）。  
3. 对齐失败路径 **零** NDS ioctl。  
4. Perf：相对 DRAM reload 有可重复收益（数字是否绑死 10MB/20ms 见 T0.4）。

---

## 4. 观测如何服务「先 A 后 B」

```text
Stage A 仪表盘： register/import 成功率和延迟、mapping 数、pattern 校验 ST
        ↓ green
Stage B 仪表盘： direct vs dram_reload 比率、fallback 原因饼图、nds 分段延迟、对齐拒绝率
```

联调排障顺序：

1. mapping 在不在？（A）  
2. 是否已落盘 / 是否对齐？（B 门禁）  
3. xds ioctl/drain 返回码？（B IO）  
4. Client 交付语义（零拷贝 vs 拷贝）？

---

## 5. 与 WBS 里程碑对齐

| 验证阶段 | 里程碑 | WBS |
|----------|--------|-----|
| Stage A | **M1** | W1 + W6 子集（IPC ST + H2D/D2H pattern） |
| XDS 用法冻结 + B1 | **M2** | W2 + W3 |
| B2–B6 e2e | **M3** | W4 + W5 |
| B7 Perf + 全量观测 | **M4** | W6 |

---

## 6. 已定验证细节（原待确认项）

| 项 | 决定 |
|----|------|
| Stage A 主证据 | Worker `aclrtMemcpy`/D2D 写 pattern + Client 校验；RH2D 可选对照 |
| XDS Phase-1 | 只封装 `read_file` + `drain_read`；`read_file_batch` 后置 |
| `p2p_fd` | 每请求 open/close；池化后置 |

详见 [decisions.md](./decisions.md) D11–D16。

---

## 7. 观测手段总表（Perf / Metrics / Trace）

完整梳理见 **[observability.md](./observability.md)**。摘要：

| 层 | 用途 | NDS 挂钩 |
|----|------|----------|
| **PerfKey** | 分段时延 | 复用 `WORKER_SPILL_*`、`CLIENT_MGET_H2D_*` 作 A/B；新增 `WORKER_NDS_*`、`HBM_IPC_*` |
| **Metrics / AccessRecorder** | 路径计数与 API | 复用 `DS_HETERO_CLIENT_MGETH2D` 等；新增 Register + direct/fallback 原因 |
| **Trace** | 单次请求排障 | 全路径带 TraceID + 稳定关键字 `nds_*` |

采集：`scripts/run_obs_smoke.sh` + workbench `gen_kv_perf_report.py` / `grep_get_latency_breakdown.sh`。
