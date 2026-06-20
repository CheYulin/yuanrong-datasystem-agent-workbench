#!/usr/bin/env bash
# Run workbench + datasystem skill verification on tiantiyun-80c128g.
# Local entry: rsync workspace, then SSH to run TDD + L1–L8 user-path ladder.
#
# Usage (from agent-workbench root, or WSL):
#   bash scripts/harness/run_skill_verification_remote.sh
#   bash scripts/harness/run_skill_verification_remote.sh --skip-sync
#   bash scripts/harness/run_skill_verification_remote.sh --tests-only
#   bash scripts/harness/run_skill_verification_remote.sh --user-only
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_ROOT="$(cd "${HARNESS_DIR}/../.." && pwd)"
LIB_DIR="${WORKBENCH_ROOT}/scripts/development/lib"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/remote_defaults.sh"

NODE="$(node_role_default verify_smoke)"
init_remote "${NODE}"

SKIP_SYNC=0
TESTS_ONLY=0
USER_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-sync) SKIP_SYNC=1; shift ;;
    --tests-only) TESTS_ONLY=1; shift ;;
    --user-only) USER_ONLY=1; shift ;;
    -h|--help)
      sed -n '1,14p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

REMOTE_WB="${REMOTE_BASE}/yuanrong-datasystem-agent-workbench"
REMOTE_ENV="VERIFY_ON_NODE='${NODE}'"

if [[ "${SKIP_SYNC}" -eq 0 ]]; then
  bash "${HARNESS_DIR}/sync_workspace_to_tiantiyun.sh"
fi

REMOTE_PARTS=()
if [[ "${USER_ONLY}" -eq 0 ]]; then
  REMOTE_PARTS+=("${REMOTE_ENV} bash '${REMOTE_WB}/scripts/run_skill_tests.sh'")
fi
if [[ "${TESTS_ONLY}" -eq 0 ]]; then
  REMOTE_PARTS+=("${REMOTE_ENV} bash '${REMOTE_WB}/scripts/run_skill_user_verification.sh'")
fi

REMOTE_CMD="$(IFS=' && '; echo "${REMOTE_PARTS[*]}")"
log_info "Running on ${NODE} (${REMOTE}): ${REMOTE_CMD}"
ssh_remote "${REMOTE}" "${REMOTE_CMD}"

log_info "Skill verification finished on ${NODE}. Evidence: ${REMOTE_WB}/results/skill_verification_*"
