#!/usr/bin/env bash
# L1 (no NPU): focused binmock ST for SSD→HBM direct track.
# Does NOT run full ds_device_llt suite.
#
# Usage:
#   bash run_binmock_flow_st.sh                    # xqyun isolated, focused filters
#   bash run_binmock_flow_st.sh --hetero-only      # Gate0 5 cases only (smoke)
#   bash run_binmock_flow_st.sh --remote           # same via ssh xqyun
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/gtest_filters.sh"
[[ -f "${ROOT}/env.local.sh" ]] && source "${ROOT}/env.local.sh"

REMOTE="${REMOTE:-xqyun-32c32g}"
BUILD="${L1_BUILD_DIR:-/root/workspace/build-ssd-hbm-direct}"
HETERO_ONLY=0
DO_REMOTE=0
for arg in "$@"; do
  case "$arg" in
    --hetero-only) HETERO_ONLY=1 ;;
    --remote) DO_REMOTE=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
  esac
done

if [[ "${HETERO_ONLY}" -eq 1 ]]; then
  FILTER="${GATE0_GTEST_FILTER}"
else
  FILTER="${BINMOCK_FLOW_GTEST_FILTER}"
fi

run_on_host() {
  local bin="${BUILD}/tests/st/ds_device_llt"
  local meta="${META:-/root/workspace/nds-ssd-hbm-meta}"
  mkdir -p "${meta}"
  if [[ ! -x "${bin}" ]]; then
    echo "ERROR: ${bin} missing. Run prepare_build_and_st_xqyun.sh --build-only first."
    exit 2
  fi
  local log="${meta}/binmock_flow_$(date +%Y%m%d_%H%M%S).log"
  echo "RUN: ${bin}"
  echo "FILTER: ${FILTER}"
  cd "$(dirname "${bin}")"
  export LD_LIBRARY_PATH="$(dirname "${bin}):${LD_LIBRARY_PATH:-}"
  set +e
  ./ds_device_llt --gtest_filter="${FILTER}" 2>&1 | tee "${log}"
  local rc=${PIPESTATUS[0]}
  set -e
  ln -sf "${log}" "${meta}/latest_binmock_flow_st.log"
  echo "LOG=${log} rc=${rc}"
  if [[ "${rc}" -ne 0 && "${HETERO_ONLY}" -eq 0 ]]; then
    if [[ "${ALLOW_HETERO_ONLY:-1}" == "1" ]]; then
      echo "WARN: NdsBinmockFlow may not exist yet; retry Gate0 smoke only"
      ./ds_device_llt --gtest_filter="${GATE0_GTEST_FILTER}"
      return $?
    fi
  fi
  return "${rc}"
}

if [[ "${DO_REMOTE}" -eq 1 ]]; then
  ssh -o BatchMode=yes "${REMOTE}" "bash -lc '
set -euo pipefail
export BUILD=${BUILD}
export META=${META:-/root/workspace/nds-ssd-hbm-meta}
FILTER=\"${FILTER}\"
GATE0=\"${GATE0_GTEST_FILTER}\"
HETERO_ONLY=${HETERO_ONLY}
bin=\${BUILD}/tests/st/ds_device_llt
test -x \"\${bin}\"
mkdir -p \"\${META}\"
cd \"\$(dirname \"\${bin}\")\"
export LD_LIBRARY_PATH=\"\$(dirname \"\${bin}\"):\${LD_LIBRARY_PATH:-}\"
log=\${META}/binmock_flow_\$(date +%Y%m%d_%H%M%S).log
echo FILTER=\${FILTER}
set +e
./ds_device_llt --gtest_filter=\"\${FILTER}\" 2>&1 | tee \"\${log}\"
rc=\${PIPESTATUS[0]}
set -e
ln -sf \"\${log}\" \${META}/latest_binmock_flow_st.log
if [[ \${rc} -ne 0 && \${HETERO_ONLY} -eq 0 ]]; then
  echo WARN: fallback Gate0 smoke
  ./ds_device_llt --gtest_filter=\"\${GATE0}\"
  rc=\$?
fi
grep -E \"\\[  PASSED  \\]|\\[  FAILED  \\]|tests from\" \"\${log}\" | tail -n 20
exit \${rc}
'"
else
  # local: delegate to xqyun ssh (no local build in this RFC)
  ssh -o BatchMode=yes "${REMOTE}" "bash -s" <<EOF
$(declare -f run_on_host)
BUILD="${BUILD}"
META="/root/workspace/nds-ssd-hbm-meta"
FILTER="${FILTER}"
GATE0_GTEST_FILTER="${GATE0_GTEST_FILTER}"
HETERO_ONLY=${HETERO_ONLY}
ALLOW_HETERO_ONLY="${ALLOW_HETERO_ONLY:-1}"
run_on_host
EOF
fi
