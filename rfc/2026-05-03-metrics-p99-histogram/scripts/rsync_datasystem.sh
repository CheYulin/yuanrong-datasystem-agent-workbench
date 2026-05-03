#!/usr/bin/env bash
# Rsync local yuanrong-datasystem to remote (third-party cache preserved on remote).
#
# Usage (from this RFC dir or any cwd):
#   bash scripts/rsync_datasystem.sh
#   REMOTE=user@host bash scripts/rsync_datasystem.sh
#
# Defaults align with bazel_build.sh / bazel_run_tests.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RFC_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKBENCH_ROOT="$(cd "${RFC_ROOT}/../.." && pwd)"
RSYNCIGNORE_DIR="$(cd "${WORKBENCH_ROOT}/scripts/build" && pwd)"
RSYNCIGNORE_FILE="${RSYNCIGNORE_DIR}/remote_build_run_datasystem.rsyncignore"

REMOTE="${REMOTE:-root@xqyun-32c32g}"
LOCAL_DS="${LOCAL_DS:-/home/t14s/workspace/git-repos/yuanrong-datasystem}"
REMOTE_DS="${REMOTE_DS:-/root/workspace/git-repos/yuanrong-datasystem}"

if [[ ! -d "${LOCAL_DS}/src" ]]; then
  echo "Local DS not found: ${LOCAL_DS}" >&2
  exit 1
fi
if [[ ! -f "${RSYNCIGNORE_FILE}" ]]; then
  echo "rsyncignore not found: ${RSYNCIGNORE_FILE}" >&2
  exit 1
fi

echo "LOCAL_DS=${LOCAL_DS}"
echo "REMOTE=${REMOTE}"
echo "REMOTE_DS=${REMOTE_DS}"
echo

ssh "${REMOTE}" "mkdir -p \"${REMOTE_DS}\""
rsync -az --delete \
  --exclude-from="${RSYNCIGNORE_FILE}" \
  "${LOCAL_DS}/" \
  "${REMOTE}:${REMOTE_DS}/"

echo "rsync done."
