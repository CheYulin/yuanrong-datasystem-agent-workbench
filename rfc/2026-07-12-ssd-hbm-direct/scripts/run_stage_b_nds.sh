#!/usr/bin/env bash
# HUMAN: run on L2 after Stage A PASS. Requires xds + BDEV_NAME.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "${ROOT}/env.local.sh" ]] && source "${ROOT}/env.local.sh"

bash "${ROOT}/check_env_device.sh" || {
  echo "ERROR: device env check failed"
  exit 1
}

if [[ ! -e "${P2P_DEVICE:-/dev/p2p_device}" ]]; then
  echo "ERROR: ${P2P_DEVICE:-/dev/p2p_device} required for Stage B"
  exit 2
fi
if [[ -z "${BDEV_NAME:-}" ]]; then
  echo "ERROR: set BDEV_NAME in env.local.sh"
  exit 2
fi

BUILD_DIR="${L2_BUILD_DIR:-${L1_BUILD_DIR:-}}"
BIN="${BUILD_DIR}/tests/st/ds_device_llt"
FILTER="${STAGE_B_FILTER:-NdsStageB*}"
LOG="stage_b_$(hostname)_$(date +%Y%m%d_%H%M%S).log"

export FLAGS_nds_bdev_name="${BDEV_NAME}"
# Datasystem gflags name may differ; keep env for adapter to read:
export DS_NDS_BDEV_NAME="${BDEV_NAME}"

if [[ ! -x "${BIN}" ]]; then
  echo "ERROR: missing ${BIN}"
  exit 2
fi

echo "RUN Stage B: ${BIN} --gtest_filter=${FILTER} bdev=${BDEV_NAME}"
set +e
"${BIN}" --gtest_filter="${FILTER}" 2>&1 | tee "${LOG}"
rc=${PIPESTATUS[0]}
set -e
echo "LOG=$(pwd)/${LOG} rc=${rc}"
echo "Paste log to Agent. Expect: file pattern == HBM; misaligned → fallback."
exit "${rc}"
