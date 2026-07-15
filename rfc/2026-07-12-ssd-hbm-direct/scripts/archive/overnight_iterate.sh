#!/usr/bin/env bash
# Overnight iteration: Gate0 → Task1 UT → incremental verify loop.
# Appends progress to results.md; safe to re-run.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RFC="$(cd "$DIR/.." && pwd)"
RESULTS="$RFC/results.md"
# shellcheck disable=SC1091
source "${DIR}/gtest_filters.sh"
REMOTE="${REMOTE:-xqyun-32c32g}"
LLT="/root/workspace/build-ssd-hbm-direct/tests/st/ds_device_llt"
UT_NDS="/root/workspace/build-ssd-hbm-direct/tests/ut/ds_ut_nds"
POLL=90
MAX_WAIT=$((60 * 60))  # 60 min for initial build

log() { echo "[$(date -Iseconds)] $*"; }

append_results() {
  {
    echo ""
    echo "| $(date '+%H:%M') | $1 | $2 | $3 |"
  } >> "$RESULTS"
}

remote_llt_ready() {
  ssh -o BatchMode=yes -o ConnectTimeout=30 "$REMOTE" \
    "test -x ${LLT} && echo READY=1 || echo READY=0; \
     pgrep -f 'build.sh.*build-ssd-hbm-direct' >/dev/null && echo BUILD=1 || echo BUILD=0" 2>/dev/null || echo "READY=0 BUILD=?"
}

log "overnight_iterate start"
elapsed=0
while [[ $elapsed -lt $MAX_WAIT ]]; do
  st="$(remote_llt_ready)"
  if echo "$st" | grep -q 'READY=1'; then
    log "ds_device_llt ready"
    break
  fi
  if echo "$st" | grep -q 'BUILD=0'; then
    log "build ended without executable llt"
    append_results "Gate0 build" "FAIL" "build stopped, llt missing"
    bash "$DIR/check_cmake_puncture_xqyun.sh" | tail -20
    exit 1
  fi
  log "wait build (${elapsed}s) ..."
  sleep "$POLL"
  elapsed=$((elapsed + POLL))
done

if [[ $elapsed -ge $MAX_WAIT ]]; then
  append_results "Gate0 build" "TIMEOUT" "no executable llt in ${MAX_WAIT}s"
  exit 1
fi

append_results "Gate0 build" "OK" "isolated ds_device_llt executable"

log "Gate0 ST (5 focused cases)"
set +e
st_out="$(bash "$DIR/run_existing_hetero_st_xqyun.sh" 2>&1)"
st_rc=$?
set -e
echo "$st_out" | tail -40
if [[ $st_rc -eq 0 ]]; then
  append_results "Gate0 ST (5 HeteroD2HTest)" "PASS" "rc=0 see latest_gate0_st.log"
else
  append_results "Gate0 ST (5 HeteroD2HTest)" "FAIL" "rc=${st_rc}"
fi

log "sync + Task1/2 UT"
set +e
ut_out="$(bash "$DIR/run_nds_ut_remote.sh" 2>&1)"
ut_rc=$?
set -e
echo "$ut_out" | tail -40
if [[ $ut_rc -eq 0 ]]; then
  append_results "ds_ut_nds" "PASS" "AlignmentGate + MockIpc UT"
else
  append_results "ds_ut_nds" "FAIL" "rc=${ut_rc}"
fi

log "overnight_iterate done gate0_rc=${st_rc} ut_rc=${ut_rc}"
exit $(( st_rc != 0 ? st_rc : ut_rc ))
