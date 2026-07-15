#!/usr/bin/env bash
set -uo pipefail
BASE=/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct
LOG="$BASE/overnight_verify.log"
PREP="$BASE/scripts/prepare_build_and_st_xqyun.sh"
UT="$BASE/scripts/run_nds_ut_remote.sh"
echo "overnight start $(date -Iseconds)" > "$LOG"
GATE0=GATE0_UNKNOWN
UTST=UT_UNKNOWN
for i in $(seq 1 40); do
  if ssh -o BatchMode=yes -o ConnectTimeout=25 xqyun-32c32g true 2>/dev/null; then
    echo "ssh_ok poll=$i $(date -Iseconds)" >> "$LOG"
    break
  fi
  echo "ssh_wait poll=$i $(date -Iseconds)" >> "$LOG"
  sleep 90
done
if ! ssh -o BatchMode=yes -o ConnectTimeout=25 xqyun-32c32g true 2>/dev/null; then
  echo "GATE0_FAIL ssh_blocked" >> "$LOG"
  echo "UT_FAIL ssh_blocked" >> "$LOG"
  exit 1
fi
echo "=== Gate0 ST ===" >> "$LOG"
set +e
bash "$PREP" --skip-sync --skip-build 2>&1 | tee "$BASE/gate0_st_run.log"
GEC=$?
set -e
if [[ $GEC -eq 0 ]] && grep -qiE 'HeteroD2H|PASSED|tests passed' "$BASE/gate0_st_run.log" 2>/dev/null; then
  GATE0=GATE0_PASS
elif [[ $GEC -eq 0 ]]; then
  GATE0=GATE0_PASS
else
  GATE0=GATE0_FAIL
fi
echo "$GATE0 ec=$GEC" >> "$LOG"
echo "=== Task1 UT ===" >> "$LOG"
set +e
bash "$UT" 2>&1 | tee "$BASE/nds_ut_run.log"
UEC=$?
set -e
if [[ $UEC -eq 0 ]] && grep -qiE 'AlignmentGate|PASSED|OK' "$BASE/nds_ut_run.log" 2>/dev/null; then
  UTST=UT_PASS
elif [[ $UEC -eq 0 ]]; then
  UTST=UT_PASS
else
  UTST=UT_FAIL
fi
echo "$UTST ec=$UEC" >> "$LOG"
echo "DONE $(date -Iseconds)" >> "$LOG"
