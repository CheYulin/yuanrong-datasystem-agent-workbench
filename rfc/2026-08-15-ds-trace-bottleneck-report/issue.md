# RFC：增强 DataSystem Trace 的 TopN、多 Runs 与读写瓶颈分析

## 背景

`ds-trace-triage` 已能够确定性地完成压缩包读取、Trace 聚合和 RPC/URMA 字段归一化，但复杂尾延迟分析仍容易依赖单批数据的一次性脚本。不同运行还会出现 Trace ID 形态、数据通路、对象大小、QPS、Client 数和线程数变化，导致报告口径漂移。

需要在不破坏基础 triage 的前提下，固化一个可复用的后置分析器，输出单 Run 详细页面、多 Runs 控制变量总览和离线可分享的 ECharts 报告。

## 目标

- 支持 Top100、Top1000、任意 TopN 和预筛选异常集合的全量分析。
- 每个 Run 独立生成 triage、bottleneck 和 NUMA 页面，禁止跨 Run 合并 Trace。
- 读取与写入使用独立 TopN 和互斥 Breakdown。
- 缺失证据保持“未观测”或“未解释残差”，不把缺失值当作 0，也不猜测网络、CPU、锁或调度根因。
- 页面支持时间序列 Stacked Bars、问题数量与耗时分离、Worker/WR 分析、筛选、排序、分页和 Trace 证据下载。

## 方案

### 处理流水线

```text
trace bundles
    -> ds_trace_triage.py
    -> normalized run directory
    -> ds_trace_bottleneck.py
    -> ds_trace_numa_analysis.py
    -> optional multi-run suite
```

`ds_trace_triage.py` 是唯一的原始日志解析入口。后置脚本只消费 run-directory 中间结果，不重新打开或发明另一套原始日志解析规则。

### 读取 Breakdown

- URMA 建链耗时
- URMA 通信耗时
- QueryAndGet 其他业务处理耗时
- Get 其他业务处理耗时
- 明确的调度和线程等待
- RPC 通信残差
- RPC 框架
- 未解释残差

所有阶段互斥，合计不得超过 Client 总时延。`transportType:UB` 只证明选择了 UB 通路；缺少显式 URMA elapsed/timeout 证据时，精确 URMA 耗时保持未观测。

### 写入 Breakdown

- Create RPC 其他处理
- 写入 MemoryCopy
- 写入 URMA 通信
- Publish RPC 其他处理
- Worker Publish/元数据
- 明确的调度和线程等待
- RPC 通信残差
- RPC 框架
- 未解释残差

Create 与 Publish 分别拆解；优先使用包含重试的 total 字段。URMA 位于 MemoryCopy 父窗口内时不得重复累计。失败或不完整 RPC timing 不作为网络、框架或 handler 的确定证据。

### 拓扑和多 Runs

`local_cache=false` 按当前源码解释为 Client 查询 Meta Owner 后直接访问 Data Worker，不虚构固定 Entry Worker 或 Worker 间互拉。多 Runs 只在控制变量匹配时比较实现方式、QPS、对象大小、Client 数和线程数；文件名仅作为实验意图，最终以日志证据为准。

## 交付范围

| 路径 | 责任 |
| --- | --- |
| `scripts/ds_trace_triage.py` | 通用解析与归一化 |
| `scripts/ds_trace_bottleneck.py` | 单 Run 读写 TopN 与离线页面 |
| `scripts/ds_trace_bottleneck_suite.py` | 多 Runs 控制变量总览 |
| `scripts/ds_trace_numa_analysis.py` | WR、Inflight、source chip 与 NUMA 证据 |
| `.skills/ds-trace-bottleneck-analysis/SKILL.md` | 主问题分析请求的使用合同 |

详细设计保存在 `yuanrong-datasystem-agent-workbench/rfc/2026-08-15-ds-trace-bottleneck-report/`。实现由 PR 2165 跟踪。

## 验收标准

- 同一 Trace 的 Breakdown 阶段和不超过 Client 总时延。
- 读取和写入不复用错误的父窗口，且各自独立选择 TopN。
- QueryAndGet inline UB、URMA timeout、RPC residual/framework 和写入 Create/Publish 均有回归测试。
- Trace ID、缺失阶段、失败 RPC、不同 local-cache 模式和多 Runs 隔离均有降级或边界测试。
- HTML 为自包含离线页面，关键表格支持自适应、排序和分页。
- RFC、Issue、PR 描述不包含真实服务器地址、凭据或本地绝对路径。

## 风险边界

该功能是离线诊断工具，不进入 DataSystem 运行时热路径，不改变 RPC 协议、数据一致性、并发共享状态或持久化格式。分析结论受输入日志和部署版本限制；缺失证据必须显式呈现。

## 关联

- 实现：PR 2165
- RFC：`yuanrong-datasystem-agent-workbench/rfc/2026-08-15-ds-trace-bottleneck-report/`
