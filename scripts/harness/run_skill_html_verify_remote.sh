#!/usr/bin/env bash
# Run wb-html-publish verification on xqyun-32c32g (yche.me git host).
#
# Usage:
#   bash scripts/harness/run_skill_html_verify_remote.sh
#   bash scripts/harness/run_skill_html_verify_remote.sh --skip-sync
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_ROOT="$(cd "${HARNESS_DIR}/../.." && pwd)"
LIB_DIR="${WORKBENCH_ROOT}/scripts/development/lib"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/remote_defaults.sh"

NODE="$(node_role_default publish_web)"
init_remote "${NODE}"

SKIP_SYNC=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-sync) SKIP_SYNC=1; shift ;;
    -h|--help)
      sed -n '1,10p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

REMOTE_WB="${REMOTE_BASE}/yuanrong-datasystem-agent-workbench"
REMOTE_ENV="VERIFY_ON_NODE='${NODE}'"

if [[ "${SKIP_SYNC}" -eq 0 ]]; then
  bash "${WORKBENCH_ROOT}/scripts/development/sync/sync_to_xqyun.sh"
fi

REMOTE_CMD="${REMOTE_ENV} bash '${REMOTE_WB}/scripts/run_skill_html_verification.sh'"
log_info "HTML skill verification on ${NODE} (${REMOTE}): ${REMOTE_CMD}"
ssh_remote "${REMOTE}" "${REMOTE_CMD}"

log_info "Done. Evidence: ${REMOTE_WB}/results/skill_html_verification_*"
