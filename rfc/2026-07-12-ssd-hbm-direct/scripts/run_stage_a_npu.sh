#!/usr/bin/env bash
# HUMAN: run on L2 NPU node after Stage A tests exist.
# Proves CANN IPC Export/Import + pattern (no xds).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "${ROOT}/env.local.sh" ]] && source "${ROOT}/env.local.sh"

bash "${ROOT}/check_env_device.sh" || {
  echo "ERROR: device env check failed; fix before Stage A"
  exit 1
}

BUILD_DIR="${L2_BUILD_DIR:-${L1_BUILD_DIR:-}}"
BIN="${BUILD_DIR}/tests/st/ds_device_llt"
FILTER="${STAGE_A_FILTER:-NdsStageA*}"
LOG="stage_a_$(hostname)_$(date +%Y%m%d_%H%M%S).log"

if [[ ! -x "${BIN}" ]]; then
  echo "ERROR: missing ${BIN}"
  echo "Build WITH NPU (BUILD_HETERO_NPU=on) on this node, then re-run."
  exit 2
fi

echo "RUN Stage A: ${BIN} --gtest_filter=${FILTER}"
set +e
"${BIN}" --gtest_filter="${FILTER}" 2>&1 | tee "${LOG}"
rc=${PIPESTATUS[0]}
set -e
echo "LOG=$(pwd)/${LOG} rc=${rc}"
echo "Paste this log back to Agent. Expect: bidirectional pattern match, no xds."
exit "${rc}"
