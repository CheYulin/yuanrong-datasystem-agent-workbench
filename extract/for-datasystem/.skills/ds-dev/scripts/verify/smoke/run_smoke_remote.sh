#!/usr/bin/env bash
# Smoke test on remote node via SSH (< 5 minutes).
#
# Usage:
#   bash scripts/testing/verify/smoke/run_smoke_remote.sh [--node <name>] [--skip-build]

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
SKIP_ZMQ_GATE=0
NODE="${NODE_NAME:-$(node_role_default verify_smoke)}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-zmq-gate) SKIP_ZMQ_GATE=1; shift ;;
    --node) NODE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

init_remote "${NODE}"

BUILD_BACKEND="${BUILD_BACKEND:-cmake}"
: "${BUILD_DIR:=build}"

banner "Smoke test on ${REMOTE}"

run_payload() {
  bash -s -- "${SKIP_BUILD}" "${BUILD_BACKEND}" "${REMOTE_BASE}" "${BUILD_DIR}" "${SKIP_ZMQ_GATE}" <<'REMOTE_SCRIPT'
  set -euo pipefail
  SKIP_BUILD="$1"
  BUILD_BACKEND="$2"
  REMOTE_BASE="$3"
  BUILD_DIR="$4"
  SKIP_ZMQ_GATE="$5"
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
    echo 'No ctest smoke targets; falling back to Python cluster smoke...'
    WB="${REMOTE_BASE}/yuanrong-datasystem-agent-workbench"
    ZMQ_ARGS=()
    if [[ "${SKIP_ZMQ_GATE}" -eq 1 ]]; then
      ZMQ_ARGS+=(--skip-zmq-gate)
    fi
    python3 "${WB}/scripts/testing/verify/smoke/run_smoke.py" \
      --read-loop-sec "${SMOKE_READ_LOOP_SEC:-15}" \
      --keys "${SMOKE_KEYS:-80}" \
      --tenants "${SMOKE_TENANTS:-2}" \
      --clients-per-tenant "${SMOKE_CLIENTS_PER_TENANT:-2}" \
      --min-zmq-metric-count "${SMOKE_MIN_ZMQ_METRIC_COUNT:-5}" \
      "${ZMQ_ARGS[@]}"
    exit $?
  fi
  exit "${CTEST_STATUS}"
REMOTE_SCRIPT
}

if [[ -d "${REMOTE_BASE}/yuanrong-datasystem" ]]; then
  log_info "Running locally on $(hostname -s) with REMOTE_BASE=${REMOTE_BASE}"
  run_payload
else
  ssh_remote "${REMOTE}" bash -s -- "${SKIP_BUILD}" "${BUILD_BACKEND}" "${REMOTE_BASE}" "${BUILD_DIR}" "${SKIP_ZMQ_GATE}" <<'REMOTE_SCRIPT'
  set -euo pipefail
  SKIP_BUILD="$1"
  BUILD_BACKEND="$2"
  REMOTE_BASE="$3"
  BUILD_DIR="$4"
  SKIP_ZMQ_GATE="$5"
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
    echo 'No ctest smoke targets; falling back to Python cluster smoke...'
    WB="${REMOTE_BASE}/yuanrong-datasystem-agent-workbench"
    ZMQ_ARGS=()
    if [[ "${SKIP_ZMQ_GATE}" -eq 1 ]]; then
      ZMQ_ARGS+=(--skip-zmq-gate)
    fi
    python3 "${WB}/scripts/testing/verify/smoke/run_smoke.py" \
      --read-loop-sec "${SMOKE_READ_LOOP_SEC:-15}" \
      --keys "${SMOKE_KEYS:-80}" \
      --tenants "${SMOKE_TENANTS:-2}" \
      --clients-per-tenant "${SMOKE_CLIENTS_PER_TENANT:-2}" \
      --min-zmq-metric-count "${SMOKE_MIN_ZMQ_METRIC_COUNT:-5}" \
      "${ZMQ_ARGS[@]}"
    exit $?
  fi
  exit "${CTEST_STATUS}"
REMOTE_SCRIPT
fi
