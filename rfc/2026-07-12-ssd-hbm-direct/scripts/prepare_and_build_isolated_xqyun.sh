#!/usr/bin/env bash
# Prepare isolated cmake build on xqyun with third-party cache (nodes.yaml).
# Does NOT reuse previous datasystem build trees / ds_device_llt.
set -euo pipefail

REMOTE="${REMOTE:-xqyun-32c32g}"
REPO="${REPO:-/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct}"
BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"
META="${META:-/root/workspace/nds-ssd-hbm-meta}"
# nodes.yaml: xqyun-32c32g.thirdparty_cache
CACHE="${DS_OPENSOURCE_DIR:-/root/.cache/yuanrong-datasystem-third-party}"
LOCAL="${LOCAL:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
IGNORE="${IGNORE:-/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/development/sync/sync_to_xqyun.rsyncignore}"
JOBS="${JOBS:-16}"

echo "== sync worktree =="
ssh -o BatchMode=yes "$REMOTE" "mkdir -p '$REPO' '$META'"
rsync -az --human-readable --info=stats2 --exclude-from="$IGNORE" "$LOCAL/" "${REMOTE}:${REPO}/"

echo "== verify cache =="
ssh -o BatchMode=yes "$REMOTE" "bash -lc '
set -e
test -d ${CACHE}/openssl_23b00df3cf1669c598eec1e4f433ef1ca8c9d7a2e90a858e28c531726b25e5ea
echo CACHE_OK=${CACHE}
ls ${CACHE} | wc -l
# stop previous puncture if any
if [[ -f ${META}/nds_cmake_puncture.pid ]]; then
  kill \$(cat ${META}/nds_cmake_puncture.pid) 2>/dev/null || true
fi
# clean isolated build only (never touch other trees)
rm -rf ${BUILD}
mkdir -p ${BUILD}
'"

echo "== start isolated build =="
ssh -o BatchMode=yes "$REMOTE" "bash -lc '
set -e
cd ${REPO}
export DS_OPENSOURCE_DIR=${CACHE}
echo BUILD_START=\$(date -Iseconds)
echo DS_OPENSOURCE_DIR=\$DS_OPENSOURCE_DIR
echo BUILD=${BUILD}
nohup bash build.sh -t build -X off -P off -B ${BUILD} -j ${JOBS} \
  >${META}/nds_cmake_puncture.log 2>&1 &
echo \$! > ${META}/nds_cmake_puncture.pid
sleep 5
if kill -0 \$(cat ${META}/nds_cmake_puncture.pid) 2>/dev/null; then
  echo STILL_RUNNING pid=\$(cat ${META}/nds_cmake_puncture.pid)
  # prove cache hit / no re-download of openssl
  grep -E \"Cache the third party|openssl|Building openssl|openssl build\" ${META}/nds_cmake_puncture.log | head -40 || true
  tail -n 30 ${META}/nds_cmake_puncture.log
else
  echo EARLY_EXIT
  tail -n 80 ${META}/nds_cmake_puncture.log || true
  exit 1
fi
'"
