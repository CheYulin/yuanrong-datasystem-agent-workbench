# Thread Artifact Audit

This audit records what was read from the eight Codex trace-analysis threads and
how each artifact shaped the reusable DataSystem trace triage workflow.

Current source calibration during the audit:

```text
main/master a7130ac9c3171bf3acb70601c7de99f7bc24f25a
```

| Thread | Produced artifact | Trace/corpus shape | Capability to preserve |
|---|---|---|---|
| `019f753c-d7fd-75b1-ac3e-b4afff712d23` | `https://yche.me/perf/ds-get-ub-remote-trace-rootcause-20260718.html` | 248 Get traces, p50 about 518.9ms, RemotePull and URMA completion tails dominate | gzip-tar handling, trace-id grouping, time/worker/flow latency aggregation, RemotePull + `ProcessGetObjectRequest` + `URMA_ELAPSED_TOTAL` evidence alignment, CodeGraph plus direct source validation |
| `019f75a9-dc00-7941-be79-84ceb728173e` | `https://yche.me/perf/ds-get-ub-remote-trace-round2-isolated-port-20260718.html` | 23 traces after hardware-port isolation, URMA max sub-ms, residual access tail around 20ms/70ms | Separate resolved hardware/URMA tail from residual 20ms client WorkerRpc and GetObjMetaInfo deadlines; avoid copying previous-round root cause |
| `019f7606-6565-7a50-8abf-33dcd9205eec` | `https://yche.me/perf/ds-trace-round3-no-baseline-noise-20260719.html#figure-2-4` | 12 no-baseline-noise failure traces, all around 20ms deadline | Role-aware flow graph: Client, Entry Worker, Meta Worker, DataWorker; classify RemotePull, QueryMeta, ProcessGetObjectRequest, and log-order/deadline mismatch separately |
| `019f7686-78bd-7551-8d4f-96cbe19e558d` | `https://yche.me/perf/ds-get-4-8ms-sampled-trace-performance-20260719.html` | ZMQRPC sampled 4-8ms traces | Parse RPC slow method and subfields: e2e, client framework, server queues, `server_exec_us`, `network_residual_us`; keep trace selector, breakdown CSV, evidence export as report expectations |
| `019f76d0-1529-79c1-9669-40ae879f382b` | `https://yche.me/perf/ds-write-set-create-publish-latency-20260719.html` | 273 Set/Create/Publish write traces; many summaries dominated by `client.process.memory_copy` | Preserve original `latencySummary:{...}` text, parse summary key/value fields, classify write memory copy dominance, distinguish low-threshold summary evidence from standalone slow logs |
| `019f7970-0deb-7432-b925-386a051c6f7f` | `https://yche.me/perf/ds-0719-0400am-error-log-analysis-20260719.html` | 04:00 error-log interactive report | Validate independent table/card trace filters and downloads; restore `categoryRowsFor`, `categoryRowsForCard`, and `evidenceText`; add EntryWorker/DataWorker/MetaWorker edge filters and 10/20/50/all pagination |
| `019f79c0-d276-7810-9009-fb6f2dc9cf85` | `https://yche.me/perf/ds-rw-p999-noise-vs-clean-20260719.html` plus yche homepage repair | P999 read/write noise-vs-clean report and site index failure | Generated HTML needs inline JS `node --check`, quoted/deduped index metadata, live homepage validation, and no misleading Worker/IP summary block when a tag is only a filter |
| `019f7b27-56f0-74f0-9a68-5b3742f11e23` | `https://yche.me/perf/ds-get-failure-noise-vs-clean-20260720.html?v=flow-legend-1`; GitCode issues #791-#796 | 17 GET failures: 12 noisy, 5 clean; all client-side about 20ms deadline with divergent server completion paths | Issue-grade failure families: DataWorker UB/URMA server exec, Entry->DataWorker network residual, client timeout but server fast, EntryWorker late processing, remote_get/brpc mismatch, QueryMeta/log-mixing anomalies |

## Parser Contracts Derived

- Keep `dimensions.time`, `dimensions.workers`, `dimensions.flow`,
  `dimensions.latency_ms`, `dimensions.breakdown_ms`, `dimensions.errors`, and
  per-trace evidence as the base output.
- Add `dimensions.latency_summary_us` and `traces[*].latency_summary_raw`; never
  reconstruct raw summary text when original log text exists.
- Add RPC slow subfield percentiles for `e2e_us`, framework costs, server queue,
  `server_exec_us`, and `network_residual_us`.
- Add URMA elapsed percentiles for total, poll JFC, notify, and thread
  scheduling.
- Add `dimensions.classifications` so repeated failure families can be tracked
  in CI and compared across DataSystem log-format evolution.

## Skill Contracts Derived

- Always refresh and record `main/master` before saying "latest".
- Treat CodeGraph as source discovery; validate high-impact causality directly
  in source.
- Keep aggregate distributions before per-trace examples.
- Separate observed evidence, source-backed inference, and hypothesis.
- For HTML reports, validate inline JavaScript, downloads, filters, page index
  registration, and live URLs.
