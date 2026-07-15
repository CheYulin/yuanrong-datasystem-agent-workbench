#!/usr/bin/env bash
# Run focused Gate0 HeteroD2H ST on xqyun via ctest (correct LD_LIBRARY_PATH).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${DIR}/gtest_filters.sh"
# shellcheck disable=SC1091
REMOTE="${REMOTE:-xqyun-32c32g}"
BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"
FILTER="${FILTER:-${NDS_UT_GTEST_FILTER}}"
META="${META:-/root/workspace/nds-ssd-hbm-meta}"
LOG="${META}/ds_ut_nds.log"

echo "Gate0: ctest ds_ut_nds GTEST_FILTER=${FILTER}"

ssh -o BatchMode=yes "$REMOTE" "bash -s" <<REMOTE
set -euo pipefail
BUILD="${BUILD}"
META="${META}"
LOG="${LOG}"
FILTER='${FILTER}'
mkdir -p "\${META}"
test -x "\${BUILD}/tests/ut/ds_ut_nds"

echo "RUN ctest -R ds_ut_nds GTEST_FILTER=\${GTEST_FILTER}"
set +e
ctest --test-dir "\${BUILD}" --output-on-failure -R '^ds_ut_nds\$' -j 1 >"\${LOG}" 2>&1
cd /root/workspace/build-ssd-hbm-direct/tests/ut
export LD_LIBRARY_PATH=/root/workspace/build-ssd-hbm-direct/tests/ut
set +e
./ds_ut_nds --gtest_filter= > 2>&1
set -e
ln -sf "\${LOG}" "\${META}/latest_ds_ut_nds.log"
echo RC=\$RC LOG=\$LOG
grep -E '\\[  PASSED  \\]|\\[  FAILED  \\]|tests from|PASSED|FAILED' "\${LOG}" | tail -n 30
tail -n 20 "\${LOG}"
exit \$RC
REMOTE
