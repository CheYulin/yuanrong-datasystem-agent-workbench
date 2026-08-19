# PR 2081 多 NUMA Trace 专项分析设计

## 目标

分析 `trace_0817_src_balanced_dst_affinity.tar.gz` 中的 Core 错误 Trace 与分档慢 Trace，结合 PR 2081 精确源码，生成一个可离线打开的证据保留型可视化页面。报告需要回答：

1. `K_URMA_ERROR(1004)` 与 `K_URMA_WAIT_TIMEOUT(1010)` 分别由什么错误链产生；
2. GET/PUT 慢请求的主要瓶颈阶段是什么；
3. Client 多 NUMA 接收内存是否让 chip 1/2 都被实际使用，负载是否均衡；
4. chip 使用、URMA 时延、超时、Worker 与时间之间有什么相关关系；
5. 哪些结论只能由当前样本证明，哪些收益仍需要修改前基线或吞吐数据验证。

## 固定输入与源码

- Trace 包：`trace_0817_src_balanced_dst_affinity.tar.gz`。
- PR：GitCode `openeuler/yuanrong-datasystem#2081`。
- PR head：`8e4aa6967fef67ced6228b478924b33d0e703835`。
- PR base：`56b9e583097121fe7e48f388318a8417149ce8ef`。
- PR 标题：`perf(common): UB 通信内存池按 NUMA 多 arena 均匀分布`。
- 运行语义：以 Trace 内 `client.process.direct_*`、transport、URMA、Worker 与错误字段为准；文件名只提供 Core/时延档采集意图。

## 数据模型

### 唯一 Trace 层

整个压缩包只运行一次 `ds_trace_triage.py`。以 Trace ID 为唯一键合并同一 Trace 在 `core/` 和 `time/` 中的重复副本，避免错误数、时延数和阶段数重复计算。

每条 Trace 保留多值 cohort 标签：

- `core/1004_DS`；
- `core/1010_DS`；
- `time/GET_5000_7000`；
- `time/GET_7000_10000`；
- `time/GET_10000_20000`；
- `time/GET_20000`；
- 对应 PUT 时延档。

报告同时显示文件数、唯一 Trace 数和 cohort 交集，禁止把目录文件数直接当独立问题数。

### 阶段与错误层

复用 `ds_trace_bottleneck.py` 的缺失证据语义与互斥阶段模型。GET 至少区分：

- Client 路由与 QueryMeta；
- RPC 网络与服务端处理；
- URMA/UB 数据传输；
- 数据访问父窗口但内部未细分；
- 未解释残差。

PUT 使用原始 `latencySummary` 与 URMA WRITE 事件，不强行套用 GET 阶段模型。1004/1010 通过状态码、错误文本、WR request ID、chunk index/count、timeout、fallback/empty payload 和 Client access status 串联。

### NUMA/chip 层

从 `URMA_ELAPSED_TOTAL` 与 timeout 证据提取：

- `srcChipInflight` 的 chip 1、chip 2 计数；
- 单 chip / 双 chip / 未观测 Trace；
- `abs(chip1-chip2)/(chip1+chip2)` 偏斜率；
- URMA total/wait/completion observation；
- WR 数、慢 WR（严格 `>1.5ms`）、timeout；
- Worker、源/目标、CPU、时间与 GET/PUT 方向。

`srcChipInflight` 是发送侧当前 inflight 分布。结合 PR 2081 的 NUMA arena 与 transmitted chip 路径，它可证明 chip 选择结果，但不能单独证明接收端实际内存带宽或端到端吞吐提升。

## 源码解释边界

源码章节展示以下链路：

1. `DATASYSTEM_UB_TRANSPORT_ARENA_NUM` / `ub_transport_arena_num` 配置；
2. Client transport pool 拆为等大 arena；
3. arena 内存按发现的 NUMA node 轮转绑定；
4. ArenaGroup 在多个 arena 间分配；
5. allocation NUMA/chip 信息随 UB 描述传递；
6. 发送端据 transmitted chip 选择 affinity source chip；
7. Trace 中通过 `srcChipInflight` 观察实际选择结果。

页面明确区分：

- 功能激活：chip 1/2 均有证据；
- 分布质量：双 chip 覆盖率、偏斜与 Worker 集中度；
- 性能收益：需要修改前同配置基线或吞吐/带宽指标，当前单包不计算提升百分比。

## 页面结构

生成一个自包含 ECharts HTML：

1. 核心判断与证据边界；
2. 样本与 cohort 交集；
3. Core 1004/1010 错误族及时间序列；
4. GET/PUT 时延档与关键瓶颈；
5. chip 1/2 使用、双 chip 覆盖率与偏斜；
6. chip × Worker × 时间 × URMA 时延关联；
7. TopN 关键 Trace 表；
8. 单条 Trace 阶段、错误链和证据日志；
9. PR 2081 源码链与限制。

所有长表分页、列排序、筛选；表格在窄屏下自适应，不依赖横向拖动。页面支持按 cohort、GET/PUT、状态码、Worker、chip 模式和时延范围过滤，并下载当前筛选 Trace。

## 实现边界

- 不修改 `scripts/ds_trace_triage.py`。
- 新专项脚本只读取 triage run contract 与归档成员名，不复制基础日志正则。
- 若 PR 2081 引入的新字段已被 triage 原样保留，则在后置层解析；只有基础 parser 丢失关键证据时才单独提出 parser 扩展，不在本任务中静默改写。
- 输出只写到独立分析目录，不发布站点、不修改产品源码。

## 验证

1. `ds_trace_triage.py verify` 与相关 pytest；
2. archive 文件数、cohort 成员与唯一 Trace 数交叉核对；
3. 1004、1010、双 chip、单 chip、慢 WR、正常 Trace 各抽样回看原始日志；
4. 所有聚合值可由结构化 JSON 重算；
5. Node 语法检查与 Edge/Playwright 页面检查；
6. 900px 视口无页面和表格横向溢出；
7. 分享包完整性、敏感信息扫描与 SHA256。

## 首页入口与运行配置补充

首页同时承担分享包入口职责，在首屏提供三个清晰入口：当前 NUMA 专项分析、`bottleneck.html`、`triage.html`。链接使用相对路径，使解压后的离线目录可以直接跳转，不依赖 HTTP 服务。

运行配置由专项脚本参数化写入分析 JSON 与首屏，不手工修改生成后的 HTML。本次配置为：

- 单节点目标 QPS：315；
- Client 数：8；
- 每 Client 线程数：16；
- 每节点 Worker 数：1。

页面可展示由配置直接推导的单 Client 平均 QPS `315 / 8 = 39.375` 与单节点 Client 总线程数 `8 × 16 = 128`，并明确它们是配置口径，不是从 Trace 反推的实测吞吐。CLI 对这些字段提供可选参数；字段未提供时显示“未配置”，不得沿用本 case 的固定值。

验证增加：首页三个入口存在且目标文件有效；结构化 JSON 保存四个原始配置字段；浏览器断言本 case 的 315、8、16、1 及两个推导值均正确渲染。
