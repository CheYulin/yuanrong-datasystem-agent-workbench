---
name: ds-build
description: >-
  Build yuanrong-datasystem with CMake or Bazel on tiantiyun, collect total/step/module timing,
  identify long-tail targets, summarize failures, and suggest build optimizations.
---

# Datasystem Build

## Purpose

Use this skill when the task is about compiling datasystem, comparing CMake vs Bazel, understanding build cost,
or explaining why builds are slow.

## When to Use

- Before deeper verification when the change may break compilation.
- When comparing `cmake` and `bazel` build backends.
- When a build is slow and needs long-tail target/module analysis.

## Inputs

- Backend: `cmake` or `bazel`.
- Node: default `tiantiyun-80c128g` from `.skills/ds-harness/references/nodes.yaml`.
- Profile: `build.quick` or `build.full` from `.skills/ds-harness/references/profiles.yaml`.

## Commands

```bash
python3 .skills/ds-harness/scripts/ds_harness.py build --backend cmake --profile build.quick
python3 .skills/ds-harness/scripts/ds_harness.py build --backend bazel --profile build.full
python3 .skills/ds-harness/scripts/ds_harness.py build --backend cmake --dry-run --json
```

Compatibility wrappers may still call `.skills/ds-build/scripts/build_cmake.sh`, `.skills/ds-build/scripts/build_bazel.sh`, or
`.skills/ds-build/scripts/rsync_datasystem_remote_bazel.sh`, but the owner is `ds-build`.

## Evidence

Harness runs write `results/ds-harness/<timestamp>-<profile>/` with:

- `summary.json` for status, backend, node, profile, git SHA, and total elapsed time.
- `steps.jsonl` for command, elapsed time, exit code, and log path per step.
- `build_timing.csv` for build step/module timings and long-tail identification.

## Pass/Fail Criteria

- Pass: selected backend builds successfully and evidence files are present.
- Fail: any build step exits non-zero, required timing evidence is missing, or dry-run JSON does not match the harness schema.
- Evaluation: report Top N slow steps/modules and provide one concrete optimization hint when long-tail time is detected.
