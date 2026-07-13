#!/usr/bin/env bash
# Run focused ds_ut_nds UT on xqyun via direct gtest (worker LD_LIBRARY_PATH).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE="${REMOTE:-xqyun-32c32g}"
BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"

# shellcheck disable=SC1091
source "${DIR}/gtest_filters.sh"
# shellcheck disable=SC1091
source "${DIR}/lib_ctest_env.sh"
FILTER="${FILTER:-${NDS_UT_GTEST_FILTER}}"
META="${META:-/root/workspace/nds-ssd-hbm-meta}"
LOG="${META}/ds_ut_nds.log"

echo "UT: direct gtest ds_ut_nds GTEST_FILTER=${FILTER}"

ssh -o BatchMode=yes "$REMOTE" "bash -s" <<REMOTE
set -euo pipefail
BUILD="${BUILD}"
META="${META}"
LOG="${LOG}"
FILTER='${FILTER}'
mkdir -p "\${META}"
test -x "\${BUILD}/tests/ut/ds_ut_nds"

export LD_LIBRARY_PATH="\${BUILD}/src/datasystem/worker:\${BUILD}/tests/ut:\${LD_LIBRARY_PATH:-}"
export GTEST_FILTER="\${FILTER}"
echo "RUN ds_ut_nds GTEST_FILTER=\${GTEST_FILTER}"
set +e
cd "\${BUILD}/tests/ut"
./ds_ut_nds --gtest_filter="\${FILTER}" >"\${LOG}" 2>&1
RC=\$?
set -e
ln -sf "\${LOG}" "\${META}/latest_ds_ut_nds.log"
echo RC=\$RC LOG=\$LOG
grep -E '\\[  PASSED  \\]|\\[  FAILED  \\]|tests from|PASSED|FAILED' "\${LOG}" | tail -n 30
tail -n 20 "\${LOG}"
exit \$RC
REMOTE
