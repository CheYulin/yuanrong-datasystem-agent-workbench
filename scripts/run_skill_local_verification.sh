#!/usr/bin/env bash
# GitCode / commit-message user-path checks — run LOCAL (WSL), not on tiantiyun/xqyun.
# Evidence: results/skill_local_verification_<stamp>/
set -uo pipefail
FAILED=0

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DS="${DATASYSTEM_ROOT:-${ROOT}/../yuanrong-datasystem}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${ROOT}/results/skill_local_verification_${STAMP}"
mkdir -p "${OUT}"

on_remote_verify_host() {
  [[ "${VERIFY_ON_NODE:-}" == "tiantiyun-80c128g" ]] && return 0
  [[ "${VERIFY_ON_NODE:-}" == "xqyun-32c32g" ]] && return 0
  [[ "$(hostname -s 2>/dev/null || true)" == "tiantiyun-80c128g" ]] && return 0
  [[ "$(hostname -s 2>/dev/null || true)" == "xqyun-32c32g" ]] && return 0
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
run_allow_fail() {
  local name="$1"; shift
  log "=== ${name} (non-fatal) ==="
  {
    echo "\$ $*"
    "$@"
  } >"${OUT}/${name}.log" 2>&1 && echo "PASS ${name}" >>"${OUT}/summary.log" || {
    echo "WARN ${name} (exit $?)" >>"${OUT}/summary.log"
    tail -15 "${OUT}/${name}.log" >>"${OUT}/summary.log"
    return 0
  }
}

log "OUT=${OUT} DS=${DS} NODE=$(hostname -s 2>/dev/null || echo local)"

if on_remote_verify_host; then
  log "ERROR: GitCode checks are local-only. Run from WSL, not tiantiyun/xqyun."
  exit 2
fi

# --- wb-docs (commit draft) ---
run_allow_fail wb_docs_commit_help bash "${ROOT}/scripts/development/git/generate_commit_message.sh" --help
run_allow_fail wb_docs_commit bash "${ROOT}/scripts/development/git/generate_commit_message.sh" --all

# --- ds-pr-flow ---
run_allow_fail ds_pr_review_help bash -c "python3.11 '${DS}/.skills/ds-pr-review/scripts/review_pr.py' --help 2>/dev/null || python3 '${DS}/.skills/ds-pr-review/scripts/review_pr.py' --help"
run ds_pr_create_help python3 "${DS}/.skills/ds-create-pr/scripts/create_pr.py" --help
run ds_pr_template test -f "${DS}/.gitee/PULL_REQUEST_TEMPLATE/PULL_REQUEST_TEMPLATE.zh-cn.md"

log "Done. Evidence: ${OUT} (failed_steps=${FAILED})"
cat "${OUT}/summary.log"
exit $(( FAILED > 0 ? 1 : 0 ))
