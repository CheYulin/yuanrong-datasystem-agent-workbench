#!/usr/bin/env bash
# Integration test (ST) on remote node via SSH (< 60 minutes).
#
# Usage:
#   bash scripts/testing/verify/st/run_st_remote.sh [--node <name>] [--skip-build]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${SCRIPT_DIR}/../../../development/lib" && pwd)"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/remote_defaults.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/timing.sh"

SKIP_BUILD=0
NODE="${NODE_NAME:-$(node_role_default verify_st)}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --node) NODE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

init_remote "${NODE}"
BUILD_BACKEND="${BUILD_BACKEND:-cmake}"

banner "ST on ${REMOTE}"

run_payload() {
  bash -s -- "${SKIP_BUILD}" "${BUILD_BACKEND}" "${REMOTE_BASE}" <<'REMOTE_SCRIPT'
  set -euo pipefail
  SKIP_BUILD="$1"
  BUILD_BACKEND="$2"
  REMOTE_BASE="$3"
  cd "${REMOTE_BASE}/yuanrong-datasystem"

  if [[ "${SKIP_BUILD}" -eq 0 ]]; then
    echo 'Building...'
    bash build.sh -t build -B build -b "${BUILD_BACKEND}" -j "$(nproc)" 2>&1 | tail -5
  fi

  echo 'Running ST tests...'
  ctest --test-dir build --output-on-failure -R 'st|ST|integration' -j "$(nproc)" 2>&1 | tail -30
REMOTE_SCRIPT
}

if [[ -d "${REMOTE_BASE}/yuanrong-datasystem" ]]; then
  log_info "Running locally on $(hostname -s) with REMOTE_BASE=${REMOTE_BASE}"
  run_payload
else
  ssh_remote "${REMOTE}" bash -s -- "${SKIP_BUILD}" "${BUILD_BACKEND}" "${REMOTE_BASE}" <<'REMOTE_SCRIPT'
  set -euo pipefail
  SKIP_BUILD="$1"
  BUILD_BACKEND="$2"
  REMOTE_BASE="$3"
  cd "${REMOTE_BASE}/yuanrong-datasystem"

  if [[ "${SKIP_BUILD}" -eq 0 ]]; then
    echo 'Building...'
    bash build.sh -t build -B build -b "${BUILD_BACKEND}" -j "$(nproc)" 2>&1 | tail -5
  fi

  echo 'Running ST tests...'
  ctest --test-dir build --output-on-failure -R 'st|ST|integration' -j "$(nproc)" 2>&1 | tail -30
REMOTE_SCRIPT
fi
