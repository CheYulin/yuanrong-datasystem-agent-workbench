#!/usr/bin/env bash
# Direct gtest fallback when ctest tree is broken (ds_ut_tests.cmake missing).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE="${REMOTE:-xqyun-32c32g}"
BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"
# shellcheck disable=SC1091
source "${DIR}/gtest_filters.sh"
FILTER="${FILTER:-${NDS_UT_GTEST_FILTER}}"

ssh -o BatchMode=yes "$REMOTE" "bash -s" <<REMOTE
set -euo pipefail
set -o noglob
BUILD='${BUILD}'
FILTER='${FILTER}'
UT_BIN="\${BUILD}/tests/ut/ds_ut_nds"
WORKER_BIN="\${BUILD}/src/datasystem/worker"
META=/root/workspace/nds-ssd-hbm-meta
LOG="\${META}/ds_ut_nds_direct.log"
mkdir -p "\${META}"
test -x "\${UT_BIN}"
export LD_LIBRARY_PATH="\${WORKER_BIN}:\${BUILD}/tests/ut:\${LD_LIBRARY_PATH:-}"
export GTEST_FILTER="\${FILTER}"
echo "RUN \${UT_BIN} GTEST_FILTER=\${GTEST_FILTER}"
set +e
"\${UT_BIN}" --gtest_filter="\${GTEST_FILTER}" >"\${LOG}" 2>&1
RC=\$?
set -e
ln -sf "\${LOG}" "\${META}/latest_ds_ut_nds.log"
grep -E '\\[  PASSED  \\]|\\[  FAILED  \\]|tests from' "\${LOG}" | tail -n 20
echo RC=\$RC
exit \$RC
REMOTE
