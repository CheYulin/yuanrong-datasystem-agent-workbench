#!/usr/bin/env bash
# Full UT + ST on tiantiyun with failure recording under /home/cache/verify-logs.
#
# Usage:
#   bash scripts/testing/verify/run_full_ut_st_remote.sh [--node tiantiyun-80c128g] [--skip-rsync]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${SCRIPT_DIR}/../../lib" && pwd)"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/remote_defaults.sh"
. "${LIB_DIR}/common.sh"

NODE="${NODE_NAME:-$(node_role_default verify_ut)}"
SKIP_RSYNC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node) NODE="$2"; shift 2 ;;
    --skip-rsync) SKIP_RSYNC=1; shift ;;
    *) shift ;;
  esac
done

init_remote "${NODE}"

BUILD="${BUILD_DIR:-/home/cache/build-remote-datasystem}"
SRC="${REMOTE_BASE}/yuanrong-datasystem"
LOG="/home/cache/verify-logs"
OLD_BUILD="/root/workspace/build-remote-datasystem"

banner "Full UT/ST on ${REMOTE} (build=${BUILD}, logs=${LOG})"

ssh_remote "${REMOTE}" bash -s -- "${BUILD}" "${SRC}" "${LOG}" "${OLD_BUILD}" "${SKIP_RSYNC}" <<'REMOTE'
set -uo pipefail
BUILD="$1"
SRC="$2"
LOG="$3"
OLD_BUILD="$4"
SKIP_RSYNC="$5"

mkdir -p "$LOG" "$BUILD/tests/st/cluster"

SUMMARY="$LOG/verify_summary.md"
: > "$SUMMARY"
{
  echo "# Full UT/ST verification"
  echo ""
  echo "- Started: $(date -Is)"
  echo "- Build: \`${BUILD}\`"
  echo "- Source: \`${SRC}\`"
  echo "- Host: $(hostname -s)"
  echo ""
} >> "$SUMMARY"

record_failures() {
  local phase="$1"
  local logfile="$2"
  local out="$LOG/${phase}_failures.txt"
  local detail="$LOG/${phase}_failure_details.log"
  [ -f "$logfile" ] || return 0
  grep -oE '[0-9]+ - [^(]+ \(Failed\)' "$logfile" 2>/dev/null | sort -u > "$out" || true
  grep -B30 '(Failed)' "$logfile" 2>/dev/null | grep -E 'Failure|error while loading|Which is:|Expected|FAILED|Subprocess is abnormal|cannot find' \
    >> "$detail" 2>/dev/null || true
  local passed failed total
  passed=$(grep -oE '[0-9]+% tests passed, [0-9]+ tests failed out of [0-9]+' "$logfile" | tail -1 || true)
  {
    echo "## ${phase}"
    echo ""
    echo "- Log: \`${logfile}\`"
    echo "- Summary: ${passed:-pending}"
    echo "- Failure list: \`${out}\` ($(wc -l < "$out" 2>/dev/null || echo 0) cases)"
    echo ""
  } >> "$SUMMARY"
}

if [[ "${SKIP_RSYNC}" -eq 0 ]] && [[ ! -f "${BUILD}/CMakeCache.txt" ]] && [[ -d "${OLD_BUILD}" ]]; then
  echo "[$(date -Is)] rsync ${OLD_BUILD} -> ${BUILD} ..." | tee -a "$LOG/runner.out"
  rsync -a "${OLD_BUILD}/" "${BUILD}/"
fi
echo "[$(date -Is)] build size: $(du -sh "$BUILD" | cut -f1)" | tee -a "$LOG/runner.out"

ln -sf "${SRC}/tests/st/cluster/mock_obs_service.py" "${BUILD}/tests/st/cluster/mock_obs_service.py"

JEM=$(find /tmp/127e9ab4befe707261b03d285940e52deb3cbf0eafb08714d037a6485b399ef3 -name libjemalloc.so.2 2>/dev/null | head -1 | xargs dirname 2>/dev/null || true)
TP=/tmp/127e9ab4befe707261b03d285940e52deb3cbf0eafb08714d037a6485b399ef3
GOOD="${SRC}/output/cpp/lib:${BUILD}/src/datasystem/worker"
GOOD="${GOOD}${JEM:+:${JEM}}"
GOOD="${GOOD}:${TP}/openssl_23b00df3cf1669c598eec1e4f433ef1ca8c9d7a2e90a858e28c531726b25e5ea/lib"
GOOD="${GOOD}:${TP}/iconv_7f7e38ce74693c1c2b38edb8a26491ba1fe81f5b5886e1056a1f68a217f8a1f8/lib"
GOOD="${GOOD}:${TP}/absl_d5a2f7056d0e731c31f9599cfb697752b3bce2b77af8605402a8ef3d320be07a/lib64"

for f in "${BUILD}"/tests/ut/*_tests.cmake "${BUILD}"/tests/st/*_tests.cmake; do
  [[ -f "$f" ]] || continue
  sed -i "s|LD_LIBRARY_PATH=[^=]*src/datasystem/worker:|LD_LIBRARY_PATH=${GOOD}:|g" "$f"
done

cd "${BUILD}"

echo "[$(date -Is)] === FULL UT ===" | tee "$LOG/full_ut.log"
set +e
ctest --label-regex "ut level|object ut|stream ut" -j 40 --timeout 300 \
  --output-on-failure --output-junit "${LOG}/ut.junit.xml" 2>&1 | tee -a "$LOG/full_ut.log"
UT_EXIT=$?
set -e
echo "UT_EXIT=${UT_EXIT}" | tee -a "$LOG/full_ut.log" "$LOG/runner.out"
[[ -f Testing/Temporary/LastTestsFailed.log ]] && cp Testing/Temporary/LastTestsFailed.log "$LOG/ut_LastTestsFailed.log"
record_failures ut "$LOG/full_ut.log"

echo "[$(date -Is)] === FULL ST ===" | tee "$LOG/full_st.log"
set +e
ctest --label-regex " st " -j 8 --timeout 600 \
  --output-on-failure --output-junit "${LOG}/st.junit.xml" 2>&1 | tee -a "$LOG/full_st.log"
ST_EXIT=$?
set -e
echo "ST_EXIT=${ST_EXIT}" | tee -a "$LOG/full_st.log" "$LOG/runner.out"
[[ -f Testing/Temporary/LastTestsFailed.log ]] && cp Testing/Temporary/LastTestsFailed.log "$LOG/st_LastTestsFailed.log"
record_failures st "$LOG/full_st.log"

{
  echo "## Final"
  echo ""
  echo "- Finished: $(date -Is)"
  echo "- UT_EXIT=${UT_EXIT}"
  echo "- ST_EXIT=${ST_EXIT}"
  echo "- JUnit: \`${LOG}/ut.junit.xml\`, \`${LOG}/st.junit.xml\`"
} >> "$SUMMARY"

echo "[$(date -Is)] ALL DONE UT=${UT_EXIT} ST=${ST_EXIT}" | tee -a "$LOG/runner.out"
REMOTE

log_info "Remote logs: ${LOG}/verify_summary.md, ut_failures.txt, st_failures.txt"
