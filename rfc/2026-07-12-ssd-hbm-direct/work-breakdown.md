# Work Breakdown: SSD→HBM Direct (Phase-1)

**Date**: 2026-07-12  
**Status**: Draft（按用户建议收成 **三事项**；产品决策已收口）  
**Source**: [README](./README.md) / [decisions.md](./decisions.md) / [env-validation.md](./env-validation.md)

## 总览：三事项 + Mock 对接

```text
┌─────────────────────────────────────────────────────────────┐
│ ① DataSystem 模块与流程修改（主工程，可无 NPU）              │
│    Register/mapping · AlignmentGate · Get 分支 · metrics    │
│    依赖接口：IpcHbmBackend · NdsReadSpillToHbm               │
└───────────────┬─────────────────────────────┬───────────────┘
                │ mock / 真实现可切换           │
                ▼                             ▼
┌───────────────────────────┐   ┌─────────────────────────────┐
│ ② NPU IPC 共享内存穿刺    │   │ ③ SSD→HBM 直通穿刺           │
│    Stage A（无 xds）      │   │    Stage B（依赖 ② 已绿）    │
│    真机 L2 有 NPU         │   │    真机 L2 + xds + NVMe      │
└───────────────────────────┘   └─────────────────────────────┘
```

| 事项 | 内容 | 无 NPU | 有 NPU |
|------|------|--------|--------|
| **①** | 模块/流程/接口/分支/UT | **主战场**：mock IPC + FakeNds | 换真 backend 联调 |
| **②** | CANN IPC Export/Import + pattern | stub/mock 编译与契约 UT | **Stage A 穿刺** |
| **③** | xds `read_file`/`drain` → Import VA | FakeNds 灌通 Get 路径 | **Stage B 穿刺** |

原则：

1. **① 先按接口落地**，不阻塞在硬件上。  
2. **① 对接 ②/③** 只通过稳定接口；无卡用 mock，有卡换真实现，业务代码不改分支语义。  
3. **② 不绿不上 ③**（IPC 与直通问题隔离）。  
4. 环境分层见 [env-validation.md](./env-validation.md)。

---

## ① DataSystem 模块与流程修改

**目标**：产品路径在仓库内可编译、可测、可 fallback；硬件通过接口注入。

### 接口（① 与 ②/③ 的接缝）

```text
// ② 提供（或 MockIpcHbm）
Status ExportKey / SetImportPid / ImportByKey / Close
Status RegisterHbmBuffer RPC → ImportedHbmMapping{localVa, size, device}

// ③ 提供（或 FakeNdsAdapter）
Status NdsReadSpillToHbm(loc, readOff, readSize, importedVa, destOff, deviceIdx)
```

| ID | 事项 | 依赖 | 验收（无 NPU 即可） |
|----|------|------|---------------------|
| T1.1 | Client：`RegisterHbmBuffer` / `Unregister` API + RPC | — | API/proto 齐；mock backend 可跑 |
| T1.2 | Worker：`ImportedHbmMapping` 表（生命周期、refcount、in-flight） | T1.1 | UT：注册/注销/拒注销 |
| T1.3 | 未 Register：EnsureCommBuffer → 同套 Register → 交付前 **拷到 user** | T1.1–1.2 | 不暴露 comm；内容靠 mock 写 pattern |
| T1.4 | `AlignmentGate`（offset/length/addr，默认 4K） | D8 | 纯函数 UT 矩阵 |
| T1.5 | Eligibility：local + IsSpilled + 已落盘 + mapping + align | T1.2, T1.4 | 表驱动 UT |
| T1.6 | SpillBuffer 仍在 → **禁 NDS，DRAM** | — | UT |
| T1.7 | Get/MGet 旁路：eligible → `NdsReadSpillToHbm`；失败 → `LoadSpilledObjectToMemory` | T1.5, 接口③ | FakeNds e2e；不改 RH2D 禁令 |
| T1.8 | Metrics：direct / dram_reload / fallback{align,buffer,nds,ipc} | T1.7 | 日志/指标可 grep |
| T1.9 | 编译开关：无 CANN/xds 链 mock；有则选真 | — | L1 tiantiyun 编过 |

**挂钩点**（实现时对照 [flow-analysis.md](./flow-analysis.md)）：Get 本地 spilled、`KeepObjectDataInMemory` 旁路；**不改** `LoadPayloadAndFillResponse` 对 spilled RH2D 的禁止。

**① Done（无卡）**：分支 + Fake/Mock UT 绿；L1 编译过；文档标明 `hardware-pending`。

---

## ② NPU IPC 共享内存穿刺验证

**目标**：证明 Client Export → Worker Import 后 **同一物理 HBM** 可双向访问（**不用 xds**）。

| ID | 事项 | 环境 | 验收 |
|----|------|------|------|
| T2.1 | 真 CANN IPC 封装（实现 ① 的 IpcHbmBackend） | L2 NPU | Import 得 localVa |
| T2.2 | Stage A：Worker `aclrtMemcpy`/D2D 写 pattern ↔ Client 校验 | L2 | pattern 100% 一致 |
| T2.3 | 反向：Client 先写 → Worker 从 localVa 读 | L2 | 双向一致 |
| T2.4 | 生命周期 / Unregister + in-flight | L2 | 无 UAF；错误码明确 |
| T2.5 | 零拷贝 Register 与 comm+拷贝 两条都穿刺 | L2 | 与 ① 语义一致 |

细则：[verification-observability.md](./verification-observability.md) §2、[tech-brief-cann-ipc-hbm.md](./tech-brief-cann-ipc-hbm.md)。

**② Done**：Stage A Pass；① 可把 MockIpc 换成真 backend 而不改 Get 分支。

无 NPU 时：只保留 **MockIpcHbm**（host 侧假 VA / 或编译桩），**不宣称 ② 绿**。

---

## ③ SSD→HBM 直通穿刺验证

**目标**：已落盘文件 → AlignmentGate → xds → Worker `importedVa`；再挂上 ① 的 Get。

| ID | 事项 | 环境 | 验收 |
|----|------|------|------|
| T3.1 | 真 XdsAdapter：`read_file` + `drain_read`；addr=Import VA | L2+NDS | 单文件读通 |
| T3.2 | bdev/nsid 配置；每请求 `p2p_fd` | L2 | 非猜盘 |
| T3.3 | Stage B1：对齐测试文件 → 已知 Import HBM（可先不经 Get） | L2 | 文件 pattern == HBM |
| T3.4 | Stage B2：① Get e2e（Register 零拷贝 / 未 Register 拷贝） | L2 | 与 Fake 路径同契约 |
| T3.5 | Fallback 负例：不对齐、SpillBuffer、无 xds | L2 / L1 | 安全回 DRAM |
| T3.6 | Perf 相对 A/B（DRAM reload vs NDS） | L2 | 数字进 results；不绑死正式 SLO |

细则：[verification-observability.md](./verification-observability.md) §3、[tech-brief-xds-nds.md](./tech-brief-xds-nds.md)。依赖：**② Pass** + V1–V4 实机。

无 NPU/无 xds 时：只用 **FakeNdsAdapter**（读文件→可选 H2D 或 host 模拟）把 ① 灌通，**不宣称 ③ 绿**。

---

## 并行与顺序

```text
     ┌──► ② IPC 穿刺（等 L2 NPU） ────────┐
① ──┤                                     ├──► ①↔②↔③ 联调 e2e
     └──► ③ 直通穿刺（等 ② + L2 NDS） ────┘

① 可立即开写（mock）；②∥③ 有节点再穿刺；③ 严格晚于 ②。
```

| 里程碑 | 含义 |
|--------|------|
| **M-①** | 模块/流程 + Mock 接口 UT（无卡） |
| **M-②** | Stage A 穿刺 Pass |
| **M-③** | Stage B 穿刺 + Get 真机 e2e |
| **M-观测** | metrics 齐全 + Perf A/B（挂在 ③ 后） |

---

## Phase-1 明确不做

| 不做 | 说明 |
|------|------|
| SSD / spilled → RH2D | 已决禁止 |
| 跨机 SSD→HBM | 无跨机 NVMe P2P |
| VMM ShareableHandle | IPC only |
| 暴露专用 comm | 已决 |
| 读路径强制 flush SpillBuffer | 走 DRAM |
| spill 写 pad / HBM→SSD 大改 | 后置 |
| xds 内做 KV | IO only |

---

## Next

1. **先开 ①**：接口 + AlignmentGate + Get 分支 + MockIpc / FakeNds UT。  
2. L2 节点信息到位后开 **②**；② 绿再开 **③**。  
3. 实机异常只升级 [decisions.md](./decisions.md) **V1–V4**。
