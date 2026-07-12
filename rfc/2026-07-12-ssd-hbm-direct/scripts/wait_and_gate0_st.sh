#!/usr/bin/env bash
# Poll until ds_device_llt exists or build fails; then run Gate 0 ST.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RFC="$(cd "$DIR/.." && pwd)"
RESULTS="$RFC/results.md"
LLT_PATH="/root/workspace/build-ssd-hbm-direct/tests/st/ds_device_llt"
POLL=90
MAX=$((60*45))  # 45 min
elapsed=0

log() { echo "[$(date -Iseconds)] $*"; }

check_llt() {
  ssh -o BatchMode=yes -o ConnectTimeout=20 xqyun-32c32g \
    "test -x ${LLT_PATH} && echo HAS_LLT=1 || echo HAS_LLT=0; \
     if [[ -f /root/workspace/nds-ssd-hbm-meta/nds_cmake_puncture.pid ]]; then \
       PID=\$(cat /root/workspace/nds-ssd-hbm-meta/nds_cmake_puncture.pid); \
       kill -0 \$PID 2>/dev/null && echo BUILD_RUNNING=1 || echo BUILD_RUNNING=0; \
     else echo BUILD_RUNNING=0; fi"
}

append() {
  {
    echo ""
    echo "$1"
  } >> "$RESULTS"
}

log "gate0 waiter start"
while [[ $elapsed -lt $MAX ]]; do
  st="$(check_llt 2>&1)" || st="ssh_fail"
  if echo "$st" | grep -q 'HAS_LLT=1'; then
    log "ds_device_llt ready"
    break
  fi
  if echo "$st" | grep -q 'BUILD_RUNNING=0'; then
    log "build stopped without llt"
    bash "$DIR/check_cmake_puncture_xqyun.sh" | tail -30
    append "### $(date -Iseconds) FAIL: build ended without ds_device_llt"
    exit 1
  fi
  log "waiting (${elapsed}s) ..."
  sleep "$POLL"
  elapsed=$((elapsed + POLL))
done

if [[ $elapsed -ge $MAX ]]; then
  append "### $(date -Iseconds) FAIL: timeout waiting for ds_device_llt"
  exit 1
fi

append "### $(date -Iseconds) Build OK: isolated ds_device_llt exists"

log "running HeteroD2H ST"
set +e
out="$(bash "$DIR/prepare_build_and_st_xqyun.sh" --skip-sync --skip-build 2>&1)"
rc=$?
set -e
append "### $(date -Iseconds) Gate 0 ST rc=${rc}"
append '```'
echo "$out" | tail -60 >> "$RESULTS"
append '```'

exit "$rc"
