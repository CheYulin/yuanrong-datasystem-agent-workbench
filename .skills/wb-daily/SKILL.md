---
name: wb-daily
description: >-
  Full daily quality build on tiantiyun: all smoke/UT/ST, coverage, perf regression, long-tail and flake
  statistics, with trend-ready evidence.
---

# Workbench Daily Build

## Purpose

Use this skill for scheduled or manually requested full validation, not for the normal short developer loop.

## When to Use

- Nightly/daily all-in quality runs.
- Before a release branch or large integration.
- When collecting coverage, performance regression, flake, and long-tail trends.

## Inputs

- Backend: usually `cmake`; include `bazel` where profile requires it.
- Node: default `tiantiyun-80c128g`.
- Profile: `daily.full`.
- Thresholds: coverage and perf regression thresholds from `scripts/harness/profiles.yaml`.

## Commands

```bash
python3 scripts/harness/ds_harness.py daily --profile daily.full
python3 scripts/harness/ds_harness.py daily --profile daily.full --dry-run --json
```

The profile owns the full smoke/UT/ST sweep, coverage collection via `yuanrong-datasystem/build.sh -c`, and
performance regression via `scripts/analysis/perf/zmq_rpc_perf_nightly.sh`.

## Evidence

Harness runs write:

- `summary.json` and `steps.jsonl` for the full run.
- `test_results.json` for full smoke/UT/ST counts, failures, skips, long-tail tests, and flake candidates.
- `coverage.json` for line/function/branch coverage and threshold decisions.
- `perf_hotspots.md` for regression evidence and recommended follow-up.

## Pass/Fail Criteria

- Pass: all required full gates pass, coverage meets thresholds, and perf regression stays within threshold.
- Fail: any full gate fails, coverage is below threshold, P95 regression exceeds threshold, or required daily evidence is missing.
- Evaluation: summarize pass/fail by category and include trend-ready metrics rather than only raw logs.
