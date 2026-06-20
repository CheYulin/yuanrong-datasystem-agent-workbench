#!/usr/bin/env bash
# Remote skill verification on tiantiyun (+ TDD on verify node).
#
# Usage:
#   bash scripts/harness/run_skill_verification_remote.sh
#   bash scripts/harness/run_skill_verification_remote.sh --skip-sync
#   bash scripts/harness/run_skill_verification_remote.sh --tests-only
#   bash scripts/harness/run_skill_verification_remote.sh --user-only
#   bash scripts/harness/run_skill_verification_remote.sh --dry-run
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_ROOT="$(cd "${HARNESS_DIR}/../.." && pwd)"
LIB_DIR="${WORKBENCH_ROOT}/scripts/lib"
SCRIPT_DIR="${LIB_DIR}"
# shellcheck source=../lib/load_nodes.sh
. "${LIB_DIR}/load_nodes.sh"
# shellcheck source=../lib/common.sh
. "${LIB_DIR}/common.sh"
# shellcheck source=../lib/remote_defaults.sh
. "${LIB_DIR}/remote_defaults.sh"

NODE="$(node_role_default verify_smoke)"
init_remote "${NODE}"

SKIP_SYNC=0
TESTS_ONLY=0
USER_ONLY=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-sync) SKIP_SYNC=1; shift ;;
    --tests-only) TESTS_ONLY=1; shift ;;
    --user-only) USER_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '1,14p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

REMOTE_WB="${REMOTE_BASE}/yuanrong-datasystem-agent-workbench"
VERIFY_OPTS=()
(( SKIP_SYNC )) || VERIFY_OPTS+=(--sync)
(( DRY_RUN )) && VERIFY_OPTS+=(--dry-run)

if [[ "${USER_ONLY}" -eq 0 ]]; then
  log_info "TDD on ${NODE}"
  ssh_remote "${REMOTE}" "cd '${REMOTE_WB}' && bash scripts/run_skill_tests.sh"
fi

if [[ "${TESTS_ONLY}" -eq 0 ]]; then
  log_info "Skill verify (tiantiyun skills via verify_skill.sh)"
  for skill in wb-build wb-dev wb-daily wb-perf wb-docs; do
    bash "${HARNESS_DIR}/verify_skill.sh" --skill "${skill}" "${VERIFY_OPTS[@]}"
  done
fi

log_info "Done."
