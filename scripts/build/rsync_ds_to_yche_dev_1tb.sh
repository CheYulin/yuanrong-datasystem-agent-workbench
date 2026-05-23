#!/usr/bin/env bash
set -euo pipefail

REMOTE="yche@1.95.199.126"
REMOTE_PORT="22224"
REMOTE_DS_DIR="/home/yche/workspace/git-repos/yuanrong-datasystem"
REMOTE_VIBE_DIR="/home/yche/workspace/git-repos/yuanrong-datasystem-agent-workbench"
LOCAL_DS_DIR="/root/agent/hermes-workspace/yuanrong-datasystem"
LOCAL_VIBE_DIR="/root/agent/hermes-workspace/yuanrong-datasystem-agent-workbench"
RSYNC_IGNORE_FILE="${LOCAL_VIBE_DIR}/scripts/build/remote_build_run_datasystem.rsyncignore"

echo "== rsync to yche-dev-1tb =="
echo "LOCAL_DS -> ${REMOTE}:${REMOTE_DS_DIR}"
rsync -az --delete \
  --exclude-from="${RSYNC_IGNORE_FILE}" \
  --rsh="ssh -p ${REMOTE_PORT}" \
  "${LOCAL_DS_DIR}/" "${REMOTE}:${REMOTE_DS_DIR}/"

echo "LOCAL_VIBE -> ${REMOTE}:${REMOTE_VIBE_DIR}"
rsync -az --delete \
  --exclude-from="${RSYNC_IGNORE_FILE}" \
  --rsh="ssh -p ${REMOTE_PORT}" \
  "${LOCAL_VIBE_DIR}/" "${REMOTE}:${REMOTE_VIBE_DIR}/"

echo "== done =="
