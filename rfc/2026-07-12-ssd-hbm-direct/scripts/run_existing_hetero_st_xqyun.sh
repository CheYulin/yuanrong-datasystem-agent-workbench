#!/usr/bin/env bash
# Run focused Gate0 HeteroD2H ST on xqyun (5 cases only — NOT full device suite).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${DIR}/gtest_filters.sh"

REMOTE="${REMOTE:-xqyun-32c32g}"
LLT="${LLT:-/root/workspace/build-ssd-hbm-direct/tests/st/ds_device_llt}"
FILTER="${FILTER:-${GATE0_GTEST_FILTER}}"
META="${META:-/root/workspace/nds-ssd-hbm-meta}"
LOG="${META}/hetero_d2h_isolated_st.log"

echo "FILTER (Gate0 5 cases): ${FILTER}"

ssh -o BatchMode=yes "$REMOTE" "bash -lc '
set -e
mkdir -p ${META}
test -x ${LLT} || { echo MISSING_LLT=${LLT}; echo Build isolated tree first: prepare_build_and_st_xqyun.sh --build-only; exit 2; }
echo RUN ${LLT} --gtest_filter=${FILTER}
cd \$(dirname ${LLT})
export LD_LIBRARY_PATH=\"\$(dirname ${LLT}):\${LD_LIBRARY_PATH:-}\"
set +e
./ds_device_llt --gtest_filter=\"${FILTER}\" >${LOG} 2>&1
RC=\$?
set -e
ln -sf ${LOG} ${META}/latest_gate0_st.log
echo RC=\$RC LOG=${LOG}
grep -E \"\\[  PASSED  \\]|\\[  FAILED  \\]|tests from|RUN\" ${LOG} | tail -n 25
tail -n 15 ${LOG}
exit \$RC
'"
