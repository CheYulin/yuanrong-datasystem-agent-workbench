#!/usr/bin/env bash
# Sync feat/ssd-hbm-direct worktree -> xqyun isolated tree (does not touch main yuanrong-datasystem).
set -euo pipefail
REMOTE="${REMOTE:-xqyun-32c32g}"
RDIR="${RDIR:-/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct}"
LOCAL="${LOCAL:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
IGNORE="${IGNORE:-/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/development/sync/sync_to_xqyun.rsyncignore}"

ssh -o BatchMode=yes "$REMOTE" "mkdir -p '$RDIR' /root/workspace/build-ssd-hbm-direct"
rsync -az --human-readable --info=stats2 --exclude-from="$IGNORE" "$LOCAL/" "${REMOTE}:${RDIR}/"
echo "SYNC_OK -> ${REMOTE}:${RDIR}"
