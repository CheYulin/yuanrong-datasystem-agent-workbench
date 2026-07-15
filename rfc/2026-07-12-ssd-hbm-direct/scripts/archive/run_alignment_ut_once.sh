#!/usr/bin/env bash
set -euo pipefail
REMOTE=xqyun-32c32g
REPO=/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct
BUILD=/root/workspace/build-ssd-hbm-direct
LOCAL=/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct
IGNORE=/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/development/sync/sync_to_xqyun.rsyncignore
LOG=/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/nds_ut_run.log
ssh -o BatchMode=yes "$REMOTE" "mkdir -p '$REPO'"
rsync -az --exclude-from="$IGNORE" "$LOCAL/" "${REMOTE}:${REPO}/"
ssh -o BatchMode=yes "$REMOTE" bash -s <<'REMOTEEOF' 2>&1 | tee "$LOG"
set -euo pipefail
export DS_OPENSOURCE_DIR=/root/.cache/yuanrong-datasystem-third-party
REPO=/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct
BUILD=/root/workspace/build-ssd-hbm-direct
cd "$REPO"
bash build.sh -t build -X off -P off -B "$BUILD" -j 16 -i on 2>&1 | tail -n 60
test -x "$BUILD/tests/ut/ds_ut_nds"
cd "$BUILD/tests/ut"
./ds_ut_nds --gtest_filter='AlignmentGateTest.*'
REMOTEEOF
