#!/usr/bin/env bash
# Quick cmake puncture on xqyun (hetero OFF, WITH_TESTS ON) — isolated build dir.
# Log/pid live OUTSIDE build dir because build.sh may wipe -B.
set -euo pipefail
REMOTE="${REMOTE:-xqyun-32c32g}"
REPO="${REPO:-/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct}"
BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"
META="${META:-/root/workspace/nds-ssd-hbm-meta}"
JOBS="${JOBS:-16}"

ssh -o BatchMode=yes "$REMOTE" "bash -lc '
set -e
mkdir -p ${META} ${BUILD}
cd ${REPO}
if [ -d /home/ds-thirdparty-cache ]; then export DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache; fi
echo BUILD_START=\$(date -Iseconds)
nohup bash build.sh -t build -X off -P off -B ${BUILD} -j ${JOBS} >${META}/nds_cmake_puncture.log 2>&1 &
echo \$! > ${META}/nds_cmake_puncture.pid
echo PID=\$(cat ${META}/nds_cmake_puncture.pid)
echo LOG=${META}/nds_cmake_puncture.log
sleep 3
if kill -0 \$(cat ${META}/nds_cmake_puncture.pid) 2>/dev/null; then
  echo STILL_RUNNING
  tail -n 20 ${META}/nds_cmake_puncture.log
else
  echo EARLY_EXIT
  tail -n 50 ${META}/nds_cmake_puncture.log || true
  exit 1
fi
'"
