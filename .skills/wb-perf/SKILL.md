---
name: wb-perf
description: >-
  Performance hotspot and regression diagnosis with perf, bpftrace, strace, metrics_summary, kvtest, and ZMQ
  nightly evidence.
---

# Workbench Perf

## Purpose

Use this skill to identify runtime hotspots, explain regressions, and propose evidence-backed optimization work.

## When to Use

- A benchmark, smoke run, or daily build reports latency or throughput regression.
- The user asks where CPU, lock, RPC, or IO time is going.
- Metrics logs need conversion into a readable hotspot report.

## Inputs

- Profile: `perf.hotspot` for one-off diagnosis, `perf.regression` for baseline comparison, or benchmark smoke
  profiles such as `bench.dsbench.smoke` and `bench.kvtest.smoke`.
- Evidence source: perf/bpftrace/strace output, glog `metrics_summary`, dsbench output, kvtest CSV, or ZMQ nightly results.
- Node: default `tiantiyun-80c128g`.

## Commands

```bash
python3 scripts/harness/ds_harness.py perf --profile perf.hotspot
python3 scripts/harness/ds_harness.py perf --profile perf.regression
python3 scripts/harness/ds_harness.py perf --profile bench.dsbench.smoke
python3 scripts/harness/ds_harness.py perf --profile bench.kvtest.smoke
python3 scripts/harness/ds_harness.py perf --profile perf.hotspot --dry-run --json
```

Owned tools include:

- `scripts/analysis/perf/perf_record_kv_lock_io.sh`
- `scripts/analysis/perf/trace_kv_lock_io.sh`
- `scripts/analysis/perf/run_kv_lock_ebpf_workflow.sh`
- `scripts/analysis/perf/kv_executor_perf_analysis.py`
- `scripts/analysis/perf/zmq_rpc_perf_nightly.sh`
- `scripts/metrics/gen_kv_perf_report.py`
- `scripts/testing/bench/bootstrap_bench_cluster.sh`
- `scripts/testing/bench/run_dsbench_smoke_remote.sh`
- `scripts/testing/bench/run_kvtest_smoke_remote.sh`

## Evidence

Harness runs write `summary.json`, `steps.jsonl`, and either `perf_hotspots.md` or `bench_results.json`,
depending on profile. The report must include:

- Evidence: source file or command and the metric/call-stack rows used.
- Judgment: why the hotspot or regression is credible.
- Suggestion: the next optimization or narrowing experiment.
- Recheck: exact command/profile to rerun after a change.

## Pass/Fail Criteria

- Pass: at least one supported evidence source is parsed and ranked.
- Fail: no supported evidence source exists, commands fail, or the report lacks evidence/judgment/suggestion/recheck sections.
- Evaluation: rank hotspots by observed cost or regression size, not by intuition.
