# 设计：ds-trace-triage 后置关键瓶颈分析器

## 1. 背景

`ds-trace-triage` 已提供输入解包、Trace 聚合、RPC/URMA 字段、时间/Worker/flow 维度和本地报告。复杂尾延迟批次仍需要进一步完成 TopN 互斥阶段归因、主问题统计、时间序列 stacked bars、Worker/URMA 批量分析和逐 Trace 证据结论。过去这部分容易演变为针对单批数据的临时脚本，结论和页面难以复用。

## 2. 架构

```text
trace bundles
    │
    ▼
ds_trace_triage.py
    │ manifest.json / summary.json / triage.json
    │ parsed_traces.json / events.jsonl
    ▼
ds_trace_bottleneck.py
    ├── BottleneckAnalyzer
    ├── bottleneck.analysis.json
    └── bottleneck.local.html
```

基础 triage 是唯一压缩包与日志分组入口。后置脚本不得打开原始 gzip/tar；它可以读取 triage 已归入 Trace 的结构化字段和 evidence，并补充面向诊断的语义规则。

## 3. 输入输出

命令：

```bash
python3 scripts/ds_trace_bottleneck.py \
  --run-dir <triage-run-dir> \
  --top 100 \
  --output <triage-run-dir>/bottleneck.local.html
```

必需输入：`manifest.json`、`summary.json`、`triage.json`。`parsed_traces.json` 和 `events.jsonl` 是逐 Trace 深挖的可选增强；缺失时仍生成聚合页，但在证据覆盖中明确降级。

输出：

- `bottleneck.analysis.json`：页面使用的稳定派生模型。
- `bottleneck.local.html`：内联数据和 ECharts 的离线报告。

默认拒绝覆盖已有输出；只有 `--force` 可以覆盖。脚本不自动发布。

## 4. 分析口径

### 4.1 TopN

按 Trace ID 和 evidence 正文去重。优先使用 Client access latency，缺失时才使用明确标注的 Worker 口径。按总时延降序选择 TopN，以时间和 Trace ID 稳定打破并列。

### 4.2 六个互斥阶段

- RPC 网络/框架 residual
- QueryMeta/metadata
- URMA/UB completion
- 远端供数非 URMA
- 直连 Data Worker 本地/内部未细分
- 未解释残差

父窗口与子阶段不能重复相加；阶段之和不得超过用户可见总时延。主问题取最大阶段。证据不足时只能落入“内部未细分”或“未解释残差”，不能推断为网络、CPU、锁或线程调度。

### 4.3 场景规则

- RPC：区分 server queue/exec、framework 和 network residual。
- URMA：比较 total、completion wait、wake、poll/notify/thread scheduling、Inflight WR、RemoteGet WR、source chip 和源→目标边。
- GET：使用 triage 提供的 Client/Worker access、ProcessGet、BatchGet、QueryMeta、Local processing、RemotePull 证据。
- 非 RPC/非 UB：只有显式证据时才细分本地处理、BatchGet 超时/重试、供数端处理和 deadline 观测空窗。

分析层输出事实、派生结论、证据强度、缺失字段与下一步；浏览器 JavaScript 只负责交互，不重新发明结论。

## 5. 页面契约

1. 总览与核心判断。
2. 主问题 Trace 数和对应阶段时延两个图。
3. 独立的关键阶段耗时占比。
4. TopN 时间序列 stacked bars 和 deadline。
5. 有 URMA 证据时展示 WR 时间、Worker、边、Inflight 和逐 Trace 请求。
6. 非 RPC/非 UB 的证据分层、分类、代表 Trace 和逐条结论。
7. 按日志可证角色展示 Worker，不假设 `local cache=false` 存在固定 Entry Worker。
8. Trace 筛选、排序、8 行分页、阶段明细、重点日志和完整日志展开。
9. 输入/ref/缺失面与源码校正边界。

下载支持 TopN 全量、当前筛选/分类和单条 Trace。异常高亮只作用于关键词与具体耗时 token，避免整段铺红。

## 6. 失败与降级

| 条件 | 行为 |
| --- | --- |
| 必需 JSON 缺失或损坏 | 非零退出并列出文件 |
| 可选逐 Trace 文件缺失 | 生成聚合报告并标记能力缺口 |
| Client latency 缺失 | 显式使用 Worker 口径或排除该 Trace |
| 无 RPC/URMA 字段 | 隐藏对应分析章并显示“本次无证据” |
| 阶段和超过总时延 | 按优先级裁剪并保留父窗口原值 |
| 输出已存在 | 拒绝覆盖，除非 `--force` |

## 7. 验证

- RED/GREEN pytest：输入契约、TopN、互斥阶段、降级、CLI、转义和 HTML 合约。
- Playwright/Chrome：ECharts、排序后分页、筛选、Trace 联动、日志渐进展开和真实下载。
- SAME 回归：使用同一 triage 中间结果生成 Top100，核对稳定统计与证据覆盖，不比较写死文案。
- 基础回归：`python3 scripts/ds_trace_triage.py verify` 与原有 pytest。
- PR 前运行 `$ds-self-verify` 和独立代码评审。

## 8. 性能、安全与兼容

这是离线工具，不进入 DataSystem 运行时热路径，不涉及并发共享状态、持久化格式或恢复协议。HTML 转义所有标题、Worker、Trace 和日志文本；不记录私有节点地址到 RFC/PR。依赖限制为 Python 标准库和仓库内 ECharts。

首版消费当前 run-directory schema；不兼容时明确失败，不静默猜测。新日志格式应先进入 `ds_trace_triage.py` 的 ParserRules/归一化字段和测试，再由后置脚本消费。

## 9. 发布与回滚

脚本默认只生成本地报告。需要站点发布时仍使用 `ds-trace-triage` 的 dry-run、HTML 尺寸门禁和目录注册流程。

回滚只需撤销 DataSystem PR；基础 `ds_trace_triage.py run`、`report.local.html` 和原 skill 流程保持可用。新文件没有运行时状态或数据迁移。
