# KVC 对比工具模块化重构设计

- **Status**: In-Progress
- **日期**: 2026-10-20
- **现状基线**: workbench `d5907ed041f44261bff82c99ac41bc96e2f0d18d`
- **范围**: `scripts/experiments/kvc-fair/` 及两个 KVC 实验 RFC 中与工具有关的代码和资源。
- **实施进度**: W01–W18 编排与验收回执已接入；P6 目录收敛及本地/干净包验收已通过；目标节点新布局 gate/smoke、最终 Coordinator 正式结果及公开交付仍在验收，见 [implementation.md](implementation.md)。

| 文档 | 内容 |
| --- | --- |
| [design.md](design.md) | 现状证据、五个主模块、目录、接口、生命周期、分阶段迁移与风险 |
| [workflow-acceptance.md](workflow-acceptance.md) | 每一步的输入、中间结果、自动验收、失败分支与复用规则，W01–W18 验收表 |
| [runtime-design.md](runtime-design.md) | 运行资源锁、环境准备与恢复账本、明确命令入口及验证边界 |
| [migration-map.csv](migration-map.csv) | 基线 142 个文件的职责、目标路径、迁移动作和阶段；复合脚本列出多个拆分目标 |
| [validation.md](validation.md) | 本次设计检查，以及后续实施必须通过的兼容性和复现验收 |
| [delivery-receipt-appendix.md](delivery-receipt-appendix.md) | 独立逐步回执附录、受控取消、网络适配和原始清单不变的重建配方 |
| [final-results.md](final-results.md) | Coordinator 最终矩阵、逐步实际回执/耗时与当前交付状态 |
| [implementation.md](implementation.md) | 实施阶段、实际命令/耗时/中间结果、未完成项与后续验收 |

设计的主线是 **构建 → 运行与观测 → 收集及离线核验 → 报告生成**。每一步遵循 **执行 → 中间产物落盘 → 独立验收 → 下游消费**，有可读摘要和机器可验收回执。发布网站单独执行，公共进程、锁、cgroup、路径与计时能力有明确归属。保留既有实验的原始输入、失败记录和证据来源，不用目录调整改写历史。

相关实验：[首次对比 RFC](../2026-10-20-kvc-fair-comparison/README.md)、[批量 TCP / pure-KV RFC](../2026-10-20-kvc-batch-tcp-comparison/README.md)。
