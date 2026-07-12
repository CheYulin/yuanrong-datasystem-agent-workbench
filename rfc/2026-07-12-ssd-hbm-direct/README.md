# SSD Direct-to-HBM (NDS) Integration

**Status**: In-Progress（Track① Tasks 1–3 已落地；见 [pr-description.md](./pr-description.md) / [issue-rfc.md](./issue-rfc.md)）  
**Date**: 2026-07-12  
**Source repo**: `yuanrong-datasystem`  
**Workbench**: `yuanrong-datasystem-agent-workbench`

## 一句话目标

DataSystem 集成 NDS SSD→HBM 直通以降时延；**仅本机已落盘 spill**；统一 KV + Register；SSD **不**接 RH2D。

## Documents

| Document | Purpose |
|----------|---------|
| [decisions.md](./decisions.md) | **决策表**（默认已定 + 待实机验证项） |
| [open-questions.md](./open-questions.md) | 已关闭问题 + 仅实机升级项 V1–V4 |
| [design.md](./design.md) | 设计正文：范围、路径、组件 |
| [alignment.md](./alignment.md) | 对齐一等约束 |
| [verification-observability.md](./verification-observability.md) | Stage A→B；接口用法 |
| [observability.md](./observability.md) | **PerfKey / Metrics / Trace** 观测手段与拟增 key |
| [env-validation.md](./env-validation.md) | **无 NPU 如何推进**：L0/L1 编码+Fake；L2 实机 ST |
| [work-breakdown.md](./work-breakdown.md) | 三事项 WBS |
| [test-walkthrough.md](./test-walkthrough.md) | **用例复现**（Gate0 / UT / 将来 e2e） |
| [issue-rfc.md](./issue-rfc.md) | Issue 跟踪 + 验证快照 |
| [pr-description.md](./pr-description.md) | PR 描述模板 |
| [results.md](./results.md) | 夜间/验证日志 |
| [scripts/](./scripts/) | L1 binmock / L2 Stage A·B 脚本 + 人工清单 |
| [flow-analysis.md](./flow-analysis.md) | 现有 Spill/H2D 挂钩点 |
| [tech-brief-cann-ipc-hbm.md](./tech-brief-cann-ipc-hbm.md) | CANN IPC / Register |
| [tech-brief-xds-nds.md](./tech-brief-xds-nds.md) | xds/NDS |
| [product-architecture.md](./product-architecture.md) | 讨论稿映射 |
| [references.md](./references.md) | 链接索引 |

## 三事项拆解（主线）

| # | 事项 | 无 NPU | 有 NPU |
|---|------|--------|--------|
| **①** | DataSystem 模块与流程修改 | mock IPC + FakeNds，主路径先落地 | 换真 backend |
| **②** | NPU IPC 共享内存穿刺 | stub/契约 UT | Stage A（无 xds） |
| **③** | SSD→HBM 直通穿刺 | FakeNds 灌通 Get | Stage B（依赖 ②） |

① 通过稳定接口对接 ②/③；细节：[work-breakdown.md](./work-breakdown.md)、[env-validation.md](./env-validation.md)。

## Current Decision（摘要）

1. 本机已落盘 spill → HBM；SpillBuffer / 不对齐 / 无 xds → DRAM fallback。  
2. CANN IPC 跨进程；Register 零拷贝；未 Register 专用 comm + 拷贝（不暴露 comm）。  
3. 对齐默认 **4096**（可配置，下限 512）。  
4. XDS：Worker 调 `read_file` + `drain_read`；`addr`=Import VA；每请求 fd；batch 后置。  
5. 验证：**先 ②（IPC）再 ③（直通）**；Perf 先相对 A/B。  
6. Phase-1 不动写路径 spill；不写 pad。

## Next

- **Gate 0（skill 对齐）**：见 [scripts/BUILD_VERIFY.md](./scripts/BUILD_VERIFY.md)；入口 `prepare_build_and_st_xqyun.sh`（ds-build + ds-dev 流程，xqyun 隔离）。  
- 隔离编进行中 → 出 `ds_device_llt` 后跑 `HeteroD2H*`；过关再开 Task 1。  
- Worktree：`.worktrees/ssd-hbm-direct` ← `origin/master`。
