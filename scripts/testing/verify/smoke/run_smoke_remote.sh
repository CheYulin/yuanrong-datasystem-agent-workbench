#!/usr/bin/env bash
# Smoke test on remote node via SSH (< 5 minutes).
#
# Usage:
#   bash scripts/testing/verify/smoke/run_smoke_remote.sh [--node <name>] [--skip-build]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${SCRIPT_DIR}/../../../lib" && pwd)"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/remote_defaults.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/timing.sh"

SKIP_BUILD=0
NODE="${NODE_NAME:-$(node_role_default verify_smoke)}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --node) NODE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

init_remote "${NODE}"

BUILD_BACKEND="${BUILD_BACKEND:-cmake}"
BUILD_DIR="${BUILD_DIR:-build}"

banner "Smoke test on ${REMOTE}"

run_payload() {
  bash -s -- "${SKIP_BUILD}" "${BUILD_BACKEND}" "${REMOTE_BASE}" "${BUILD_DIR}" <<'REMOTE_SCRIPT'
  set -euo pipefail
  SKIP_BUILD="$1"
  BUILD_BACKEND="$2"
  REMOTE_BASE="$3"
  BUILD_DIR="$4"
  cd "${REMOTE_BASE}/yuanrong-datasystem"

  if [[ "${SKIP_BUILD}" -eq 0 ]]; then
    echo 'Building...'
    bash build.sh -t build -B "${BUILD_DIR}" -b "${BUILD_BACKEND}" -j "$(nproc)" 2>&1 | tail -120
  fi

  echo 'Running smoke tests...'
  set +e
  CTEST_OUTPUT="$(ctest --test-dir "${BUILD_DIR}" --output-on-failure -R smoke -j "$(nproc)" 2>&1)"
  CTEST_STATUS="$?"
  set -e
  printf '%s\n' "${CTEST_OUTPUT}" | tail -30
  if printf '%s\n' "${CTEST_OUTPUT}" | grep -q 'No tests were found'; then
    exit 8
  fi
  exit "${CTEST_STATUS}"
REMOTE_SCRIPT
}

if [[ -d "${REMOTE_BASE}/yuanrong-datasystem" ]]; then
  log_info "Running locally on $(hostname -s) with REMOTE_BASE=${REMOTE_BASE}"
  run_payload
else
  ssh_remote "${REMOTE}" bash -s -- "${SKIP_BUILD}" "${BUILD_BACKEND}" "${REMOTE_BASE}" "${BUILD_DIR}" <<'REMOTE_SCRIPT'
  set -euo pipefail
  SKIP_BUILD="$1"
  BUILD_BACKEND="$2"
  REMOTE_BASE="$3"
  BUILD_DIR="$4"
  cd "${REMOTE_BASE}/yuanrong-datasystem"

  if [[ "${SKIP_BUILD}" -eq 0 ]]; then
    echo 'Building...'
    bash build.sh -t build -B "${BUILD_DIR}" -b "${BUILD_BACKEND}" -j "$(nproc)" 2>&1 | tail -120
  fi

  echo 'Running smoke tests...'
  set +e
  CTEST_OUTPUT="$(ctest --test-dir "${BUILD_DIR}" --output-on-failure -R smoke -j "$(nproc)" 2>&1)"
  CTEST_STATUS="$?"
  set -e
  printf '%s\n' "${CTEST_OUTPUT}" | tail -30
  if printf '%s\n' "${CTEST_OUTPUT}" | grep -q 'No tests were found'; then
    exit 8
  fi
  exit "${CTEST_STATUS}"
REMOTE_SCRIPT
fi
