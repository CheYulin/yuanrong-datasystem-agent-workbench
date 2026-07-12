#!/usr/bin/env bash
# Run on L2 NPU / NDS node. Prints PASS/FAIL lines for human paste-back.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "${ROOT}/env.local.sh" ]] && source "${ROOT}/env.local.sh"

echo "=== NDS/HBM device env check ==="
echo "host=$(hostname) date=$(date -Iseconds)"

fail=0
check() {
  local name="$1"
  shift
  if "$@"; then
    echo "PASS: ${name}"
  else
    echo "FAIL: ${name}"
    fail=1
  fi
}

check "davinci node" bash -c 'compgen -G "/dev/davinci*" >/dev/null'
if command -v npu-smi >/dev/null 2>&1; then
  check "npu-smi" npu-smi info
else
  echo "WARN: npu-smi not in PATH"
fi

if [[ -n "${ASCEND_HOME:-}" ]]; then
  check "ASCEND_HOME lib64" test -d "${ASCEND_HOME}/lib64"
else
  echo "WARN: ASCEND_HOME unset"
fi

if [[ -e "${P2P_DEVICE:-/dev/p2p_device}" ]]; then
  check "p2p_device" test -e "${P2P_DEVICE}"
else
  echo "WARN: ${P2P_DEVICE:-/dev/p2p_device} missing (Stage B only)"
fi

if [[ -n "${BDEV_NAME:-}" ]]; then
  check "bdev ${BDEV_NAME}" test -b "${BDEV_NAME}" || check "bdev ${BDEV_NAME} (char)" test -e "${BDEV_NAME}"
fi

if [[ -n "${XDS_SO_PATH:-}" ]]; then
  check "xds so" test -f "${XDS_SO_PATH}"
fi

echo "=== done fail=${fail} ==="
exit "${fail}"
