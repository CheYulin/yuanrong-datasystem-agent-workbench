#!/usr/bin/env bash
# Sync local git-repos workspace (workbench + datasystem) → tiantiyun.
# Used before skill TDD / user-path verification on the verify node.
#
# Usage:
#   bash scripts/harness/sync_workspace_to_tiantiyun.sh
#   bash scripts/harness/sync_workspace_to_tiantiyun.sh --dry-run
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_ROOT="$(cd "${HARNESS_DIR}/../.." && pwd)"
REPOS_ROOT="$(cd "${WORKBENCH_ROOT}/.." && pwd)"
LIB_DIR="${WORKBENCH_ROOT}/scripts/lib"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/remote_defaults.sh"

NODE="$(node_role_default build)"
init_remote "${NODE}"

DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '1,12p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

RSYNC_OPTS=(-avz --delete
  --exclude='yuanrong-datasystem/build'
  --exclude='yuanrong-datasystem/example/cpp/build'
  --exclude='yuanrong-datasystem/config.cmake'
  --exclude='.git'
  --exclude='**/results/'
  --exclude='*.whl'
)
(( DRY_RUN )) && RSYNC_OPTS+=(--dry-run)

DEST="${REMOTE}:${REMOTE_BASE}/"
log_info "Sync ${REPOS_ROOT}/ → ${DEST}"
ssh_remote "${REMOTE}" "mkdir -p '${REMOTE_BASE}'"
rsync "${RSYNC_OPTS[@]}" "${REPOS_ROOT}/" "${DEST}"
log_info "Sync done (${NODE})."
