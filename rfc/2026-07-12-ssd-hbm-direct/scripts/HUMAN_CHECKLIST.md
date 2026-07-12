# Human checklist — NDS / HBM environments

Agent 改代码 + 准备脚本；**有 NPU / xds 的步骤由人在 L2 执行**，把日志贴回对话即可。

## 一次性

- [ ] 复制 `env.local.sh.example` → `env.local.sh`，填真实值（勿提交密钥）
- [ ] 确认 Agent/CI 能 L1（tiantiyun）rsync + 编译
- [ ] L2 NPU 节点：SSH、CANN、同机可起 worker+client（或双进程 ST）
- [ ] L2 NDS（可与 NPU 同机）：`/dev/p2p_device`、xds `.ko/.so`、`BDEV_NAME`

## 事项①（可无卡）

- [ ] L1 跑：`bash run_binmock_flow_st.sh`
- [ ] 期望：`NdsBinmockFlow*` PASS；`HeteroD2HTest.*` 回归 PASS
- [ ] 观测：`bash run_obs_smoke.sh <log>`（见 [../observability.md](../observability.md)）
- [ ] PR/结果标 `hardware-pending` 直至 ②/③ 绿

## 事项② Stage A（人工）

- [ ] L2：`bash check_env_device.sh`
- [ ] L2：`bash run_stage_a_npu.sh`
- [ ] 验收：双向 pattern 一致；保存日志路径给 Agent

## 事项③ Stage B（人工，② 之后）

- [ ] L2：`bash run_stage_b_nds.sh`
- [ ] 验收：B1 文件→HBM；B2 Get e2e；不对齐 fallback
- [ ] 异常属 V1–V4 → 升级讨论（见 `../decisions.md`）
