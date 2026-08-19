# DataSystem Trace 同 Worker 时间关联分析设计

## 目标

在现有 TopN 关键瓶颈页面中新增独立的“同 Worker 时间关联分析”章节，回答以下问题：

- QueryMeta 或 RemoteGet/BatchGet 失败是否集中在特定 Worker 和时间窗口；
- 失败前后是否同时出现 RPC 慢请求或 URMA 慢 WR；
- 同一 Worker 上元数据访问和数据访问分别呈现什么特征；
- 现有日志不能闭合因果关系时，明确保留为伴随现象或未观测，而不是强行归因。

本功能是 `scripts/ds_trace_bottleneck.py` 的后处理扩展，继续消费
`ds_trace_triage.py` 已生成的 run-directory 合同，不直接读取压缩包，也不成为第二套通用日志解析器。

## 用户口径

- Worker 选择器默认“全部 Worker”，支持单选一个 Worker。
- 选择器只联动新章节中的图、结论和 Trace 表；现有 TopN 总览保持全局基准。
- 时间聚合使用日志所在 Worker 的本地时间，粒度为 1 秒。
- 对 QueryMeta 或 RemoteGet/BatchGet 失败事件，关联同一 Worker 前后各 1 秒的伴随事件。
- 只有 `URMA_ELAPSED_TOTAL` 才构成一个已观测 WR；`total > 1.5ms` 为慢 WR，`total <= 1.5ms` 为正常 WR。
- 缺少 `URMA_ELAPSED_TOTAL` 时标记为“未观测”，不得归一为 0ms。
- `transferPath: UB` 只表示能力或路径标签，不是本次请求发生 UB 传输或产生 UB 耗时的证据。

## 方案选择

采用独立章节，而不是让 Worker 选择器过滤整个页面，也不为每个 Worker 生成独立 HTML。
这样既保留 TopN 全局基准，又能在一个页面内完成 Worker 横向切换和局部关联分析。

## 数据模型

分析 JSON 新增 `aggregate.worker_correlation`：

- `slow_wr_threshold_ms`: 固定为 `1.5`；
- `bucket_seconds`: 固定为 `1`；
- `neighbor_window_seconds`: 固定为 `1`；
- `workers`: Worker 名称、可证角色和总体计数；
- `time_buckets`: 按 Worker 和本地秒聚合的四维指标；
- `events`: 可下钻的失败、慢请求和慢 WR 事件；
- `summaries`: 全部 Worker及单 Worker的数据驱动结论素材。

每个事件只归属日志明确记录的 Worker，并记录 `worker_roles`。允许同一个 Worker 同时具有 Get Worker、
URMA 源 Worker、RPC 发起侧或数据提供侧角色，但不得仅凭服务名推断调用拓扑。

### 四个独立维度

1. RPC 通信：RPC 方法、失败状态、e2e、network residual、server queue 和 server exec。
2. UB 通信：WR 数、慢 WR 数、URMA total、completion wait、inflight WR 和 source-chip inflight。
3. 元数据访问：QueryMeta 请求数、失败数及 e2e；没有明确失败状态时不得从慢耗时推导失败。
4. 数据访问：本地命中、Local processing、RemoteGet/BatchGet 请求、失败、重试及可观测处理耗时。

四个维度并排展示，不把父窗口和子阶段相加，也不把 UB、RPC、元数据和数据访问压成一个互斥耗时模型。

## 时间关联

每条日志事件先按自身 Worker 的时间戳落入 1 秒桶。对 QueryMeta 或 RemoteGet/BatchGet 失败事件，
查找同 Worker 的 `[t-1s, t+1s]` 闭区间，生成伴随特征：

- 慢 WR 数和最大 URMA total；
- RPC 失败/慢请求数及最大 network/server 窗口；
- QueryMeta 失败/慢请求数；
- RemoteGet/BatchGet 失败、重试和本地处理事件数。

关联只表达“同 Worker、同期出现”。除非同一 Trace 或日志字段建立直接关系，否则文字不得写成因果关系。
不同 Worker 的时间戳不直接做亚秒因果排序，避免跨节点时钟偏差造成错误结论。

## 页面交互

新章节位于 URMA 分析和通用 Trace 明细之间，包含：

- Worker 单选框和当前 Worker 的日志角色说明；
- 动态结论区，区分“直接证据”“同期伴随”和“未观测”；
- 四张 1 秒时间图：RPC、UB、QueryMeta、数据访问；
- 关联 Trace 表：时间、Trace、Worker角色、四维摘要、前后 1 秒伴随事件、结论；
- 表格按 8 行分页，支持各列升降序和跳转现有 Trace 明细。

UB 图固定绘制 1.5ms 阈值线。慢 WR 使用醒目颜色，但正常 WR 和未观测状态必须可区分。
当选中 Worker 没有某一维度证据时，该图显示“未观测到对应证据”，不渲染零值趋势。

## 结论规则

- 同 Trace 同 Worker 的失败与慢阶段可以写为直接证据。
- 不同 Trace 只要落入前后 1 秒窗口，只能写为同期伴随。
- `transferPath: UB` 不增加 WR 数，也不触发慢 WR 结论。
- RPC 超时且 server breakdown 缺失时，写“RPC 截止点失败，服务端细分未观测”，不能写网络或处理为 0ms。
- ProcessGet 父窗口减去已知子阶段的差额继续标记为观测未细分，不转换成 CPU、锁或调度结论。
- 自动总结只从聚合数据生成，不保留针对当前 SAME case 的固定文字。

## 错误和缺失处理

- 无 Worker 证据的事件不参与单 Worker分析，记录在 `unassigned_event_count`。
- 无合法时间戳的事件不进入时间桶，记录在 `untimed_event_count`。
- 证据被 `ds_trace_triage` 截断时，在关联表和下载文本中继续显示截断数量。
- run 中无 URMA、无 QueryMeta 或无 RemoteGet 时，仍生成章节并明确对应维度未观测。

## 验证

Python 回归测试覆盖：

- `1.500ms` 为正常、`1.501ms` 为慢 WR；缺失 total 不计 WR；
- 同 Worker 前后 1 秒命中，超过窗口或不同 Worker 不关联；
- `transferPath: UB` 单独出现时不生成 UB 事件；
- QueryMeta、RPC、RemoteGet/BatchGet 和本地处理分别进入正确维度；
- 无 Worker/无时间/证据截断的降级行为。

浏览器契约测试覆盖：

- Worker 切换联动四图、结论和表格，但不改变全局 TopN；
- UB 图存在固定 1.5ms 阈值线；
- 表格 8 行分页、全数据排序和 Trace 下钻；
- 缺失维度显示未观测，而不是全零曲线。

focus case 生成后额外核对：慢 WR 总数、Worker 计数、QueryMeta/RemoteGet 失败事件及代表 Trace，
并用现有 Chrome/Playwright 检查自包含页面、ECharts、筛选、排序、分页和下载。

## 文档与技能

同步更新：

- `.skills/ds-trace-triage/SKILL.md`：记录 Worker 关联模式、1 秒窗口、固定慢 WR 阈值及证据边界；
- `../../playbooks/trace-bottleneck-analysis.md`：记录分析 JSON 和页面合同；
- `.skills/ds-trace-triage/agents/openai.yaml`：在主问题关键瓶颈场景提示 Worker 时间关联能力。

本功能不修改 DataSystem 运行时，不影响热路径、并发、持久化或恢复行为。
