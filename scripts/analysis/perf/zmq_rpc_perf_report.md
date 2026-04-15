# ZMQ RPC Nightly Perf Report

- Run ID: `{{RUN_ID}}`
- Generated At: `{{GENERATED_AT}}`
- Remote: `{{REMOTE}}`
- Build Dir: `{{BUILD_DIR}}`
- Iterations Per Scenario: `{{ITERATIONS}}`
- Baseline: `{{BASELINE_PATH}}`
- Regression Threshold: `{{REGRESSION_THRESHOLD_PCT}}%`
- Max Fail Ratio: `{{MAX_FAIL_RATIO}}`
- Overall Status: `{{OVERALL_STATUS}}`

## Overall Note

{{OVERALL_NOTE}}

## Scenario Latency Summary

| Scenario | Samples | Fail Ratio | P50 (ms) | P95 (ms) | P99 (ms) | Regression vs Baseline (P95) | Status |
|---|---:|---:|---:|---:|---:|---:|---|
{{SCENARIO_TABLE}}

## ZMQ Perf Key Summary

> Percentiles are computed from per-iteration `avgTime` (converted to microseconds) in Perf logs.

| Scenario | Perf Key | Samples | P50 Avg (us) | P95 Avg (us) | P99 Avg (us) |
|---|---|---:|---:|---:|---:|
{{PERF_TABLE}}

## Artifacts

{{ARTIFACTS}}

## Pass Criteria

1. Fail ratio per scenario <= `{{MAX_FAIL_RATIO}}`.
2. If baseline is provided, P95 regression per scenario <= `{{REGRESSION_THRESHOLD_PCT}}%`.
3. No systemic environment breakage in `raw_logs/` (etcd absent, port conflict storms, disk full).
