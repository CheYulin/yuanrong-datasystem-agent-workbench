#!/usr/bin/env bash
# Track① verify on tiantiyun (fallback when xqyun SSH unavailable).
# sync → incremental build → Gate0 ST → ds_ut_nds (direct gtest + worker LD_LIBRARY_PATH).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RFC="$(cd "$DIR/.." && pwd)"
RESULTS="$RFC/results.md"
# shellcheck disable=SC1091
source "${DIR}/gtest_filters.sh"

REMOTE="${REMOTE:-tiantiyun-80c128g}"
REPO="${REPO:-/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct}"
BUILD="${BUILD:-/home/cache/build-ssd-hbm-direct}"
LOCAL="${LOCAL:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
IGNORE="${IGNORE:-/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/development/sync/sync_to_xqyun.rsyncignore}"
CACHE="${DS_OPENSOURCE_DIR:-/home/ds-thirdparty-cache}"

SKIP_SYNC=0
for arg in "$@"; do
  case "$arg" in --skip-sync) SKIP_SYNC=1 ;; esac
done

append() { echo "| $(date '+%H:%M') | $1 | $2 | $3 |" >> "$RESULTS"; }

if [[ "$SKIP_SYNC" -eq 0 ]]; then
  echo "== sync to ${REMOTE} =="
  ssh -o BatchMode=yes "$REMOTE" "mkdir -p '$REPO'"
  rsync -az --exclude-from="$IGNORE" "$LOCAL/" "${REMOTE}:${REPO}/"
fi

echo "== build + tests on ${REMOTE} =="
ssh -o BatchMode=yes "$REMOTE" "bash -s" <<REMOTE
set -euo pipefail
REPO='${REPO}'
BUILD='${BUILD}'
CACHE='${CACHE}'
GATE0_FILTER='${GATE0_GTEST_FILTER}'
UT_FILTER='${NDS_UT_GTEST_FILTER}'
export DS_OPENSOURCE_DIR="\${CACHE}"
cd "\${REPO}"
echo BUILD_START=\$(date -Is)
bash build.sh -t build -X off -P off -B "\${BUILD}" -j 16 -i on 2>&1 | tail -n 40
echo BUILD_END=\$(date -Is)
test -x "\${BUILD}/tests/st/ds_device_llt"
test -x "\${BUILD}/tests/ut/ds_ut_nds"

META=/root/workspace/nds-ssd-hbm-meta
mkdir -p "\${META}"

echo "=== Gate0 ==="
GLOG="\${META}/gate0_\$(date +%Y%m%d_%H%M%S).log"
set +e
export LD_LIBRARY_PATH="\${BUILD}/src/datasystem/worker:\${BUILD}/tests/st:\${LD_LIBRARY_PATH:-}"
export GTEST_FILTER="\${GATE0_FILTER}"
cd "\${BUILD}/tests/st"
./ds_device_llt --gtest_filter="\${GATE0_FILTER}" >"\${GLOG}" 2>&1
G0=\$?
set -e
ln -sf "\${GLOG}" "\${META}/latest_gate0_st.log"
grep -E '\\[  PASSED  \\]|\\[  FAILED  \\]|tests from' "\${GLOG}" | tail -n 15
echo GATE0_RC=\$G0

echo "=== ds_ut_nds ==="
ULOG="\${META}/ut_nds_\$(date +%Y%m%d_%H%M%S).log"
set +e
export LD_LIBRARY_PATH="\${BUILD}/src/datasystem/worker:\${BUILD}/tests/ut:\${LD_LIBRARY_PATH:-}"
export GTEST_FILTER="\${UT_FILTER}"
cd "\${BUILD}/tests/ut"
./ds_ut_nds --gtest_filter="\${UT_FILTER}" >"\${ULOG}" 2>&1
UT=\$?
set -e
ln -sf "\${ULOG}" "\${META}/latest_ut_nds.log"
grep -E '\\[  PASSED  \\]|\\[  FAILED  \\]|tests from' "\${ULOG}" | tail -n 15
echo UT_RC=\$UT

exit \$(( G0 != 0 ? G0 : UT ))
REMOTE
rc=$?
if [[ $rc -eq 0 ]]; then
  append "verify_track1_tiantiyun" "PASS" "Gate0+UT ctest green (xqyun fallback)"
else
  append "verify_track1_tiantiyun" "FAIL" "rc=$rc on ${REMOTE}"
fi
exit $rc
