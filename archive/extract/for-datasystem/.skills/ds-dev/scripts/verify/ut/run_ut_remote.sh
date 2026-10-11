#!/usr/bin/env bash
# Unit test regression on remote node via SSH (< 30 minutes).
#
# Usage:
#   bash scripts/testing/verify/ut/run_ut_remote.sh [--node <name>] [--skip-build]

set -euo pipefail

_ds_find_repo_root() {
  local d
  d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  while [[ "${d}" != "/" ]]; do
    if [[ -f "${d}/build.sh" && -f "${d}/CMakeLists.txt" ]]; then
      echo "${d}"
      return 0
    fi
    d="$(dirname "${d}")"
  done
  return 1
}
DS_REPO_ROOT="${DS_REPO_ROOT:-$(_ds_find_repo_root)}"
LIB_DIR="${DS_HARNESS_LIB:-${DS_REPO_ROOT}/.skills/ds-harness/scripts/lib}"
# shellcheck source=ds_repo_root.sh
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/remote_defaults.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/timing.sh"

SKIP_BUILD=0
NODE="${NODE_NAME:-$(node_role_default verify_ut)}"
# GTest names use *Test.*; exclude ST/integration suffixes (not bare "st" — that matches "Test").
UT_CTEST_REGEX="${UT_CTEST_REGEX:-Test\.|DeathTest}"
UT_CTEST_EXCLUDE="${UT_CTEST_EXCLUDE:-_st$|_ST$|IntegrationTest|integration_test|smoke|e2e}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --node) NODE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

init_remote "${NODE}"
BUILD_BACKEND="${BUILD_BACKEND:-cmake}"
: "${BUILD_DIR:=build}"

banner "UT regression on ${REMOTE}"

run_payload() {
  bash -s -- "${SKIP_BUILD}" "${BUILD_BACKEND}" "${REMOTE_BASE}" "${BUILD_DIR}" "${UT_CTEST_REGEX}" "${UT_CTEST_EXCLUDE}" <<'REMOTE_SCRIPT'
  set -euo pipefail
  SKIP_BUILD="$1"
  BUILD_BACKEND="$2"
  REMOTE_BASE="$3"
  BUILD_DIR="$4"
  UT_CTEST_REGEX="$5"
  UT_CTEST_EXCLUDE="$6"
  cd "${REMOTE_BASE}/yuanrong-datasystem"

  if [[ "${SKIP_BUILD}" -eq 0 ]]; then
    echo 'Building...'
    bash build.sh -t build -B "${BUILD_DIR}" -b "${BUILD_BACKEND}" -j "$(nproc)" 2>&1 | tail -120
  fi

  echo "Running UT tests (regex=${UT_CTEST_REGEX}, exclude=${UT_CTEST_EXCLUDE})..."
  set +e
  CTEST_OUTPUT="$(ctest --test-dir "${BUILD_DIR}" --output-on-failure -R "${UT_CTEST_REGEX}" -E "${UT_CTEST_EXCLUDE}" -j "$(nproc)" 2>&1)"
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
  ssh_remote "${REMOTE}" bash -s -- "${SKIP_BUILD}" "${BUILD_BACKEND}" "${REMOTE_BASE}" "${BUILD_DIR}" "${UT_CTEST_REGEX}" "${UT_CTEST_EXCLUDE}" <<'REMOTE_SCRIPT'
  set -euo pipefail
  SKIP_BUILD="$1"
  BUILD_BACKEND="$2"
  REMOTE_BASE="$3"
  BUILD_DIR="$4"
  UT_CTEST_REGEX="$5"
  UT_CTEST_EXCLUDE="$6"
  cd "${REMOTE_BASE}/yuanrong-datasystem"

  if [[ "${SKIP_BUILD}" -eq 0 ]]; then
    echo 'Building...'
    bash build.sh -t build -B "${BUILD_DIR}" -b "${BUILD_BACKEND}" -j "$(nproc)" 2>&1 | tail -120
  fi

  echo 'Running UT tests...'
  set +e
  CTEST_OUTPUT="$(ctest --test-dir "${BUILD_DIR}" --output-on-failure -R "${UT_CTEST_REGEX}" -E "${UT_CTEST_EXCLUDE}" -j "$(nproc)" 2>&1)"
  CTEST_STATUS="$?"
  set -e
  printf '%s\n' "${CTEST_OUTPUT}" | tail -30
  if printf '%s\n' "${CTEST_OUTPUT}" | grep -q 'No tests were found'; then
    exit 8
  fi
  exit "${CTEST_STATUS}"
REMOTE_SCRIPT
fi
