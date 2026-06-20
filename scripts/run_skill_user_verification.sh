#!/usr/bin/env bash
# End-to-end user-path verification for the workbench skill taxonomy.
# Intended to run ON tiantiyun-80c128g (set VERIFY_ON_NODE or use harness wrapper).
# Evidence: results/skill_verification_<stamp>/
set -uo pipefail
FAILED=0

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${ROOT}/results/skill_verification_${STAMP}"
mkdir -p "${OUT}"

on_tiantiyun() {
  [[ "${VERIFY_ON_NODE:-}" == "tiantiyun-80c128g" ]] && return 0
  [[ "$(hostname -s 2>/dev/null || true)" == "tiantiyun-80c128g" ]] && return 0
  [[ "${ROOT}" == /root/workspace/git-repos/yuanrong-datasystem-agent-workbench ]] && return 0
  return 1
}

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "${OUT}/summary.log"; }
run() {
  local name="$1"; shift
  log "=== ${name} ==="
  {
    echo "\$ $*"
    "$@"
  } >"${OUT}/${name}.log" 2>&1 && echo "PASS ${name}" >>"${OUT}/summary.log" || {
    echo "FAIL ${name} (exit $?)" >>"${OUT}/summary.log"
    tail -20 "${OUT}/${name}.log" | tee -a "${OUT}/summary.log"
    FAILED=$((FAILED + 1))
    return 0
  }
}

log "OUT=${OUT}"
log "NODE=$(hostname -s 2>/dev/null || echo unknown) VERIFY_ON_NODE=${VERIFY_ON_NODE:-}"

if ! on_tiantiyun; then
  log "ERROR: run on tiantiyun only. From local: bash scripts/harness/run_skill_verification_remote.sh"
  exit 2
fi

run tdd_workbench bash "${ROOT}/scripts/run_skill_tests.sh"

run wb_build_cmake_dry python3 "${ROOT}/scripts/harness/ds_harness.py" build \
  --backend cmake --profile build.quick --dry-run --json --evidence-dir "${OUT}/wb-build-cmake"
run wb_build_bazel_dry python3 "${ROOT}/scripts/harness/ds_harness.py" build \
  --backend bazel --profile build.quick --dry-run --json --evidence-dir "${OUT}/wb-build-bazel"
run wb_dev_dry python3 "${ROOT}/scripts/harness/ds_harness.py" dev \
  --profile dev.default --dry-run --json --evidence-dir "${OUT}/wb-dev"
run wb_daily_dry python3 "${ROOT}/scripts/harness/ds_harness.py" daily \
  --profile daily.full --dry-run --json --evidence-dir "${OUT}/wb-daily"
run wb_perf_hotspot_dry python3 "${ROOT}/scripts/harness/ds_harness.py" perf \
  --profile perf.hotspot --dry-run --json --evidence-dir "${OUT}/wb-perf-hotspot"
run wb_perf_regression_dry python3 "${ROOT}/scripts/harness/ds_harness.py" perf \
  --profile perf.regression --dry-run --json --evidence-dir "${OUT}/wb-perf-regression"

run wb_docs_workbook test -f "${ROOT}/docs/observable/workbook/sheet1-call-chain.md"
run wb_docs_fema_help python3 "${ROOT}/scripts/analysis/generate_bugfix_fema_report.py" --help
run wb_html_publish_script test -x "${ROOT}/scripts/development/sync/publish_htmls_git.sh"

log "Done. Evidence: ${OUT} (failed_steps=${FAILED})"
cat "${OUT}/summary.log"
exit $(( FAILED > 0 ? 1 : 0 ))
