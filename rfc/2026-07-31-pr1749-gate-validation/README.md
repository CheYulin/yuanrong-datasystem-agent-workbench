# PR1749 Gate Validation

## Scope

- PR: `openeuler/yuanrong-datasystem!1749`
- Source branch: `yche-huawei/feat/worker-self-healing-main-20260716-squash-20260731`
- Exact source SHA: `9f88dde0eb5455b848e9932e97ddf41090e5afad`
- Base SHA: `7507f139fdb1222b29eb77542223a68dea6cd19f`
- Validation node: `tiantiyun-80c128g`
- Third-party cache: `/home/ds-thirdparty-cache`
- Build tree: `/home/cache/build-pr1405-8731-rebalance-gate-cached`
- Evidence root: `/home/cache/pr1405-squash-9f88-evidence`

The directory names retain historical `pr1405` text because the cached build tree was reused. Evidence identity is established by the exact SHA and the tracked-file Git blob manifest, not by the directory name.

## Rules

1. CMake build and focused CMake UT/ST are the primary evidence. Bazel is source-build evidence only.
2. Reuse the existing build tree and `/home/ds-thirdparty-cache`; do not clean or expunge.
3. Validate every gtest filter with `--gtest_list_tests`; zero matches and unexpected counts are failures.
4. UT buckets may run in parallel.
5. Object, KV, Etcd and Coordinator ST buckets may run in parallel for the first pass. Any failed case must be rerun alone after other ST processes have exited. Only a serial failure is a product/test failure verdict.
6. Stream/S9 is excluded from execution. It must remain build-compatible and has zero product/UT/ST diff in this squash.
7. The four PR-added slow, non-one-shot ST cases remain explicitly disabled and are reported as disabled, never as PASS.
8. Preserve XML, logs, exact filters, counts, timestamps, durations and exit codes under the evidence root.

## Scripts

- `scripts/verify_git_manifest.sh`: verifies a `git ls-files -s` manifest against a synced source tree whose `.git` file is not usable remotely.
- `scripts/pr1749_run_gtest_bucket.sh`: validates a filter count and runs one gtest bucket with complete evidence.
- `scripts/run_pr1749_matrix.sh`: runs the focused UT buckets in parallel, then the related ST buckets in parallel.
- `scripts/rerun_failed_cases.py`: extracts failed cases from XML and reruns them one by one serially.
- `scripts/run_pr1749_gate_failures.sh`: reproduces the four Stream and one KV timeout gate failures, then serially reruns every failed exact case.

## Current Evidence

- Tracked source verification: `3200` files checked, `0` mismatches.
- Focused build (`ds_ut_object`, `ds_st_object_cache`, `ds_st_kv_cache`): exit `0`.
- Default CMake build using the same cache/build tree: exit `0`, `2026-07-31 08:50:29` to `08:53:44`.
- UT/ST execution: in progress; do not mark cases PASS until their latest XML and exit files are summarized here.
- `RdmaObjectClientAuthorizationTest.TestBatchRemoteGet4` is not present in any Tiantiyun CMake ST binary for this build configuration. Record it as `NOT_BUILT`; do not count it as PASS or FAIL.
- Direct CMake gtest execution must set `TEST_SRCDIR=<synced-source-dir>` and `TEST_WORKSPACE=.`. Without them, OBS-backed ST searches for `mock_obs_service.py` relative to the standalone build tree and produces a harness failure.

## Verdict Taxonomy

- `PASS`: expected test count is nonzero and exact, process exit is zero, and XML has no failures/errors.
- `PARALLEL_FAIL`: first-pass bucket failed; not yet a product verdict.
- `SERIAL_FAIL`: exact case failed again when run alone; actionable product/test failure.
- `HARNESS_FAIL`: zero match, missing XML, binary/setup error, or no `[ RUN ]` entry.
- `FLAKY`: serial reruns disagree.
- `DISABLED`: intentionally excluded case; never counted as PASS.
