#!/usr/bin/env bash
# wb-html-publish user-path checks — run ON xqyun-32c32g only.
# Evidence: results/skill_html_verification_<stamp>/
set -uo pipefail
FAILED=0

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DS="${DATASYSTEM_ROOT:-${ROOT}/../yuanrong-datasystem}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${ROOT}/results/skill_html_verification_${STAMP}"
mkdir -p "${OUT}"

on_xqyun() {
  [[ "${VERIFY_ON_NODE:-}" == "xqyun-32c32g" ]] && return 0
  [[ "$(hostname -s 2>/dev/null || true)" == "xqyun-32c32g" ]] && return 0
  [[ -d /var/www/html/.git ]] && return 0
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

log "OUT=${OUT}"
log "NODE=$(hostname -s 2>/dev/null || echo unknown) VERIFY_ON_NODE=${VERIFY_ON_NODE:-}"

if ! on_xqyun; then
  log "ERROR: wb-html-publish verification runs on xqyun only."
  log "From local: bash scripts/harness/run_skill_html_verify_remote.sh"
  exit 2
fi

# --- wb-html-publish ---
run wb_html_git_help bash "${ROOT}/scripts/development/sync/publish_htmls_git.sh" --help
run_allow_fail wb_html_git_status bash "${ROOT}/scripts/development/sync/publish_htmls_git.sh" status
run_allow_fail wb_html_curl curl -sI "https://yche.me/research/skills-catalog-overview-20260619.html"
if [[ -d /var/www/html ]]; then
  run wb_html_repo_present test -d /var/www/html/.git
else
  log "SKIP wb_html_repo_present: /var/www/html not on this host"
fi
if command -v codegraph >/dev/null 2>&1 && [[ -d "${DS}" ]]; then
  run_allow_fail wb_html_codegraph bash -c "cd '${DS}' && codegraph status ."
else
  log "SKIP wb_html_codegraph: codegraph or DS missing"
fi

log "Done. Evidence: ${OUT} (failed_steps=${FAILED})"
cat "${OUT}/summary.log"
exit $(( FAILED > 0 ? 1 : 0 ))
