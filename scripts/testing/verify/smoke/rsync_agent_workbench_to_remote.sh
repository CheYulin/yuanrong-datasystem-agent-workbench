#!/usr/bin/env bash
# Rsync local yuanrong-datasystem-agent-workbench → remote clone.
# Excludes logs, smoke/artifacts results trees, common build/binary patterns — see generated exclude list.
#
# Usage:
#   bash scripts/testing/verify/smoke/rsync_agent_workbench_to_remote.sh
#   REMOTE=user@host bash .../rsync_agent_workbench_to_remote.sh
#   bash .../rsync_agent_workbench_to_remote.sh --dry-run
#   bash .../rsync_agent_workbench_to_remote.sh --delete   # strict mirror (deletes extras on remote)
#
# Base ignore rules: scripts/development/sync/sync_to_xqyun.rsyncignore
# Extra: nested **/results/, *.whl (wheels built on remote, not from workbench tree).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
BASE_IGNORE="${WORKBENCH_ROOT}/scripts/development/sync/sync_to_xqyun.rsyncignore"

REMOTE="${REMOTE:-xqyun-32c32g}"
REMOTE_BASE="${REMOTE_BASE:-~/workspace/git-repos}"
REMOTE_WB_NAME="yuanrong-datasystem-agent-workbench"

DRY_RUN=0
DO_DELETE=0

usage() {
  sed -n '1,22p' "$0" | tail -n +2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run) DRY_RUN=1 ;;
    --delete) DO_DELETE=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ ! -f "${BASE_IGNORE}" ]]; then
  echo "Missing base rsyncignore: ${BASE_IGNORE}" >&2
  exit 1
fi

EXCLUDES_TMP="$(mktemp)"
trap 'rm -f "${EXCLUDES_TMP}"' EXIT

{
  cat "${BASE_IGNORE}"
  printf '\n# --- rsync_agent_workbench_to_remote extras ---\n'
  printf '%s\n' '**/results/' '*.whl'
} >"${EXCLUDES_TMP}"

RSYNC_OPTS=(
  -az
  --no-owner
  --no-group
  --human-readable
  --exclude-from="${EXCLUDES_TMP}"
)
(( DRY_RUN )) && RSYNC_OPTS+=(--dry-run)
(( DO_DELETE )) && RSYNC_OPTS+=(--delete)

REMOTE_DEST="${REMOTE_BASE%/}/${REMOTE_WB_NAME}"
echo "[rsync_agent_workbench] LOCAL   = ${WORKBENCH_ROOT}/"
echo "[rsync_agent_workbench] REMOTE  = ${REMOTE}:${REMOTE_DEST}/"
echo "[rsync_agent_workbench] DELETE  = ${DO_DELETE}  DRY_RUN=${DRY_RUN}"
echo "[rsync_agent_workbench] EXCLUDE = ${BASE_IGNORE} + extras (**/results/, *.whl)"

if (( DRY_RUN == 0 )); then
  ssh -o BatchMode=yes "${REMOTE}" "mkdir -p \"${REMOTE_DEST}\""
fi

rsync "${RSYNC_OPTS[@]}" "${WORKBENCH_ROOT}/" "${REMOTE}:${REMOTE_DEST}/"

echo "[rsync_agent_workbench] done."
echo "Next: see REMOTE_SMOKE.md (build whl on remote → pip install → run run_smoke_metrics_30s.sh)."
