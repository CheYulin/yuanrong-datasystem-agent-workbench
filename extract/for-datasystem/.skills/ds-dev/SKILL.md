---
name: ds-dev
description: >-
  Daily development loop on tiantiyun: clang-format check, smoke, UT, ST, and change-type gates with clear
  failure evidence for PR readiness.
---

# Datasystem Dev Loop

## Purpose

Use this skill to answer: "Can this code change be safely proposed for review?"

## When to Use

- After editing datasystem C++ or test code.
- Before claiming a feature, bugfix, or refactor is verified.
- When choosing the minimum smoke/UT/ST gates from changed paths.

## Inputs

- Backend: default `cmake`; override with `--backend bazel` when the change is Bazel-specific.
- Node: default `tiantiyun-80c128g`.
- Profile: `dev.quick` (default) or `dev.default` (manual/nightly, includes build).

## Commands

```bash
bash .skills/ds-harness/scripts/verify_skill.sh --skill ds-dev
python3 .skills/ds-harness/scripts/ds_harness.py dev --profile dev.quick
python3 .skills/ds-harness/scripts/ds_harness.py dev --profile dev.default
python3 .skills/ds-harness/scripts/ds_harness.py dev --dry-run --json
```

Owned gates include:

- `.skills/ds-dev/scripts/lint/check_cpp_line_width.sh`
- `.skills/ds-dev/scripts/verify/smoke/run_smoke_remote.sh`
- `.skills/ds-dev/scripts/verify/ut/run_ut_remote.sh`
- `.skills/ds-dev/scripts/verify/st/run_st_remote.sh`
- `.skills/ds-dev/scripts/verify/validate_kv_executor.sh`
- `.skills/ds-dev/scripts/verify/validate_urma_tcp_observability_logs.sh`
- `.skills/ds-dev/scripts/verify/smoke/harness_zmq_metrics_e2e.sh`

## Evidence

Harness runs write `summary.json`, `steps.jsonl`, and `test_results.json` under
`results/ds-harness/<timestamp>-dev.quick/`.

## Pass/Fail Criteria

- Pass: format/lint checks and selected smoke/UT/ST/profile gates pass.
- Fail: any required gate fails, a C++ change lacks format evidence, or the summary cannot point to the failed command and log.
- Evaluation: report the failing layer first, then the exact command, log path, and next focused rerun.
