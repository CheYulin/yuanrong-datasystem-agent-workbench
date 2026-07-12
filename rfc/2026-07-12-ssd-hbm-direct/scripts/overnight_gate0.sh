#!/usr/bin/env bash
# Wait for isolated build, run Gate 0 ST, append to results.md locally.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RFC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS="${RFC_DIR}/results.md"
BUILD_LOG_MARKER="HAS_DEVICE_LLT=1"
MAX_WAIT_SEC="${MAX_WAIT_SEC:-3600}"
POLL_SEC="${POLL_SEC:-120}"

log() { echo "[$(date -Iseconds)] $*"; }

append_results() {
  local line="$1"
  echo "| $(date +%H:%M) | $line |" >> "${RESULTS}.tmp"
}

log "overnight runner start max_wait=${MAX_WAIT_SEC}s poll=${POLL_SEC}s"
elapsed=0
while [[ "${elapsed}" -lt "${MAX_WAIT_SEC}" ]]; do
  out="$(bash "${SCRIPT_DIR}/check_cmake_puncture_xqyun.sh" 2>&1)" || true
  if echo "$out" | grep -q 'HAS_DEVICE_LLT=1'; then
    log "build artifact ready"
    break
  fi
  if echo "$out" | grep -q 'DONE_OR_DEAD\|NO_PID'; then
    if ! echo "$out" | grep -q 'RUNNING'; then
      # build ended but no llt — capture tail
      log "build process ended without ds_device_llt"
      echo "$out" | tail -20
      {
        echo ""
        echo "### $(date -Iseconds) Build ended without ds_device_llt"
        echo '```'
        echo "$out" | tail -40
        echo '```'
      } >> "${RESULTS}"
      exit 1
    fi
  fi
  pct="$(echo "$out" | grep -oE '\[[[:space:]]*[0-9]+%\]' | tail -1 || echo '?')"
  log "still building ${pct} elapsed=${elapsed}s"
  sleep "${POLL_SEC}"
  elapsed=$((elapsed + POLL_SEC))
done

if [[ "${elapsed}" -ge "${MAX_WAIT_SEC}" ]]; then
  log "timeout waiting for build"
  echo "### $(date -Iseconds) TIMEOUT waiting for ds_device_llt" >> "${RESULTS}"
  exit 1
fi

log "running Gate 0 ST"
set +e
st_out="$(bash "${SCRIPT_DIR}/prepare_build_and_st_xqyun.sh" --skip-sync --skip-build 2>&1)"
st_rc=$?
set -e

{
  echo ""
  echo "### $(date -Iseconds) Gate 0 ST (rc=${st_rc})"
  echo '```'
  echo "$st_out" | tail -80
  echo '```'
} >> "${RESULTS}"

if [[ "${st_rc}" -ne 0 ]]; then
  log "Gate 0 ST FAILED rc=${st_rc}"
  exit "${st_rc}"
fi

log "Gate 0 PASS"
echo "### $(date -Iseconds) Gate 0 PASS — ready for Task 1" >> "${RESULTS}"
exit 0
