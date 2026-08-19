# PR 2081 多 NUMA Trace 专项分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用唯一 Trace 模型分析 0817 Core 错误、分档慢请求和 PR 2081 多 NUMA/chip 行为，生成自包含可视化页面与分享包。

**Architecture:** 先由未经修改的 `ds_trace_triage.py` 生成稳定 run contract，再由 `ds_trace_bottleneck.py` 生成互斥阶段模型。新的 NUMA 专项后处理器只读取这些 JSON 和归档成员名，合并 cohort 标签、错误链与 chip 指标，并渲染一个独立 HTML。

**Tech Stack:** Python 3 标准库、现有 ds-trace-triage / bottleneck JSON contract、ECharts 5.5.1、Node.js、Playwright/Edge。

## Global Constraints

- 固定 PR head `8e4aa6967fef67ced6228b478924b33d0e703835`，base `56b9e583097121fe7e48f388318a8417149ce8ef`。
- 不修改 `scripts/ds_trace_triage.py`，不复制基础 parser 正则。
- 同一 Trace 在 Core/慢日志目录重复出现时只计一次，cohort 标签保留多值。
- 缺失 RPC、URMA、chip、CPU、锁或调度字段保持未观测，不归一为零。
- 慢 WR 阈值严格使用 `URMA_ELAPSED_TOTAL > 1.5ms`。
- 当前单包只验证多 chip 功能与分布，不计算修改前后收益百分比。
- 输出本地自包含页面，不发布站点，不修改产品源码。

---

### Task 1: 唯一 Trace、cohort 与 NUMA 聚合模型

**Files:**
- Create: `scripts/ds_trace_numa_analysis.py`
- Test: `tests/scripts/test_ds_trace_numa_analysis.py`

**Interfaces:**
- Consumes: triage `summary.json`、bottleneck `bottleneck.analysis.json`、原始 tar.gz 成员名。
- Produces: `build_analysis(run_dir: Path, bottleneck_path: Path, archive_path: Path, source: dict) -> dict`。

- [ ] **Step 1: 写 cohort 去重和 chip 聚合失败测试**

构造一个 Trace 同时属于 `core/1004_DS` 与 `time/GET_10000_20000`，另构造 1010 PUT、双 chip、单 chip 和无 chip Trace，断言唯一 Trace 数、cohort 交集、chip1/chip2、偏斜率和严格慢 WR 阈值。

- [ ] **Step 2: 运行测试确认因模块不存在失败**

Run: `python3 -m pytest -q tests/scripts/test_ds_trace_numa_analysis.py`

Expected: FAIL，提示 `scripts/ds_trace_numa_analysis.py` 不存在。

- [ ] **Step 3: 实现最小聚合模型**

实现：

```python
def build_cohort_index(archive_path: Path) -> dict[str, set[str]]: ...
def build_trace_records(summary: dict, bottleneck: dict, cohorts: dict[str, set[str]]) -> list[dict]: ...
def build_aggregate(records: list[dict], source: dict) -> dict: ...
def build_analysis(run_dir: Path, bottleneck_path: Path, archive_path: Path, source: dict) -> dict: ...
```

Trace ID 从归档成员 basename 的 `;` 后缀取得；目录只决定 cohort。每条记录保留 GET/PUT、状态码、错误族、时延、Worker、URMA request、chunk、chip inflight、阶段与 evidence coverage。

- [ ] **Step 4: 运行聚合测试确认通过**

Run: `python3 -m pytest -q tests/scripts/test_ds_trace_numa_analysis.py`

Expected: PASS。

- [ ] **Step 5: 提交聚合模型**

```bash
git add scripts/ds_trace_numa_analysis.py tests/scripts/test_ds_trace_numa_analysis.py
git commit -m "feat: add NUMA trace analysis model"
```

### Task 2: 错误链、时延档与源码证据模型

**Files:**
- Modify: `scripts/ds_trace_numa_analysis.py`
- Modify: `tests/scripts/test_ds_trace_numa_analysis.py`

**Interfaces:**
- Consumes: Task 1 `records`，bottleneck 主阶段和 triage evidence/UB events。
- Produces: `error_families`、`latency_bands`、`chip_worker_time`、`source_chain`。

- [ ] **Step 1: 写 1004/1010 错误链失败测试**

断言 1004 GET 能关联 `URMA_WAIT_TIMEOUT → GetObjectRemote 无 UB payload/异常 TCP payload → Client 1004`；1010 PUT 能关联 Client URMA WRITE completion timeout。缺少 timeout 证据时只写“链路未闭合”。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `python3 -m pytest -q tests/scripts/test_ds_trace_numa_analysis.py -k 'error_chain or latency_band or source_chain'`

Expected: FAIL，缺少对应聚合字段。

- [ ] **Step 3: 实现错误链和源码链**

增加纯结构化函数：

```python
def classify_error_chain(record: dict) -> dict: ...
def summarize_latency_bands(records: list[dict]) -> list[dict]: ...
def build_source_chain(source: dict) -> list[dict]: ...
```

源码链记录 PR 配置、多 arena、NUMA bind、allocation NUMA、transmitted chip、source chip affinity 和 `srcChipInflight` 观测边界，不把单包分布写成带宽收益。

- [ ] **Step 4: 运行全文件测试确认通过**

Run: `python3 -m pytest -q tests/scripts/test_ds_trace_numa_analysis.py`

Expected: PASS。

- [ ] **Step 5: 提交诊断模型**

```bash
git add scripts/ds_trace_numa_analysis.py tests/scripts/test_ds_trace_numa_analysis.py
git commit -m "feat: classify NUMA trace error chains"
```

### Task 3: 自包含 ECharts 页面与交互契约

**Files:**
- Modify: `scripts/ds_trace_numa_analysis.py`
- Modify: `tests/scripts/test_ds_trace_numa_analysis.py`
- Create: `tests/scripts/check_ds_trace_numa_analysis.js`

**Interfaces:**
- Consumes: `build_analysis()` 返回的 JSON。
- Produces: `render_html(analysis: dict, echarts_source: str) -> str` 和 CLI `--output/--analysis-json`。

- [ ] **Step 1: 写 HTML 与 CLI 失败测试**

断言页面自包含数据/ECharts，含核心判断、错误族、时延档、chip、Worker/时间、TopN、Trace 明细、源码链、证据边界和下载按钮；不得包含 CDN。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest -q tests/scripts/test_ds_trace_numa_analysis.py -k 'render or cli'`

Expected: FAIL，缺少 renderer/CLI。

- [ ] **Step 3: 实现页面和命令行**

页面使用固定左导航和居中图表标题，表格 8 行分页、可排序；筛选维度为 cohort、GET/PUT、状态码、Worker、chip 模式和时延范围。Trace 明细突出异常耗时与 request/chunk/chip/inflight/timeout 证据。

- [ ] **Step 4: 实现浏览器契约检查**

`check_ds_trace_numa_analysis.js` 验证图表数、筛选、分页、排序、Trace 切换、下载、900px 响应式及控制台错误。

- [ ] **Step 5: 运行单元与 Node 语法测试**

```bash
python3 -m pytest -q tests/scripts/test_ds_trace_numa_analysis.py
node --check tests/scripts/check_ds_trace_numa_analysis.js
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交页面实现**

```bash
git add scripts/ds_trace_numa_analysis.py tests/scripts/test_ds_trace_numa_analysis.py tests/scripts/check_ds_trace_numa_analysis.js
git commit -m "feat: render NUMA trace diagnosis dashboard"
```

### Task 4: 全量解析、源码核对与真实页面生成

**Files:**
- Create outside repo: `<output-dir>/trace-0817-pr2081-numa-analysis-20260817/`

**Interfaces:**
- Consumes: 原始 tar.gz、PR 2081 worktree、Tasks 1–3 scripts。
- Produces: triage run、bottleneck JSON/HTML、NUMA 专项 JSON/HTML、README、工具快照。

- [ ] **Step 1: 验证基础 parser**

```bash
python3 scripts/ds_trace_triage.py verify
python3 -m pytest -q tests/scripts/test_ds_trace_triage.py tests/scripts/test_ds_trace_bottleneck.py
```

- [ ] **Step 2: 对完整 tar.gz 运行一次 triage**

使用 `--code-ref 8e4aa6967fef67ced6228b478924b33d0e703835`、明确 case/scenario 和独立输出目录；要求 `input_failures=0`。

- [ ] **Step 3: 生成全量 bottleneck 模型**

TopN 取唯一已观测 GET 数量，`local_cache=false` 仅在 direct-route/运行配置证据确认后传入，deadline 未知则不设置。

- [ ] **Step 4: 运行 NUMA 专项脚本**

输出 `index.html` 与 `data/numa.analysis.json`，复制 triage/bottleneck 页面和工具快照。

- [ ] **Step 5: 精确源码核对**

在 PR worktree 对 arena config、NUMA bind、allocation NUMA、transmitted chip 和 affinity source chip 逐段 `rg`/编号读取；将 exact paths/functions/commit 写入 README 和分析 JSON。

- [ ] **Step 6: 抽样回看真实日志**

至少核对 1004、1010、双 chip、单 chip、慢 WR、正常 Trace 各 3 条；记录不闭合链路而非推断。

### Task 5: 浏览器验证、自审与分享包

**Files:**
- Modify outside repo: 分析目录 README/manifest。
- Modify: `.skills/ds-trace-bottleneck-analysis/SKILL.md` only if the reusable invocation changed.

**Interfaces:**
- Consumes: Task 4 真实页面。
- Produces: 浏览器验证记录、tar.gz 分享包、SHA256。

- [ ] **Step 1: 运行完整回归**

```bash
python3 -m pytest -q tests/scripts/test_ds_trace_triage.py tests/scripts/test_ds_trace_bottleneck.py tests/scripts/test_ds_trace_numa_analysis.py
git diff --exit-code -- scripts/ds_trace_triage.py
git diff --check
```

- [ ] **Step 2: 用 Edge/Playwright 验证真实页面**

运行 `check_ds_trace_numa_analysis.js <index.html>`，要求无 JS 错误、筛选/分页/下载有效、900px 无横向溢出。

- [ ] **Step 3: 执行证据与敏感信息检查**

核对 archive member、cohort 文件数、唯一 Trace 数、错误交集和 JSON 重算；扫描私钥、访问令牌、Authorization header 与密码赋值模式，发现命中则停止打包。

- [ ] **Step 4: 打包并验证**

生成 `trace-0817-pr2081-numa-analysis-share-20260817.tar.gz`，执行 `gzip -t`、成员计数和 `sha256sum`。

- [ ] **Step 5: 运行 ds-self-verify 并提交必要的 skill 更新**

检查最新请求、diff、热路径/并发/持久化风险、测试、上下文更新；报告精确验证边界。
