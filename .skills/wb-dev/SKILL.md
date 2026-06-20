---
name: wb-dev
description: >-
  Daily development loop on tiantiyun: clang-format check, smoke, UT, ST, and change-type gates with clear
  failure evidence for PR readiness.
---

# Workbench Dev Loop

## Purpose

Use this skill to answer: "Can this code change be safely proposed for review?"

## When to Use

- After editing datasystem C++ or test code.
- Before claiming a feature, bugfix, or refactor is verified.
- When choosing the minimum smoke/UT/ST gates from changed paths.

## Inputs

- Backend: default `cmake`; override with `--backend bazel` when the change is Bazel-specific.
- Node: default `tiantiyun-80c128g`.
- Profile: `dev.default` unless a narrower profile is justified.

## Commands

```bash
python3 scripts/harness/ds_harness.py dev --profile dev.default
python3 scripts/harness/ds_harness.py dev --backend bazel --profile dev.default
python3 scripts/harness/ds_harness.py dev --dry-run --json
```

Owned gates include:

- `scripts/lint/check_cpp_line_width.sh`
- `scripts/testing/verify/smoke/run_smoke_remote.sh`
- `scripts/testing/verify/ut/run_ut_remote.sh`
- `scripts/testing/verify/st/run_st_remote.sh`
- `scripts/testing/verify/validate_kv_executor.sh`
- `scripts/testing/verify/validate_urma_tcp_observability_logs.sh`
- `scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh`

## Evidence

Harness runs write `summary.json`, `steps.jsonl`, and `test_results.json` under
`results/harness/<timestamp>-dev.default/`.

## Pass/Fail Criteria

- Pass: format/lint checks and selected smoke/UT/ST/profile gates pass.
- Fail: any required gate fails, a C++ change lacks format evidence, or the summary cannot point to the failed command and log.
- Evaluation: report the failing layer first, then the exact command, log path, and next focused rerun.
