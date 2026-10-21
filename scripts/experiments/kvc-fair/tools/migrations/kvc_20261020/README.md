# 2026-10-20 历史实现归档

本目录保存迁移前的完整控制器和一次性报告修补，供源码追溯及历史契约回归。新构建、资格、压测、收集和交付使用 `python3 -B -m kvcbench`，见工具 README。

- `workflow.py`、`batch_workflow.py`、`tcp_workflow.py` 等为旧编排，不作为当前部署资源。
- `product-smokes/` 为旧产品 smoke 和说明；当前真实节点验证通过显式 workflow gate/smoke 执行。
- `report-tools/` 保留历史模板、reader、定向网页更新与依赖声明；其固定历史路径、CAS 身份和叙事仅适用于原报告。
- 历史浏览器检查位于 `tests/acceptance/legacy/`；当前 W16/W18 使用 delivery 的参数化检查。

重放旧实验使用它自己的冻结源码包、配置及原始证据。当前运行目录只物化 run/entrypoints 的明确资源清单，不部署本目录；兼容格式解析器留在 kvcbench，不从本目录导入控制器。
