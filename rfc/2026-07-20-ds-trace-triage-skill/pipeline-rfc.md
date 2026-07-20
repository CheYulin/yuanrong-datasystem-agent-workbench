# DataSystem Trace Triage Pipeline RFC

## 背景

过去 8 个定位定界会话已经证明：DataSystem 慢时延和错误 trace 分析不能只依赖一次性 HTML 脚本。稳定流程应该把原始日志、结构化事件、聚合统计、定位定界结论和最终报告分开保存，让人工和 Codex 都能复用同一套中间产物。

本 RFC 定义一个更强、更结构化的 `ds_trace_triage.py` pipeline。它吸纳历史会话里的有效脚本经验，但只暴露两个日常入口：

```bash
python3 scripts/ds_trace_triage.py run <input...> --out <workspace> --case <case-name>
python3 scripts/ds_trace_triage.py verify
```

## 目标

- 保留原始输入包和解压后的 trace 日志，后续可以追溯每条结论来自哪一行。
- 按固定阶段产出中间文件，避免解析、聚合、定位和渲染耦合。
- 支持时间化 run 目录，区分不同次 runs。
- 相同输入、相同脚本版本、相同 `code_ref` 和相同 case/scenario 参数时只生成一次结果。
- `verify` 用内置 fixture 跑完整链路，后续能进入 CI。
- 渲染层只消费中间产物，不重新解析原始日志。

## 非目标

- 不把历史 HTML 页面脚本直接拼接进主脚本。
- 不在第一版实现完整 yche 发布自动化。
- 不在 parser 阶段替代源码/CodeGraph 人工推理。
- 不发布真实生产日志；可发布的 fixture 必须是脱敏或合成数据。

## 用户入口

### run

`run` 是生产分析入口，负责完整执行四个阶段：

```text
原始日志 / gz / tar.gz
  -> 1. 解析日志
  -> 2. 聚合分析
  -> 3. 定位定界
  -> 4. 渲染报告
```

`run` 支持多个输入，输入可以混合普通日志文件、目录、`.log.gz` 和 gzip-tar：

```bash
python3 scripts/ds_trace_triage.py run \
  b317726ce0534288aa832cfbf4340a43.gz \
  3684dca4e6964ab1ace2783dbeb30cfb.gz \
  /data/ds-extra-traces/ \
  --out /tmp/ds-triage \
  --case multi-round-get-failure \
  --scenario "GET 失败 / 多轮 trace / 20ms deadline" \
  --code-ref "$(git rev-parse main/master)"
```

### verify

`verify` 是自验证入口，使用内置 fixture 跑完整链路，并断言关键字段和产物都存在：

```bash
python3 scripts/ds_trace_triage.py verify
```

CI 可直接执行：

```bash
python3 scripts/ds_trace_triage.py verify
python3 -m pytest -s tests/scripts/test_ds_trace_triage.py -q
```

## Run 目录结构

所有结果写入一个时间化 run 目录。默认 run id 由脚本生成：

```text
YYYYMMDD-HHMMSS-<case-slug>
```

目录结构：

```text
<out>/
  runs/
    20260720-021530-round2-isolated-port/
      manifest.json
      raw/
        inputs/
          b317726ce0534288aa832cfbf4340a43.gz
        extracted/
          <trace-id-or-member>/
            worker.log
            ds_client_access_0.log
      events.jsonl
      summary.json
      triage.json
      triage.md
      report.html
```

## manifest.json

`manifest.json` 是 run 的索引和可追溯入口：

```json
{
  "schema_version": 1,
  "case_name": "round2-isolated-port",
  "scenario": "GET 失败 / 硬件端口隔离后 / 20ms deadline",
  "run_id": "20260720-021530-round2-isolated-port",
  "analysis_created_at": "2026-07-20T02:15:30+08:00",
  "code_ref": "main/master@a7130ac9c3171bf3acb70601c7de99f7bc24f25a",
  "script_version": "sha256:<scripts/ds_trace_triage.py hash>",
  "cache_key": "<stable sha256>",
  "trace_time_range": {
    "first_ts": "2026-07-18T21:09:16.331000",
    "last_ts": "2026-07-18T21:11:44.506000"
  },
  "inputs": [
    {
      "original_path": "b317726ce0534288aa832cfbf4340a43.gz",
      "stored_path": "raw/inputs/b317726ce0534288aa832cfbf4340a43.gz",
      "type": "gzip-tar",
      "sha256": "...",
      "size_bytes": 123456,
      "extracted_to": "raw/extracted/b317726ce0534288aa832cfbf4340a43"
    },
    {
      "original_path": "3684dca4e6964ab1ace2783dbeb30cfb.gz",
      "stored_path": "raw/inputs/3684dca4e6964ab1ace2783dbeb30cfb.gz",
      "type": "gzip-tar",
      "sha256": "...",
      "size_bytes": 234567,
      "extracted_to": "raw/extracted/3684dca4e6964ab1ace2783dbeb30cfb"
    }
  ],
  "outputs": {
    "events": "events.jsonl",
    "summary": "summary.json",
    "triage_json": "triage.json",
    "triage_markdown": "triage.md",
    "report_html": "report.html"
  }
}
```

## 缓存与去重

同一批输入不应该重复生成报告。`run` 开始时计算 `cache_key`：

```text
cache_key = sha256(
  sorted(input identity tuples: sha256 + size + normalized relative name)
  + script_version
  + code_ref
  + case_name
  + scenario
)
```

默认行为：

- 如果 `<out>/runs/*/manifest.json` 中已有相同 `cache_key`，直接复用旧 run 目录。输入顺序不同但集合相同时，应命中同一个缓存。
- 如果没有命中，创建新的时间化 run 目录并执行完整 pipeline。
- 如果用户传 `--force`，即使命中缓存也创建新 run。

复用时输出类似：

```text
Reusing existing run:
  /tmp/ds-triage/runs/20260720-021530-round2-isolated-port
```

## 四个阶段

### 1. 解析日志

输入：一个或多个原始日志包、文件或目录。

输出：`raw/inputs/`、`raw/extracted/`、`events.jsonl`。

职责：

- 识别普通文件、目录、`.log.gz`、gzip-tar。
- 保存每个原始输入副本。目录输入按文件逐个记录，避免只记录目录名导致内容不可追溯。
- 每个压缩包解压到独立 `raw/extracted/<input-stem-or-hash>/`，避免多个包内同名 member 覆盖。
- 对多个输入整体生成一个 `events.jsonl`，并在 event 中保留 `input_id`。
- 每条结构化 event 保留 `source/member/line/raw` 回指。
- 解析字段：
  - `trace_id`
  - timestamp
  - worker / ip / role hint
  - access operation / status / latency
  - `latencySummary:{...}` 原文和值
  - RPC slow method 和子字段
  - `URMA_ELAPSED_TOTAL/POLL_JFC/NOTIFY/THREAD_SHED`
  - `URMA_PERF`
  - error text
  - breakdown block

`events.jsonl` 示例：

```json
{
  "schema_version": 1,
  "trace_id": "e71f2d17-ab54-431e-8cba-ca1c988a1b95",
  "input_id": "b317726ce0534288aa832cfbf4340a43.gz",
  "ts": "2026-07-20T00:02:41.123000",
  "worker": "kvchachjpworker-0-worker22",
  "event_type": "rpc_slow",
  "operation": "WorkerWorkerOCService.BatchGetObjectRemote",
  "latency_ms": 231.301,
  "fields": {
    "server_exec_us": 171361,
    "network_residual_us": 59935
  },
  "source": "raw/extracted/.../worker.log",
  "member": "kvchachjpworker-0-worker22/worker.log",
  "line": 38,
  "raw": "[ZMQ_RPC_FRAMEWORK_SLOW] ..."
}
```

### 2. 聚合分析

输入：跨所有输入合并后的 `events.jsonl`。

输出：`summary.json`。

职责：

- 时间维度：first/last timestamp、秒级 bucket、burst。
- 输入维度：每个输入包的 trace 数、时间范围、错误族和主要 root-cause family。
- worker 维度：line count、trace count、EntryWorker/DataWorker/MetaWorker 候选。
- flow 维度：Get/Set/Create/Publish/GetObjMetaInfo/BatchGetObjectRemote/RemotePull。
- latency 维度：access、RPC slow、URMA、latencySummary、breakdown 的 p50/p90/p99/max。
- error 维度：status、deadline、not found、object in use、URMA timeout、fallback、Etcd。

#### 时间维度策略

时间维度必须回答三个问题：

1. 慢/错是否集中在某个时间窗口。
2. client、entry worker、data worker、meta worker 的日志顺序是否能对齐。
3. 日志采样或滚动截断是否造成某段时间的观测缺口。

`summary.json` 必须输出 `time_buckets`：

```json
{
  "time_buckets": [
    {
      "bucket_start": "2026-07-18T19:20:03+08:00",
      "bucket_ms": 1000,
      "trace_count": 28,
      "error_count": 6,
      "slow_count": 21,
      "p50_access_ms": 518.923,
      "p99_access_ms": 1035.210,
      "top_classification": "client_deadline_with_urma_wait",
      "top_workers": ["kvchachjpworker-0-worker7"],
      "coverage": {"client": "present", "worker": "sampled", "urma": "sparse"}
    }
  ]
}
```

时间桶要求：

- 默认同时生成 `1s` 和 `10s` 两套 bucket；日志量很小时退化为按 trace timestamp 排序。
- 每个 bucket 聚合 trace count、line count、slow count、error count、access p50/p90/p99/max、RPC slow p99、URMA total p99、missing stage ratio。
- 对多输入包分别记录 package time range；不同输入包时间不重叠时，不能跨包做前后因果推断。
- 对同一 trace 的多面日志，保留 `first_event_ts/last_event_ts/client_ts/entry_ts/data_ts/meta_ts`；不同机器时钟未校准时标 `clock_alignment=unknown`。
- 计算 `burst_score`：当前 bucket 的 slow/error 数量相对相邻 bucket 和全局 baseline 的突增程度。
- 计算 `gap_score`：当前 bucket 是否出现 client 有日志但 worker/URMA 消失，或 worker 有 completion 但 client access 缺失。

时间维度 triage 规则：

- `time_burst_same_worker`: 慢/错集中在连续时间桶，且 top worker 相同。
- `time_burst_cross_worker`: 多 worker 同时突刺，优先怀疑共享资源、网络、meta/master、UB fabric、日志采样策略。
- `late_worker_completion`: client deadline 已返回，worker/data worker 后续完成；必须同时展示 deadline time 和 completion time。
- `log_gap_window`: 某时间桶缺失某类日志面；只能作为采样/采集问题，不能直接归因业务慢。
- `single_trace_outlier`: 时间上不成簇，只是个别 trace 慢；优先看 trace 内 stage breakdown。

HTML 时间视图至少包含：

- access/worker/rpc/URMA/error 的统一时间轴。
- 慢 trace 点图，颜色表示 classification，形状表示 flow。
- bucket 表，展示 p99、错误数、missing ratio、top worker。
- 点击时间桶后联动 worker 表、trace 表和 evidence。

#### Worker 维度策略

worker 维度必须按角色和边拆开，而不是只数日志行。parser 阶段只保留观察到的 worker/address，triage 阶段再标角色。

`summary.json` 必须输出 `worker_summary`：

```json
{
  "worker_summary": {
    "kvchachjpworker-0-worker7": {
      "roles": ["entry_worker"],
      "line_count": 184,
      "trace_count": 72,
      "slow_trace_count": 61,
      "error_count": 8,
      "flows": {"DS_KV_CLIENT_GET": 72},
      "stage_p99_ms": {
        "read.client_to_entry_worker": 20.298,
        "read.entry_to_data_worker": 231.321
      },
      "coverage": {"client": "present", "remote_get": "present", "urma": "sparse"}
    }
  }
}
```

角色识别规则：

- client：client access、SDK/WorkerRpc、`client.*` latencySummary。
- entry worker：接收 client Get/Create/Publish 的 worker，或打印 `[Get] Done`、`Remote get request`、`worker.rpc.remote_get` 的 worker。
- data worker：打印 `URMA_ELAPSED_*`、`BatchGetObjectRemote`、RemotePull provider 侧 completion 的 worker。
- meta/master worker：打印 `QueryMeta/CreateMeta/UpdateMeta`、`master.process.*`、MasterOCService RPC slow 的 worker。
- 如果只有 IP 或目录名，没有明确角色证据，标 `role=unknown`，不能强行归到 entry/data/meta。

worker 聚合要求：

- 按 worker 统计 trace_count、slow_trace_count、error_count、missing_stage_count、top classifications。
- 按角色统计 p50/p90/p99/max，避免把 entry worker 和 data worker 的耗时混在一起。
- 按边统计 `src_worker/src_addr -> dst_worker/dst_addr` 的 count、p99、error、transfer_path。
- 按 operation 统计 worker 贡献：Get、RemotePull、QueryMeta、CreateMeta、Publish、URMA。
- 按 worker 统计日志覆盖：client/entry/data/meta/URMA/RPC slow 是否齐全。
- 对 worker 名称和 IP 做 alias map；没有来源证明时只展示并列候选，不做合并。

worker 维度 triage 规则：

- `worker_hotspot_entry`: 慢 trace 主要集中在某个 entry worker，且 entry process/remote_get stage 慢。
- `worker_hotspot_data`: 多个 entry worker 指向同一个 data worker，且 UB/RemotePull 慢集中在该 data worker。
- `worker_hotspot_meta`: 多个 entry worker 的 QueryMeta/CreateMeta 慢集中到同一 meta/master。
- `worker_edge_hotspot`: 单条 `entry -> data` 或 `entry -> meta` 边 p99/error 明显高。
- `worker_log_coverage_gap`: 某 worker 缺少预期日志面；作为采样/采集风险输出。
- `worker_role_ambiguous`: worker 角色无法由日志证明；报告中只做候选，不生成确定性 root cause。

HTML worker 视图至少包含：

- Worker 排名表：slow count、error count、p99、missing ratio、top stage。
- Role filter：client/entry/data/meta/unknown。
- Edge 表和图：`entry -> data`、`entry -> meta`、`data -> entry UB write`。
- 点击 worker 后展示该 worker 的代表 trace、stage breakdown、原始 evidence。

### 3. 定位定界

输入：`events.jsonl` + `summary.json`。

输出：`triage.json` + `triage.md`。

职责：

- 给每条 trace 生成分类：
  - `client_deadline_with_urma_wait`
  - `client_deadline_20ms`
  - `write_memory_copy_dominant`
  - `rpc_network_residual`
  - `dataworker_server_exec_tail`
  - `entryworker_process_tail`
  - `trace_log_mixing_or_missing_summary`
  - `unknown`
- 按分类聚合成 root-cause families。
- 生成 issue candidates：
  - title
  - trace count
  - representative traces
  - stage breakdown
  - key evidence
  - follow-up questions
- 明确 evidence boundary：
  - observed evidence
  - source-backed inference
  - hypothesis / needs more logs

`triage.md` 供人工直接阅读，`triage.json` 供 HTML 和 issue 脚本消费。

## 关键流程分段提取

定位定界阶段必须显式生成读/写关键流程分段。分段结果写入每条 trace 的 `stage_breakdown`，供 `triage.json`、issue candidates 和 HTML 消费。

### 读取流程

读取流程固定拆成 4 段：

```text
client -> entry worker
entry worker -> meta worker
entry worker -> data worker
data worker UB write
```

| 阶段 | 优先日志来源 | 备选日志来源 | 输出字段 |
|---|---|---|---|
| `read.client_to_entry_worker` | client access `DS_KV_CLIENT_GET`、`[Client/WorkerRpc] Get done`、`latencySummary:{client.rpc.get:...}` | client error 中 `Get->[ip:port] Reached timeout=20ms` | `duration_ms`, `entry_worker`, `entry_addr`, `timeout_ms`, `status` |
| `read.entry_to_meta_worker` | worker summary `worker.rpc.query_meta`、`ZMQ_RPC_FRAMEWORK_SLOW method=...QueryMeta...` | `Query metadata from master`、`GetObjMetaInfo`、`GetObjectLocations`、`PureQueryMeta` 相关日志 | `duration_ms`, `meta_addr`, `server_exec_us`, `network_residual_us` |
| `read.entry_to_data_worker` | worker summary `worker.rpc.remote_get`、`BatchGetObjectRemote` bRPC/ZMQ slow、`[Get] Remote done` | `RemotePull Receive/Finish`、`WorkerWorkerOCService.BatchGetObjectRemote` | `duration_ms`, `data_worker`, `data_addr`, `server_exec_us`, `network_residual_us` |
| `read.data_worker_ub_write` | data worker 侧 `URMA_ELAPSED_TOTAL`，且 `target address` 指向 entry worker | `URMA_PERF`、`URMA_ELAPSED_POLL_JFC/NOTIFY/THREAD_SHED` | `duration_ms`, `src_addr`, `target_addr`, `data_size`, `cpuid`, `urma_substages` |

读取阶段选择规则：

- `client -> entry worker` 是 client 可见等待窗口；它通常包含 entry worker 处理和响应返回，不能和下面三段简单相加。
- `entry -> meta worker`、`entry -> data worker`、`data worker UB write` 是服务端/数据面证据，用于解释 client 等待为什么超时或变慢。
- `data worker UB write` 通常被 `entry -> data worker` RPC e2e 覆盖或相关联，报告中作为 `entry -> data worker` 的证据子段展示，默认不再加到总和里。
- 如果 client 在 20ms deadline 返回失败，而 worker 侧 200ms 后才完成，两个时间都保留：client 段标为 deadline，worker/data 段标为 late completion。

读取 trace 的 `stage_breakdown` 示例：

```json
{
  "trace_id": "e71f2d17-ab54-431e-8cba-ca1c988a1b95",
  "flow": "read",
  "stage_breakdown": [
    {
      "stage": "read.client_to_entry_worker",
      "duration_ms": 20.298,
      "source": "client latencySummary / [Client/WorkerRpc] Get done",
      "evidence_ref": "events.jsonl:12"
    },
    {
      "stage": "read.entry_to_meta_worker",
      "duration_ms": 0.378,
      "source": "worker latencySummary + bRPC QueryMeta slow",
      "fields": {"server_exec_us": 11, "network_residual_us": 188}
    },
    {
      "stage": "read.entry_to_data_worker",
      "duration_ms": 231.321,
      "source": "worker latencySummary + BatchGetObjectRemote bRPC + Remote done",
      "fields": {"server_exec_us": 171361, "network_residual_us": 59935}
    },
    {
      "stage": "read.data_worker_ub_write",
      "duration_ms": 171.386,
      "source": "DataWorker RemotePull + URMA_ELAPSED_TOTAL",
      "fields": {"urma_total_ms": 171.386}
    }
  ]
}
```

### 写入流程

写入流程固定拆成 3 段：

```text
client -> entry worker createbuffer
client -> entry worker publish
entry worker -> meta worker publish
```

| 阶段 | 优先日志来源 | 备选日志来源 | 输出字段 |
|---|---|---|---|
| `write.client_to_entry_createbuffer` | client access `DS_KV_CLIENT_CREATE`、`latencySummary:{client.rpc.create:...}`、`[Client/WorkerRpc] Create done` | Worker `Create` access / `WorkerOCService.Create` bRPC slow | `duration_ms`, `entry_worker`, `entry_addr`, `status` |
| `write.client_to_entry_publish` | client access `DS_KV_CLIENT_PUBLISH`、`latencySummary:{client.rpc.publish:...}`、`[Client/WorkerRpc] Publish done` | Worker `Publish done`、`worker.process.publish` | `duration_ms`, `entry_worker`, `entry_addr`, `status` |
| `write.entry_to_meta_publish` | worker summary `worker.rpc.create_meta`、`MasterOCService.CreateMeta` bRPC/ZMQ slow | `CreateMetadataToMaster`、`CreateMeta done`、`worker_oc_service_publish_impl` 相关日志 | `duration_ms`, `meta_addr`, `server_exec_us`, `network_residual_us` |

写入还有一个独立 client 本地分段：

| 阶段 | 来源 | 说明 |
|---|---|---|
| `write.client_memory_copy` | `latencySummary:{client.process.memory_copy:...}`、`[Set] phase=MemoryCopy` | 这是 client 本地共享内存拷贝，不是 RPC 边；低于 `SLOW_PATH_LOG_THRESHOLD_US=3000` 时可能只有 summary，没有单独慢日志 |

写入阶段选择规则：

- `client.process.memory_copy` 是本地分段，不能画成 worker 边。
- `client.rpc.create` 和 `client.rpc.publish` 可以用于解释 client Set 总耗时，但它们和 worker 侧 `Create/Publish` access 不是两个可加分段，通常是同一 RPC 的不同观测面。
- `entry -> meta worker publish` 主要对应 CreateMeta；日志没有显式 meta target 时，必须标记为 `target_unknown`，不能用 IP 或目录强推。
- 如果只看到 `DS_KV_CLIENT_SET` 总耗时和 `latencySummary`，但没有 Create/Publish access，仍输出写入分段，证据来源标为 `client latencySummary only`。

写入 trace 的 `stage_breakdown` 示例：

```json
{
  "trace_id": "7991af76-71b0-4853-a86f-ff4bebe54d66",
  "flow": "write",
  "stage_breakdown": [
    {
      "stage": "write.client_to_entry_createbuffer",
      "duration_ms": 0.490,
      "source": "latencySummary:{client.rpc.create:490}"
    },
    {
      "stage": "write.client_memory_copy",
      "duration_ms": 2.988,
      "source": "latencySummary:{client.process.memory_copy:2988}"
    },
    {
      "stage": "write.client_to_entry_publish",
      "duration_ms": 0.690,
      "source": "latencySummary:{client.rpc.publish:690}"
    },
    {
      "stage": "write.entry_to_meta_publish",
      "duration_ms": 0.249,
      "source": "MasterOCService.CreateMeta bRPC slow / worker.rpc.create_meta"
    }
  ]
}
```

### 缺失与置信度

每个 stage 必须带 `confidence`：

- `high`: 有该阶段专属日志或 summary 字段。
- `medium`: 由同 trace 的 RPC slow + operation/method 匹配推导。
- `low`: 只由上下游时间差或 residual 推导。
- `missing`: 没有可用证据，报告中显示 `<missing>` 或 `<0.25ms / 未打点>`。

严禁把 `low/missing` 阶段写成原始日志事实。

### UB/URMA 字段字典

UB 是读路径定界的一级维度，不能只解析 `URMA_ELAPSED_TOTAL` 的 cost。parser 必须把 UB 相关日志统一落到 `ub_events`，再由 aggregate 生成 `ub_summary`。

#### 日志来源

| 来源 | 作用 |
|---|---|
| `[Get] Done ... transferPath: UB/RDMA/TCP ... inflightRemoteGet` | 判断本次 Get 的传输路径和 entry worker 并发堆积 |
| `Remote get request:[requestId] object:[key] offset[...] size[...] src -> dst` | 建立 entry worker 到 data worker 的 requestId、object、size、src/dst 边 |
| `Remote get success ... path: UB/RDMA/TCP, cost` | entry worker 视角的 RemotePull e2e |
| `latencySummary:{worker.rpc.remote_get:...}` | entry worker summary 视角的远端拉取 RPC |
| `latencySummary:{worker.urma.urma_total:...}` | worker summary 视角的 URMA 总耗时 |
| `latencySummary:{client.urma.ub_transfer:...}` | client UB Put/transfer 视角 |
| `[URMA_ELAPSED_TOTAL]` | WR/Event 创建到 wait 返回的总账 |
| `[URMA_ELAPSED_POLL_JFC]` | poll JFC 调用本身耗时 |
| `[URMA_ELAPSED_NOTIFY]` | poll 线程通知等待线程耗时 |
| `[URMA_ELAPSED_THREAD_SHED]` | poll loop/nanosleep 调度间隔 |
| `[URMA_PERF]` | URMA perf counter 原文，用于后续字段扩展 |
| `URMA_WAIT_TIMEOUT/K_URMA_WAIT_TIMEOUT/urma write deadline exceeded` | UB wait 错误族 |
| `URMA_NEED_CONNECT/TryReconnectRemoteWorker/URMA_CONNECT_FAILED` | UB 连接/重连错误族 |
| `fallback payload rejected/enable_transport_fallback` 相关日志 | UB fallback 到 TCP 的边界 |

#### 字段解析

`ub_events` 至少支持以下字段：

| 字段 | 来源 | 说明 |
|---|---|---|
| `trace_id` | trace context / 行内 UUID | 主关联键 |
| `request_id` | `request id:`、`requestId`、`Remote get request:[...]` | UB event 关联键；同一 trace 可以有多个 request id |
| `event_type` | 日志标签 | `total/poll_jfc/notify/thread_sched/perf/remote_get_start/remote_get_success/error` |
| `timestamp` | 行首时间 | 用于和 client/entry/data worker 时间线对齐 |
| `worker` | 文件路径、日志字段、src/dst | 当前日志产生的 worker |
| `role` | 推导字段 | `entry_worker/data_worker/client/unknown` |
| `src_addr` | `src address:` 或 AppendSrcDst | UB 发起端地址 |
| `target_addr` | `target address:` 或 AppendSrcDst | UB 目标地址 |
| `remote_addr` | Remote get address | entry worker 请求的 data worker |
| `object_key` | Remote get request | 对象维度；可脱敏/哈希后保留 |
| `offset` | Remote get request | offset read 维度 |
| `read_size` | Remote get request | 读取尺寸 |
| `data_size` | `dataSize:`、Remote get request/rsp | 传输尺寸，单位 byte |
| `cost_us` | `POLL_JFC/THREAD_SHED` | 当前源码里这两类日志是 us |
| `cost_ms` | `TOTAL/NOTIFY/Remote get success` | 统一聚合用 ms；us 字段也要换算 |
| `wait_os_sched_ms` | `TOTAL` 中 `wait_for` | 等待线程实际 wait 返回窗口 |
| `cpuid` | URMA elapsed | CPU/NUMA 维度 |
| `count` | `NOTIFY` | 一次 notify 处理的 finished request 数 |
| `status` | `TOTAL` 或 error 文本 | OK / K_URMA_WAIT_TIMEOUT / K_RPC_DEADLINE_EXCEEDED 等 |
| `urma_inflight_wr_count` | `TOTAL` | 当前 WR event map 大小 |
| `transfer_path` | `[Get] Done` / Remote get success | UB/RDMA/TCP |
| `inflight_remote_get` | `[Get] Done/Receive` | entry worker 远端拉取并发 |
| `perf_key/perf_value/perf_unit` | `[URMA_PERF]` | perf counter 原样保留并标准化单位 |
| `raw` | 原始日志 | 证据回读 |
| `source/member/line` | 输入位置 | 下载 evidence 和自验证 |

字段解析必须支持旧格式和当前格式：

- 旧 fixture 可能是 `cost 517.732ms, request id:77`。
- 当前源码格式可能是 `total cost 517.732ms, wait os sched ...: 517.100ms, request id:77`。
- `POLL_JFC/THREAD_SHED` 当前是 `cost <N>us`，不能按 ms 直接读。
- `NOTIFY` 当前是 `cost <N>ms, cpuid: <id>, count: <N>`。
- `TOTAL` 中 `status` 是完整 `Status::ToString()`，必须保留原文并抽取错误族。

#### UB 聚合维度

`summary.json` 必须生成：

- 按 `event_type` 的 p50/p90/p99/max。
- 按 `src_addr -> target_addr` 的 UB 边统计。
- 按 `target_addr` 的慢尾和错误数。
- 按 `request_id` 的子事件覆盖：是否同时有 total/poll/notify/thread_sched/perf。
- 按 `data_size` bucket 的耗时分布，例如 `<64KiB`、`64KiB-1MiB`、`1-4MiB`、`>4MiB`。
- 按 `cpuid` 的慢尾分布，用于观察 CPU/NUMA/调度问题。
- 按 `urma_inflight_wr_count` 的分布和慢尾相关性。
- 按 `transfer_path` 的 UB/RDMA/TCP/fallback 占比。
- 按 `status/error_family` 的错误聚合。

`triage.json` 里的 UB 分类至少包括：

| 分类 | 条件 | 说明 |
|---|---|---|
| `ub_wait_total_tail` | `TOTAL` 慢，poll/notify/thread_sched 不慢或缺失 | completion wait 总账慢，不能细分到 poll/notify |
| `ub_poll_jfc_tail` | `POLL_JFC` 慢 | URMA poll 调用本身慢 |
| `ub_notify_tail` | `NOTIFY` 慢或 count 很大 | poll 线程通知等待线程慢 |
| `ub_thread_sched_tail` | `THREAD_SHED` 慢 | poll loop/nanosleep/OS 调度间隔异常 |
| `ub_inflight_tail` | inflight 高且 total 慢 | 可能是并发堆积，需要结合 worker 线程和队列 |
| `ub_size_tail` | 大 data_size bucket 明显更慢 | 尺寸相关尾延迟 |
| `ub_timeout` | `K_URMA_WAIT_TIMEOUT/URMA_WAIT_TIMEOUT` | wait 超时错误族 |
| `ub_connect_or_reconnect` | connect/need reconnect 日志 | 连接建立或恢复边界 |
| `ub_fallback_boundary` | fallback/TCP evidence | UB 失败后的 TCP/fallback 边界 |
| `ub_missing_substage` | total 慢但缺 poll/notify/thread_sched | 观测盲区，只能建议补采 |

#### UB 关联规则

- `request_id` 优先级高于 trace 时间接近匹配；没有 request id 时才用 trace id + 时间窗口 + src/target。
- `Remote get request:[id]` 是 entry worker 发起边；`URMA_ELAPSED_TOTAL request id:<id>` 是 data worker/URMA wait 证据。两者要作为同一 `ub_flow` 的不同观察面。
- 同一 trace 出现多个 `request_id` 时，必须保留多个 UB event，不得去重成一条；大对象分片、retry、batch get 都可能产生多个 event。
- `TOTAL` 覆盖 Event/WR 到 wait 返回，不等于 poll 本身慢；只有 `POLL_JFC` 慢才能说 poll 调用慢。
- `wait_os_sched_ms` 接近 `total_ms` 说明等待线程阻塞窗口长，但仍不等价于 OS 调度慢；要结合 `THREAD_SHED/NOTIFY/cpuid/inflight`。
- `transferPath: UB` 证明 Get 选择 UB 路径；缺 URMA elapsed 时只能说 UB 子事件日志缺失，不能说没有 UB。
- `worker.urma.urma_total` 是 summary phase，和 `[URMA_ELAPSED_TOTAL]` 可能是同一窗口的不同观测面，默认不相加。
- `client.urma.ub_transfer` 是 client 侧 UB transfer，不和 worker RemotePull UB write 混成同一阶段。
- `target_addr` 指向 entry worker 时可用于 `data_worker_ub_write`；如果目标不是 entry worker，必须单独标记为 `ub_other_direction`。
- 有 fallback 证据时，报告要把 UB attempt 和 TCP/fallback completion 分开展示，不能只按最终成功路径聚合。

#### UB 报告视图

HTML 至少提供：

- UB edge 表：`src_addr -> target_addr`、count、p99、max、error。
- UB request 表：trace id、request id、data size、total/poll/notify/thread_sched、cpuid、status、source log。
- UB timeline：client deadline、entry RemotePull、data worker URMA total 的时间对齐。
- UB heatmap：target worker × event type 或 cpuid × event type。
- UB missing coverage：每个慢 `TOTAL` 是否缺 poll/notify/thread_sched/perf。

### 日志缺失与采样降级

日志输入可能存在采样、滚动截断、只给 client 侧、只给 worker 侧、gzip 包缺成员、trace ID 不完整等情况。pipeline 必须把“日志缺失”当成一等产物，而不是静默跳过。

每次 run 必须生成 `coverage`：

```json
{
  "coverage": {
    "input_packages": 2,
    "files_seen": 18,
    "files_parsed": 17,
    "time_range": {"start": "2026-07-17T10:12:01.123+08:00", "end": "2026-07-17T10:20:31.456+08:00"},
    "surfaces": {
      "client_access": {"events": 248, "status": "present"},
      "worker_access": {"events": 93, "status": "sampled"},
      "rpc_slow": {"events": 41, "status": "present"},
      "latency_summary": {"events": 248, "status": "present"},
      "urma_elapsed": {"events": 7, "status": "sparse"},
      "error": {"events": 12, "status": "present"}
    },
    "warnings": [
      "worker_access events are fewer than client traces; stage confidence may be downgraded",
      "URMA elapsed exists for only 7 traces; missing UB substage is not evidence of no URMA cost"
    ]
  }
}
```

每条 trace 必须生成 `evidence_coverage`：

```json
{
  "trace_id": "e71f2d17-ab54-431e-8cba-ca1c988a1b95",
  "evidence_coverage": {
    "client": "present",
    "entry_worker": "present",
    "meta_worker": "missing",
    "data_worker": "sampled",
    "urma": "missing",
    "clock_alignment": "same_host_or_unknown"
  },
  "missing_evidence": [
    {
      "stage": "read.entry_to_meta_worker",
      "expected": ["worker.rpc.query_meta", "QueryMeta rpc slow"],
      "impact": "cannot split meta lookup from entry worker processing",
      "fallback": "mark missing; keep client_to_entry_worker as observed upper bound"
    }
  ]
}
```

缺失处理规则：

- 只缺子阶段日志时，保留上层观测窗口。例如缺 `QueryMeta`，仍可保留 `client -> entry worker`，但 `entry -> meta worker` 必须标 `missing`。
- 只有 client `latencySummary` 时，可以输出 client 侧 create/get/publish/memory_copy 分段；worker 边标 `missing`，不推断目标 worker。
- 只有 worker/RPC slow、没有 client access 时，可以生成 worker 侧候选 trace，但 client 可见延迟标 `unknown`，不能归类为 client deadline。
- 有 error timeout、没有 success/done 日志时，client 段状态为 `deadline`，duration 使用 timeout 或 error elapsed；后续 worker completion 如果晚到，标为 `late_completion`。
- 有上下游两个时间点但缺中间日志时，只能给 residual/upper_bound，`confidence=low`，报告文案使用“未打点区间”。
- 缺 URMA elapsed 不代表没有 UB 写，只能说明当前日志无法证明 UB 子阶段；`data_worker_ub_write` 标 `missing` 或 `sparse_sampled`。
- 多包输入时间范围不重叠时，不能跨包拼接 trace；必须在 `manifest.json` 记录 package time range 并给出 `trace_log_mixing_or_missing_summary` warning。
- 如果日志采样导致同一 trace 的 client/worker 数量明显不匹配，聚合层必须输出 `sample_bias_warning`，Top trace 仍展示，但 root-cause family 降级为 `needs_more_logs`。

缺失场景的 triage 输出必须区分三类：

| 类型 | 含义 | 报告动作 |
|---|---|---|
| `observed` | 有直接日志字段或原文 | 可作为结论证据 |
| `bounded` | 只有上/下界，例如 client deadline 或 RPC e2e | 可作为定位边界，不可拆成内部 root cause |
| `unobserved` | 预期日志缺失 | 展示缺口和建议补采日志 |

采样质量也必须被聚合：

- 按 worker 统计 missing stage 比例。
- 按时间桶统计日志面覆盖率，找出采样断层。
- 按 flow 统计 `high/medium/low/missing` 占比。
- 对 issue candidate 计算 `evidence_score`，低于阈值时只生成 “needs more logs” 候选，不生成确定性根因标题。

建议补采日志应由脚本自动生成，例如：

```text
Need more logs for read.entry_to_data_worker:
- entry worker access or worker summary around 2026-07-17 10:14:03.100 +0800
- data worker log containing trace_id=e71f2d17-ab54-431e-8cba-ca1c988a1b95
- rpc slow for BatchGetObjectRemote
- URMA_ELAPSED_TOTAL / URMA_ELAPSED_POLL_JFC if UB path is enabled
```

### 4. 渲染报告

输入：`manifest.json`、`summary.json`、`triage.json`、`triage.md`。

输出分为两种：

- `report.local.html`: 本地自包含 HTML，可直接用浏览器打开。
- `report.site.html`: yche.me 站点版 HTML，使用站点模板、共享 CSS/JS 和首页索引元数据。

职责：

- 展示 case/scenario、analysis time、trace time range、code_ref、cache_key。
- 展示时间、worker、flow、latency、error 多维图表。
- 展示 root-cause family 表。
- 展示 Top Trace 和 evidence。
- 支持下载：
  - Trace IDs
  - Breakdown CSV
  - Evidence text
  - triage JSON/Markdown
- 错误和大耗时高亮。

渲染层不能重新扫描原始日志，只能消费中间产物。这样后续可以替换 HTML 模板，不影响 parser 和 triage。

#### 4.1 本地 HTML

入口：

```text
ds_trace_triage.py render-local <run-dir>
ds_trace_triage.py run <inputs...> --render local
```

产物：

```text
<run-dir>/report.local.html
```

本地 HTML 要求：

- 单文件自包含，CSS、JS、必要数据直接内嵌。
- 不依赖 yche.me 站点资源，不要求网络。
- 可以通过 `file://` 或本地浏览器直接打开。
- 下载按钮直接从内嵌数据生成 Trace IDs、Breakdown CSV、Evidence text、`triage.json/triage.md`。
- `manifest.json` 中记录 `render_targets.local.status/path/generated_at`。

校验：

- 抽取 inline JS 后执行 `node --check`。
- 检查必要 DOM id、图表容器、下载按钮存在。
- 检查 `report.local.html` 内含 `cache_key`、`case_name`、`trace_time_range` 和至少一个代表 trace。

#### 4.2 yche.me 站点 HTML

入口：

```text
ds_trace_triage.py render-site <run-dir> --site-root /path/to/htmls --category perf
ds_trace_triage.py publish-site <run-dir> --remote xqyun-32c32g --category perf
ds_trace_triage.py run <inputs...> --render site --site-root /path/to/htmls
```

产物：

```text
<run-dir>/report.site.html
<run-dir>/publish_manifest.json
```

站点 HTML 要求：

- 输出适配 yche.me 目录结构的页面，默认放到 category 子目录，例如 `perf/ds-trace-triage-<case-slug>-YYYYMMDD.html`。
- 页面引用站点共享资源，例如 `/assets/css/site.css`、`/assets/js/site.js`；大表格/图表需要使用局部宽容器，避免被默认正文宽度压窄。
- 页面标题、日期、category、description、path 必须能生成 `index.html` `var P = [...]` 注册项。
- `publish_manifest.json` 记录 local path、site relative path、intended URL、index registration、git commit、live check 结果。
- 站点版不能修改 parser/triage 产物；它只消费 `<run-dir>` 下的中间产物。

发布流程必须分成 prepare 和 publish 两步：

```text
prepare-site
  -> 读取 site rules
  -> 生成 report.site.html
  -> 生成 index registration patch/candidate
  -> site local validation

publish-site
  -> stage intended paths only
  -> git diff --cached --check
  -> commit/push or rsync according to site workflow
  -> curl live URL
  -> update publish_manifest.json
```

发布校验规则：

- 发布前必须读取 yche.me 站点规则文件，特别是 `/var/www/html/CLAUDE.md`；不能假设 root HTML 合法。
- 新页面必须在 category 子目录，不能默认写站点根目录。
- 新页面必须注册到首页或目录索引；未注册视为未完成发布。
- stage 只能包含本次 run 对应页面和索引变更，不能 `git add .`。
- 需要校验 `index.html` metadata/path 存在、`git diff --cached --check`、live URL `HTTP 200` 和页面关键内容回读。
- 如果 publish 失败，`publish_manifest.json` 必须标记 `status=failed`，并保留本地 `report.site.html` 供人工继续。

两种 HTML 的关系：

| 目标 | 文件 | 资源依赖 | 适用场景 | 完成定义 |
|---|---|---|---|---|
| local | `report.local.html` | 无外部依赖 | 本地分析、CI artifact、离线传阅 | 文件存在且 JS/DOM 校验通过 |
| site | `report.site.html` | yche.me 站点 CSS/JS/index | 对外发布、长期索引 | 页面生成、索引注册、live URL 和内容回读通过 |

## verify 契约

`verify` 使用内置 fixture 创建一个临时 run 目录，执行：

```text
fixture gzip-tar
  -> parse
  -> aggregate
  -> triage
  -> render
  -> validate
```

必须断言：

- `raw/inputs/` 存在原始 fixture 包。
- `raw/extracted/` 存在解压后的原始 trace 日志。
- `manifest.json` 包含 `case_name/scenario/analysis_created_at/trace_time_range/cache_key`。
- `events.jsonl` 事件能回指到 `source/member/line/raw`。
- `latencySummary` 原文和值能解析。
- RPC slow `server_exec_us/network_residual_us` 能解析。
- URMA elapsed 四个子字段能解析。
- UB 字段 fixture 能解析 request id、src/target、dataSize、cpuid、status、inflight、wait_os_sched、transferPath 和 us/ms 单位差异。
- 时间维度 fixture 能产生 `time_buckets/burst_score/gap_score/late_worker_completion`。
- worker 维度 fixture 能产生 `worker_summary/role/edge hotspot/worker_log_coverage_gap`。
- 缺失日志 fixture 能产生 `coverage/evidence_coverage/missing_evidence`。
- 采样不足的 trace 会降级为 `needs_more_logs`，不会生成确定性 root cause。
- `summary.json` 有 time/worker/flow/latency/error/classification 聚合。
- `triage.json` 有至少一个 root-cause family 和 issue candidate。
- `triage.md` 包含代表 trace 和关键证据。
- `report.local.html` 存在且内嵌 JS 可通过 `node --check`。
- `report.site.html` 可在临时 site root 中生成，且产生合法 `publish_manifest.json`。
- site render dry-run 不访问真实 yche.me，但必须校验 category path、index registration candidate 和站点资源引用。

## 历史脚本经验吸纳

历史会话里的脚本能力按阶段吸纳：

- gzip-tar 检查和解包：来自 248 Get trace、Round2、Round3 多轮分析。
- Top slow cap、分页、Trace selector、下载证据：来自第一轮 Get 页面和 ZMQRPC 页面。
- `latencySummary` 原文保留和 MemoryCopy 判断：来自写入 Set/Create/Publish 报告。
- Entry/DataWorker/MetaWorker 角色聚合和边统计：来自 04:00 错误日志页。
- 左右对比、GET/SET 分开、流程顺序分段数值：来自有/无底噪 P999 报告。
- 失败 trace issue-grade 分类：来自 17 条 GET failure trace 和 #791-#796 issue 创建。
- HTML 验证：来自首页白屏修复、`workerSummary` 移除和多次 inline JS 修复。

## 后续实现计划

1. 测试先行：新增 `run` 和 `verify` 的端到端 fixture 测试。
2. 抽出 `parse_inputs()`，输出 `events.jsonl`。
3. 抽出 `aggregate_events()`，输出 `summary.json`。
4. 抽出 `triage_events()`，输出 `triage.json/triage.md`。
5. 抽出 `render_local_report()`，输出 `report.local.html`。
6. 抽出 `render_site_report()` 和 `prepare/publish_site()`，输出 `report.site.html/publish_manifest.json`。
7. 实现 run 目录、manifest、cache_key 和 `--force`。
8. 更新 `.skills/ds-trace-triage/SKILL.md` 和中文方法论文档。
9. 将 `verify` 加入 CI gate。

## 验收标准

- `python3 scripts/ds_trace_triage.py run <fixture1> <fixture2> --out <tmp> --case verify-fixture` 产出完整 run 目录。
- 第二次运行相同输入集合且不带 `--force` 时复用已有 run；输入顺序调换也应复用。
- `--force` 会生成新的 run 目录。
- `--render local` 生成可直接打开的 `report.local.html`。
- `render-site --site-root <tmp-site>` 生成 `report.site.html`、index registration candidate 和 `publish_manifest.json`。
- `python3 scripts/ds_trace_triage.py verify` 通过。
- pytest 覆盖 parse、aggregate、triage、render、cache reuse。
- 文档说明 `run/verify` 两个入口和所有中间产物。
- 主工作区和 PR worktree 的脚本、skill、docs 保持一致。
