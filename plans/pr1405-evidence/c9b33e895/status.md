# PR1405 busy-heal retry window evidence

## Exact source

- Integration HEAD: `c9b33e895452ce3cdab520ae1404c4dd3d850f13`
- Base: `main/master=7507f139f` (rebase confirmed, 0 behind)
- Commit: `fix(worker): preserve busy-heal rate window`
- Diff: 2 files, `+27/-2` (`migrate_data_handler.h` and its existing UT)
- Tiantiyun source parity: checksum rsync over all regular tracked files reported no content delta.

## TDD evidence

- RED at the regressed `100ms/800ms` policy: deterministic minimum for three probes was `700ms`, not greater than the limiter's `1000ms` sliding window. The focused CTest failed in `0.07s`.
- GREEN after restoring the documented `200ms/1000ms` policy: the same focused CTest passed in `0.08s` (`0.28s` CTest total).
- The test calculates the retry schedule and does not depend on wall-clock sleeps.

Evidence: `busy-heal-red/`, `busy-heal-green/`.

## Exact-head validation on Tiantiyun

- CMake incremental build: 5 requested targets built, exit 0, `180.282s`; third-party cache `/home/ds-thirdparty-cache` reused.
- Affected UT: `14/14 PASS`, CTest `-j4`, `5.26s`.
- Design ST matrix: parallel first pass `24/27`; the three failures were startup/runfiles interference. With `TEST_SRCDIR` and `TEST_WORKSPACE` set per the existing test contract, isolated serial rerun was `3/3 PASS` in `89.98s`. Final matrix verdict: `27/27 PASS`.
- Busy-heal associated ST: `KVClientMemoryRebalanceLowRateTest.LowRateMigrateSucceedsAfterBusySelfHealRecovers`, `1/1 PASS`, `22.50s` (`22.67s` CTest total).

The three parallel-first-pass failures were:

- `MigrationTargetCombinedFaultTest.MigrationTargetFiltersScaleInSourceAndIsolatedPeerTogether`: serial PASS `24.26s`.
- `KVClientWriteRocksdbTest.TestNoneModeVoluntaryScaleDown`: initial coordinator startup refusal; serial PASS `22.83s`.
- `KVCacheMetaShardingTest.RemoveMetaByWorkerFullShardCoverage`: initial ETCD startup/range contention; serial PASS `42.72s`.

## Static gates

- `git diff --check main/master..HEAD`: PASS.
- `git clang-format --diff HEAD^ HEAD` on touched C++ files: PASS.
- sensitive scanner unit tests: `18/18 PASS`.
- sensitive scan of the additive busy-heal patch: 0 findings.

## Residual note

ETCD/coordinator-heavy STs share host resources and are not safely parallel as one flat `ctest -j4` bucket. Continue parallelizing independent cases, but serialize scale-down and metadata-sharding cases when they compete for process startup or ETCD resources.
