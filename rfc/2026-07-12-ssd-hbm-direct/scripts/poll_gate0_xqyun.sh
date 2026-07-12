#!/usr/bin/env bash
set -uo pipefail
SCRIPT=/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/scripts/check_cmake_puncture_xqyun.sh
LOG=/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/gate0_poll.log
PREP=/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/scripts/prepare_build_and_st_xqyun.sh
GATELOG=/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/gate0_st_run.log
echo "poll start $(date -Iseconds)" > "$LOG"
READY=0
for i in $(seq 1 30); do
  echo "=== poll $i $(date -Iseconds) ===" >> "$LOG"
  bash "$SCRIPT" >> "$LOG" 2>&1 || true
  if tail -8 "$LOG" | grep -q "ds_device_llt" && ! tail -8 "$LOG" | grep -q "cannot access"; then
    echo "LLT_READY at poll $i" >> "$LOG"
    READY=1
    break
  fi
  sleep 90
done
if [[ $READY -eq 1 ]]; then
  echo "running prepare ST $(date -Iseconds)" >> "$LOG"
  set +e
  bash "$PREP" --skip-sync --skip-build 2>&1 | tee -a "$GATELOG"
  EC=${PIPESTATUS[0]}
  if grep -qiE 'GATE0_PASS|All tests passed|\[  PASSED  \]' "$GATELOG" 2>/dev/null && [[ $EC -eq 0 ]]; then
    echo "GATE0_PASS" >> "$LOG"
  elif [[ $EC -eq 0 ]]; then
    echo "GATE0_PASS exit0" >> "$LOG"
  else
    echo "GATE0_FAIL ec=$EC" >> "$LOG"
  fi
else
  echo "GATE0_FAIL timeout_no_llt" >> "$LOG"
fi
