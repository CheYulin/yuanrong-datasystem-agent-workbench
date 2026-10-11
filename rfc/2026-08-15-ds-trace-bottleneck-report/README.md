# DataSystem Trace 主问题关键瓶颈报告

- **Status:** In-Progress
- **Date:** 2026-08-15
- **Owner:** `yuanrong-datasystem/.skills/ds-trace-triage`
- **Implementation:** `yuanrong-datasystem` 的 `scripts/ds_trace_bottleneck.py`

## 目标

在 `ds-trace-triage` 确定性预处理之后，提供通用的 TopN 主问题关键瓶颈分析与自包含 ECharts 页面。用户要求“主问题、关键瓶颈、Top100、stacked bars、Worker/RPC/URMA breakdown”时，不再临时复制一次性解析和页面代码。

## 范围

- `scripts/ds_trace_triage.py` 继续负责压缩包读取、原始日志解析、Trace 聚合和 run-directory。
- 新脚本只消费 run-directory 中间结果，输出 `bottleneck.analysis.json` 与 `bottleneck.local.html`。
- 当前 SAME Top100 页面作为完整验收样例，但数量、Trace ID、Worker、时间和结论不能写死。
- workbench 仅保存 RFC；脚本、测试和产品 skill 都落在 `yuanrong-datasystem`。

## 代码落点

| 仓库路径 | 责任 |
| --- | --- |
| `scripts/ds_trace_bottleneck.py` | TopN、互斥阶段、聚合与 HTML 渲染 |
| `tests/scripts/test_ds_trace_bottleneck.py` | 分析、降级、CLI 与 HTML 合约 |
| `tests/scripts/check_ds_trace_bottleneck.js` | Chrome 交互与下载验证 |
| `.skills/ds-trace-triage/SKILL.md` | 请求路由和两阶段工作流 |
| `.repo_context/playbooks/operations/trace-bottleneck-analysis.md` | 长期责任边界与验证入口 |

## 状态推进

1. RFC 直接进入 workbench `master`。
2. DataSystem 在 fork 分支实现并完成自验证。
3. DataSystem PR 引用本 RFC 和实际验证结果。
4. PR 合入后把本 RFC 状态更新为 `Done`。

详细方案见 [design.md](design.md)。
