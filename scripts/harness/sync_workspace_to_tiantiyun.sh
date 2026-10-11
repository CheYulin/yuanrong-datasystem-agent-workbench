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
  --exclude='yuanrong-datasystem/.worktrees'
  --exclude='.git'
  --exclude='**/results/'
  --exclude='*.whl'
  # Umbrella clutter / cold siblings (see git-repos/AGENTS.md)
  --exclude='p[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*'
  --exclude='*.lock'
  --exclude='.codex-worktrees'
  --exclude='.agents'
  --exclude='.claude'
  --exclude='.codex'
  --exclude='.cursor'
  --exclude='.idea'
  --exclude='archive'
  --exclude='kernel'
  --exclude='code-review'
  --exclude='htmls'
  --exclude='outputs'
  --exclude='plans'
  --exclude='docs'
  --exclude='results'
  --exclude='pr-review-notes'
  --exclude='pr1405-one-shot-evidence'
  --exclude='slow-log-echarts-analysis'
  --exclude='perf-log-analysis-*'
  --exclude='yche-publish'
  --exclude='cann-learning-hub'
  --exclude='hixl'
  --exclude='memfabric_hybrid'
  --exclude='todo'
  --exclude='datasystem'
  --exclude='datasystem-pr*'
  --exclude='yuanrong-datasystem-main-master'
  --exclude='yuanrong-datasystem-ds-trace-triage'
  --exclude='yuanrong-datasystem-pr1129-*'
  --exclude='*.html'
  --exclude='*.html.txt'
  --exclude='*.diff'
  --exclude='worker.config'
  --exclude='worker.config.diff'
  --exclude='.verify_bg.log'
  --exclude='分布式*'
)
(( DRY_RUN )) && RSYNC_OPTS+=(--dry-run)

DEST="${REMOTE}:${REMOTE_BASE}/"
log_info "Sync ${REPOS_ROOT}/ → ${DEST}"
ssh_remote "${REMOTE}" "mkdir -p '${REMOTE_BASE}'"
rsync "${RSYNC_OPTS[@]}" "${REPOS_ROOT}/" "${DEST}"
log_info "Sync done (${NODE})."
