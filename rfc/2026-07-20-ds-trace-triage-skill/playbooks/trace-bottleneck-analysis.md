# Trace Bottleneck Analysis

## Metadata

- Status:
  - `active`
- Playbook scope:
  - DataSystem trace TopN main-problem and key-bottleneck report generation
- Owning module or area:
  - `scripts/ds_trace_triage.py`
  - `scripts/ds_trace_bottleneck.py`
- Primary context docs:
  - `.skills/ds-trace-triage/SKILL.md`
  - `.repo_context/modules/infra/observability/performance-troubleshooting.md`
  - `.repo_context/modules/infra/logging/trace-and-context.md`
- Last verified against source:
  - `2026-08-18`

## Responsibility Boundary

`ds_trace_triage.py` owns input archives, raw log parsing, Trace grouping, normalized
RPC/URMA fields, aggregation, and the timestamped run-directory contract.

`ds_trace_bottleneck.py` is a post-processor. It consumes a completed run directory,
selects TopN, builds exclusive stage attribution, and renders
`bottleneck.analysis.json` plus `bottleneck.local.html`. It must not read gzip/tar
inputs or become a second general-purpose log parser.

The current six-stage model is GET/read-specific. Mixed runs exclude non-GET
flows from Client TopN and report the excluded count instead of applying read
stage names to SET/CREATE/PUBLISH traces.

## Workflow

1. Pin and record the intended source ref.
2. Run the base triage pipeline:

   ```bash
   python3 scripts/ds_trace_triage.py run <inputs...> \
     --code-ref "$(git rev-parse main/master)" \
     --case <case> --scenario <scenario> --out <run-root>
   ```

3. Verify `manifest.json`, `summary.json`, and `triage.json` exist and check
   `dimensions.input_failures`.
4. Generate the bottleneck report:

   ```bash
   python3 scripts/ds_trace_bottleneck.py \
     --run-dir <run-dir> --top 100 --deadline-ms <known-deadline-ms> \
     --local-cache <true|false> \
     --source-ref "$(git rev-parse main/master)" \
     --output <run-dir>/bottleneck.local.html
   ```

   `local_cache` 由用户、运行配置或明确 case 元数据提供，推荐显式传入
   `--local-cache true|false`。未知时省略，报告必须保持拓扑中性。
   对 `local_cache=false`，读取拓扑是 `Client → Meta Owner → Data Worker`：
   单对象 `GetObjectRemote` 与批量 `BatchGetObjectRemote` 都属于
   Client→Data Worker RPC，URMA 属于
   Data Worker→Client。`WorkerWorkerOCService`、`BatchGetObjectRemote`、
   `RemotePull` 的服务/日志命名本身不证明 Worker→Worker 调用。
   `local_cache` 只确定调用拓扑；每条 GET 的位置必须由 Client access 日志中的
   `DS_KV_CLIENT_GET transportType` 判定：`SHM` 是本节点共享内存，`UB` 是远端
   Data Worker；`TCP` 可能是远端，也可能是同节点 SHM 失败回退，必须标位置不确定；
   缺失为未确认。`DS_POSIX_GET` 仅标识处理证据节点。统计只覆盖当前输入/TopN。

   Omit `--deadline-ms` when the run does not record one. The report then labels
   20ms as a visualization reference and records the assumption in `limitations`.

5. Read `bottleneck.analysis.json` before accepting the report language. Confirm
   TopN selection, evidence gaps, main-problem counts, and stage bounds.
6. Inspect representative raw evidence and verify source claims against current
   source. CodeGraph is discovery, not proof.
7. Keep the report local unless publication is explicitly requested.

8. Confirm the rendered topology matches the supplied mode. For
   `local_cache=false`, reject any conclusion or chart label that treats
   BatchGet/URMA as Worker→Worker without separate caller evidence.

9. When Worker/time correlation is requested, use the dedicated chapter:
   - keep Worker/category/status/relation/Client-latency-band/local-time filters
     local to that chapter, and rebuild all four charts and the table from the
     same filtered events;
   - aggregate by emitting-Worker local time in 1-second buckets;
   - inspect failed QueryMeta/RemoteGet in the closed ±1-second neighborhood;
   - keep RPC, UB, metadata, and data access as four independent dimensions;
   - classify a WR as slow only when observed `URMA_ELAPSED_TOTAL > 1.5ms`;
   - treat `transferPath: UB` as a path label, never as timing evidence;
   - call cross-Trace proximity “same-Worker concurrent companion”, not cause.

10. Interpret the Client-direct read and URMA evidence at the correct layer:
    - `client.rpc.direct_get_data` is a Client-side parent window, not Data Worker
      `ProcessGet`;
    - one `URMA_ELAPSED_TOTAL` request ID is one WR chunk, not the whole Client Get;
    - group complete chunk-index/count sequences into a logical Write and use the
      earliest-post to latest-observed wall span; never sum overlapping WR costs;
    - completion wait is a per-WR wait call, while Inflight WR is the sender
      manager's global in-flight snapshot.

11. Analyze QueryMeta by local time and emitting/initiating Worker. `QueryAndGet`
    is a Client-initiated metadata request. When the target address and server
    trailer are absent, label the Meta Owner target and server/network split as
    unobserved rather than assigning the logging node as Meta Owner.

`--top <N>` is generic. Top100 and Top1000 use the same model; report counts,
downloads, labels, sorting, and pagination must derive from the selected N.

The overview includes five Client-total-latency reference bands: `[5,6)ms`,
`[6,7)ms`, `[7,10)ms`, `[10,20]ms`, and `>20ms`. Exact 20ms belongs to the
fourth band; rows below 5ms remain in the all-TopN scope and are explicitly counted
outside the bands. A selected band filters the overview, TopN stacked bars, and Trace
table only. URMA, same-Worker, non-RPC, and Worker deep dives retain full TopN scope.

Generated tables wrap within the available width and avoid horizontal dragging.
Responsive views may hide secondary breakdown columns while preserving complete
rows in Trace detail and downloads. Same-Worker RPC/UB/metadata/data charts use
one chart per row so their time axes remain readable.

## Attribution Contract

The six display buckets are RPC network/framework residual, QueryMeta, URMA/UB
completion, remote non-URMA provider processing, unsegmented data-access parent,
and unexplained residual. Parent windows remain available as evidence but are not
added again. Per Trace:

```text
sum(exclusive stage attribution) <= user-visible Client latency
```

Missing evidence remains missing. Do not convert absent RPC, URMA, scheduling,
CPU, or lock fields to zero and do not name those mechanisms as root cause without
direct evidence.

## Validation

```bash
python3 scripts/ds_trace_triage.py verify
python3 -m pytest -s -q \
  tests/scripts/test_ds_trace_triage.py \
  tests/scripts/test_ds_trace_bottleneck.py
node tests/scripts/check_ds_trace_bottleneck.js <generated-report.html>
```

The browser check requires a report with enough rows to exercise 8-row pagination
and sorting. It verifies inline ECharts, charts, full-data sorting before pagination,
Trace/detail linkage, bounded log highlights, and real downloads.

## Operational Boundaries

- Offline analysis only; no DataSystem runtime hot-path, concurrency, persistence,
  or recovery behavior changes.
- Default output refuses overwrite; pass `--force` only for an intentional refresh.
- The script never publishes. Site publication still requires the triage skill's
  dry-run, HTML size gate, copy verification, and exactly one catalog entry.
- When log wording changes, normalize it in the base triage parser/tests first;
  keep the post-processor focused on diagnosis and presentation.

## Multi-run Controlled Comparisons

Use `.skills/ds-trace-bottleneck-analysis/SKILL.md` when the request spans
multiple independent runs, capped latency bands, QPS/client-shape changes, object
sizes, or implementation variants. Generate each run's triage and bottleneck
artifacts independently, then run `scripts/ds_trace_bottleneck_suite.py` over the
per-run analysis JSON files.

The suite layer may read archive member names to recover collection-band
provenance. It must not read trace member content, import `ds_trace_triage.py`,
or duplicate parser regexes. Require zero unmatched Trace IDs before comparing
bands. Match control groups by fixed axes and compare normalized within-band
composition and latency only. A capped count is not an occurrence rate and does
not support a cross-run benefit percentage without request-count and duration
denominators.
