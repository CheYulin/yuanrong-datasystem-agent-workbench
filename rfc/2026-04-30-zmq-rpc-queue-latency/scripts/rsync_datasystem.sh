#!/usr/bin/env bash
# Rsync local yuanrong-datasystem → remote REMOTE_DS (see repl_remote_common.inc.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=repl_remote_common.inc.sh
source "${SCRIPT_DIR}/repl_remote_common.inc.sh"

if [[ ! -d "${LOCAL_DS}/src" ]]; then
  echo "Local DS not found: ${LOCAL_DS} (set YUANRONG_DATASYSTEM_ROOT)" >&2
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

echo "Done."
