#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes -o ConnectTimeout=20 xqyun-32c32g 'bash -s' <<'EOF'
set +e
META=/root/workspace/nds-ssd-hbm-meta
BUILD=/root/workspace/build-ssd-hbm-direct
echo --- pid ---
if [[ -f $META/nds_cmake_puncture.pid ]]; then
  PID=$(cat $META/nds_cmake_puncture.pid)
  ps -p "$PID" -o pid,etime,cmd 2>&1 | head
else
  echo NO_PID
fi
echo --- log ---
tail -n 30 $META/nds_cmake_puncture.log 2>/dev/null || echo NO_LOG
echo --- llt ---
ls -la $BUILD/tests/st/ds_device_llt 2>&1 | head
EOF
