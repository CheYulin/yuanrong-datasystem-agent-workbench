#!/usr/bin/env bash
# Full client direct-read regression on tiantiyun: functional (24) + perf (3).
#
# Single ENABLE_PERF=on build (-j40), then:
#   1) ClientDirectRead functional (includes LEVEL2; excludes LatencyBenchmark names)
#   2) CrossNode*LatencyBenchmark with DS_DIRECT_READ_PERF=1
#
# Usage:
#   bash scripts/testing/verify/run_direct_read_regression_remote.sh \
#     --worktree client-direct-read-flow --sync-local
#
# Faster perf gate (256KB cold only):
#   DS_DIRECT_READ_PERF_ITERS=100 DS_DIRECT_READ_PERF_WARMUP=10 \
#     bash scripts/testing/verify/run_direct_read_regression_remote.sh --sync-local
set -euo pipefail

VERIFY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${VERIFY_DIR}/../../lib" && pwd)"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/timing.sh"

WORKTREE_SLUG=""
SYNC_LOCAL=0
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worktree) WORKTREE_SLUG="$2"; shift 2 ;;
    --sync-local) SYNC_LOCAL=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help)
      sed -n '1,18p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${WORKTREE_SLUG}" ]] || WORKTREE_SLUG="client-direct-read-flow"

export BUILD_JOBS="${BUILD_JOBS:-40}"
export ENABLE_PERF=on
export ST_CTEST_LABEL_EXCLUDE="none"

VERIFY_ARGS=(--worktree "${WORKTREE_SLUG}" --phase st)
[[ "${SYNC_LOCAL}" -eq 1 ]] && VERIFY_ARGS+=(--sync-local)
[[ "${SKIP_BUILD}" -eq 1 ]] && VERIFY_ARGS+=(--skip-build)

FUNC_LOG="/tmp/direct_read_func_regression.log"
PERF_LOG="/tmp/direct_read_perf_regression.log"

banner "Direct read regression: functional + perf (jobs=${BUILD_JOBS})"

# --- Phase 1: functional (24 cases, incl. LEVEL2; no perf benchmarks) ---
export ST_CTEST_REGEX='ClientDirectRead'
export ST_CTEST_EXCLUDE='LatencyBenchmark'
unset DS_DIRECT_READ_PERF || true

log_info "Phase 1/2: functional ST (ClientDirectRead, -E LatencyBenchmark)"
set +e
bash "${VERIFY_DIR}/run_worktree_verify_remote.sh" "${VERIFY_ARGS[@]}" 2>&1 | tee "${FUNC_LOG}"
FUNC_RC=${PIPESTATUS[0]}
set -e

if [[ "${FUNC_RC}" -ne 0 ]]; then
  log_error "Functional regression FAILED (exit ${FUNC_RC}). See ${FUNC_LOG}"
  exit "${FUNC_RC}"
fi
log_info "Functional regression PASSED"

# --- Phase 2: perf (3 benchmarks; rebuild skipped) ---
export DS_DIRECT_READ_PERF=1
export DS_DIRECT_READ_PERF_ITERS="${DS_DIRECT_READ_PERF_ITERS:-100}"
export DS_DIRECT_READ_PERF_WARMUP="${DS_DIRECT_READ_PERF_WARMUP:-10}"
export DS_DIRECT_READ_PERF_SIZE="${DS_DIRECT_READ_PERF_SIZE:-262144}"
export DS_DIRECT_READ_PERF_MODE="${DS_DIRECT_READ_PERF_MODE:-local}"
export ST_CTEST_REGEX='CrossNode.*LatencyBenchmark'
export ST_CTEST_EXCLUDE=''

PERF_ARGS=(--worktree "${WORKTREE_SLUG}" --phase st --skip-build)
[[ "${SYNC_LOCAL}" -eq 1 ]] && PERF_ARGS+=(--sync-local)

log_info "Phase 2/2: perf ST (regex=CrossNode.*LatencyBenchmark, iters=${DS_DIRECT_READ_PERF_ITERS})"
set +e
bash "${VERIFY_DIR}/run_worktree_verify_remote.sh" "${PERF_ARGS[@]}" 2>&1 | tee "${PERF_LOG}"
PERF_RC=${PIPESTATUS[0]}
set -e

if [[ "${PERF_RC}" -ne 0 ]]; then
  log_error "Perf regression FAILED (exit ${PERF_RC}). See ${PERF_LOG}"
  exit "${PERF_RC}"
fi

if ! grep -q 'DIRECT_READ_PERF_JSON=' "${PERF_LOG}"; then
  log_error "Perf ST passed but no DIRECT_READ_PERF_JSON lines found"
  exit 1
fi

log_info "Perf regression PASSED"
log_info "Full direct-read regression OK (functional 24 + perf 3)"
