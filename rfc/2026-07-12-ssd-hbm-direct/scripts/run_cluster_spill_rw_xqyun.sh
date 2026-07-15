#!/usr/bin/env bash
# Cluster E2E: NdsClusterSpillRwTest on xqyun isolation tree.
set -euo pipefail

LOCAL="${LOCAL:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
REMOTE_HOST="${REMOTE_HOST:-xqyun-32c32g}"
REMOTE_REPO="${REMOTE_REPO:-/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct}"
BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"
CLEAN_BUILD="${CLEAN_BUILD:-0}"

rsync -az --delete --exclude='build' --exclude='.git' "${LOCAL}/" "${REMOTE_HOST}:${REMOTE_REPO}/"

ssh "${REMOTE_HOST}" bash -lc "
set -euo pipefail
export DS_OPENSOURCE_DIR=/root/.cache/yuanrong-datasystem-third-party
cd '${REMOTE_REPO}'
if [ '${CLEAN_BUILD}' = '1' ]; then rm -rf '${BUILD}'
fi
bash build.sh -t build -X off -P off -B '${BUILD}' -j 16 -i on
export LD_LIBRARY_PATH='${BUILD}/src/datasystem/worker:${BUILD}/tests/st:${BUILD}/tests/ut'
cd '${BUILD}/tests/st'
./ds_device_llt --gtest_filter='NdsClusterSpillRwTest.*'
"
