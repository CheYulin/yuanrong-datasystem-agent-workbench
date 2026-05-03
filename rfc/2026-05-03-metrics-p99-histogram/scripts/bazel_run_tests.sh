#!/usr/bin/env bash
# Remote Bazel test: metrics UT package + ST histogram_p99_perf_test (heavy ds_cc_test).
# Forces --define=enable_urma=false (same as bazel_build.sh).
# Default --test_output=all (BAZEL_TEST_OUTPUT) so ST std::cout / gtest lines appear when tests pass.
# Split UT and ST into two `bazel test` runs: with --keep_going on a single
# invocation, if one target fails *analysis* (e.g. ST missing on remote),
# Bazel can skip it and only run the others — UT then shows "4 tests" with no ST.
#
# After remote run, rsync log(s) matching bazel_test_*_${STAMP}.log into
# ${LOCAL_RESULTS} (default: ../results under this RFC).
#
# Usage:
#   bash scripts/bazel_run_tests.sh
#   REMOTE=user@host bash scripts/bazel_run_tests.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RFC_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOCAL_RESULTS="${LOCAL_RESULTS:-${RFC_ROOT}/results}"

REMOTE="${REMOTE:-root@xqyun-32c32g}"
REMOTE_DS="${REMOTE_DS:-/root/workspace/git-repos/yuanrong-datasystem}"
REMOTE_WB="${REMOTE_WB:-/root/workspace/git-repos/yuanrong-datasystem-agent-workbench}"
RFC_REL="rfc/2026-05-03-metrics-p99-histogram/results"
DS_OPENSOURCE_DIR_REMOTE="${DS_OPENSOURCE_DIR_REMOTE:-/root/.cache/yuanrong-datasystem-third-party}"
BAZEL_JOBS="${BAZEL_JOBS:-32}"
BAZEL_TEST_OUTPUT="${BAZEL_TEST_OUTPUT:-all}"
STAMP="$(date -u +%Y%m%dT%H%MZ)"

echo "REMOTE=${REMOTE}"
echo "REMOTE_DS=${REMOTE_DS}"
echo "STAMP=${STAMP}"
echo "BAZEL_TEST_OUTPUT=${BAZEL_TEST_OUTPUT}"
echo "LOCAL_RESULTS=${LOCAL_RESULTS}"
echo "(bazel: URMA off via --define=enable_urma=false)"
echo

ssh_rc=0
ssh "${REMOTE}" env \
  STAMP="${STAMP}" \
  REMOTE_DS="${REMOTE_DS}" \
  REMOTE_WB="${REMOTE_WB}" \
  RFC_REL="${RFC_REL}" \
  DS_OPENSOURCE_DIR_REMOTE="${DS_OPENSOURCE_DIR_REMOTE}" \
  BAZEL_JOBS="${BAZEL_JOBS}" \
  BAZEL_TEST_OUTPUT="${BAZEL_TEST_OUTPUT}" \
  BAZEL_EXTRA_OPTS="${BAZEL_EXTRA_OPTS:-}" \
  bash -s <<'REMOTE_SCRIPT' || ssh_rc=$?
set -euo pipefail
export DS_OPENSOURCE_DIR="${DS_OPENSOURCE_DIR_REMOTE}"
mkdir -p "${DS_OPENSOURCE_DIR}"

RFC_RESULTS="${REMOTE_WB}/${RFC_REL}"
cd "${REMOTE_DS}"

echo "=== host ==="
hostname
date -u

echo "=== datasystem git ==="
GIT_SHA="$(git rev-parse HEAD)"
GIT_SHORT="$(git rev-parse --short HEAD)"
echo "${GIT_SHA}"
git status -sb || true

if [[ -d "${REMOTE_WB}" ]]; then
  mkdir -p "${RFC_RESULTS}"
  LOG="${RFC_RESULTS}/bazel_test_${GIT_SHORT}_${STAMP}.log"
else
  LOG="/tmp/yuanrong-datasystem_metrics_bazel_${GIT_SHORT}_${STAMP}.log"
  echo "WARN: workbench repo not at ${REMOTE_WB}; log -> ${LOG}"
fi

echo "=== bazel test metrics UT (package) ==="
command -v bazel
set -o pipefail
ut_rc=0
st_rc=0

BAZEL_TEST_COMMON=(
  --jobs="${BAZEL_JOBS}"
  --define=enable_urma=false
  --test_output="${BAZEL_TEST_OUTPUT}"
  ${BAZEL_EXTRA_OPTS}
)

bazel test \
  '//tests/ut/common/metrics/...' \
  "${BAZEL_TEST_COMMON[@]}" \
  --keep_going \
  2>&1 | tee "${LOG}" || ut_rc="${PIPESTATUS[0]}"

echo "=== bazel test ST //tests/st/common/metrics:histogram_p99_perf_test ===" \
  | tee -a "${LOG}"

# If BUILD still uses cc_binary, Bazel reports "0 test targets" and never runs TEST_F.
echo "=== verify ST target is cc_test (required for bazel test) ===" | tee -a "${LOG}"
if ! bazel query 'kind("cc_test", //tests/st/common/metrics:histogram_p99_perf_test)' \
  2>/dev/null | grep -q '^//'; then
  bazel query '//tests/st/common/metrics:histogram_p99_perf_test' --output=kind 2>&1 | tee -a "${LOG}" || true
  {
    echo "ERROR: //tests/st/common/metrics:histogram_p99_perf_test is not a cc_test here."
    echo "  Symptom: bazel test reports 'Found ... 0 test targets' / 'No test targets were found'."
    echo "  Fix: rsync yuanrong-datasystem so tests/st/common/metrics/BUILD.bazel uses ds_cc_test (cc_test),"
    echo "       not cc_binary. Then: bazel query 'kind(cc_test, //tests/st/common/metrics:all)'"
  } | tee -a "${LOG}"
  exit 1
fi

# No --keep_going: analysis/build failure must not silently skip ST after UT passed.
bazel test \
  '//tests/st/common/metrics:histogram_p99_perf_test' \
  "${BAZEL_TEST_COMMON[@]}" \
  2>&1 | tee -a "${LOG}" || st_rc="${PIPESTATUS[0]}"

if [[ "${ut_rc}" -ne 0 || "${st_rc}" -ne 0 ]]; then
  echo "FAIL: ut_exit=${ut_rc} st_exit=${st_rc}" | tee -a "${LOG}"
  exit 1
fi

echo "=== log file ==="
ls -la "${LOG}"
REMOTE_SCRIPT

echo
echo "=== rsync remote bazel log(s) → ${LOCAL_RESULTS} ==="
mkdir -p "${LOCAL_RESULTS}"
set +e
# Prefer filter sync (no remote-shell glob): remote zsh may set NOMATCH and abort rsync when * matches
# nothing; see second-stage /tmp fallback below.
rsync -avz \
  --include="bazel_test_*_${STAMP}.log" \
  --exclude='*' \
  "${REMOTE}:${REMOTE_WB}/${RFC_REL}/" \
  "${LOCAL_RESULTS}/"
rsync_rc_wb=$?

rsync_rc_tmp=0
shopt -s nullglob
have_wb=( "${LOCAL_RESULTS}"/bazel_test_*_"${STAMP}".log )
shopt -u nullglob
if [[ "${#have_wb[@]}" -eq 0 ]]; then
  # Log path when workbench is missing uses /tmp; use sh for ls so zsh on the remote cannot error on
  # an empty glob.
  tmp_list="$(ssh "${REMOTE}" \
    "sh -c 'ls /tmp/yuanrong-datasystem_metrics_bazel_*_${STAMP}.log 2>/dev/null'" || true)"
  if [[ -n "${tmp_list}" ]]; then
    while IFS= read -r rf; do
      [[ -n "${rf}" ]] || continue
      rsync -avz "${REMOTE}:${rf}" "${LOCAL_RESULTS}/" || rsync_rc_tmp=$?
    done <<< "${tmp_list}"
  fi
fi
set -e

shopt -s nullglob
pulled=( "${LOCAL_RESULTS}"/bazel_test_*_"${STAMP}".log "${LOCAL_RESULTS}"/yuanrong-datasystem_metrics_bazel_*_"${STAMP}".log )
shopt -u nullglob
if [[ "${#pulled[@]}" -eq 0 ]]; then
  echo "WARN: No log matching *_${STAMP}.log under ${LOCAL_RESULTS} (rsync_wb=${rsync_rc_wb} rsync_tmp=${rsync_rc_tmp})."
  echo "  Check remote path ${REMOTE}:${REMOTE_WB}/${RFC_REL}/ or /tmp/"
else
  echo "Pulled:"
  ls -la "${pulled[@]}"
fi

if [[ "${ssh_rc}" -ne 0 ]]; then
  echo "Remote bazel test failed (exit ${ssh_rc}); log may still be synced above."
  exit "${ssh_rc}"
fi
