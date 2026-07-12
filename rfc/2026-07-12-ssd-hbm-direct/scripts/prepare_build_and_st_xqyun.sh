#!/usr/bin/env bash
# SSD→HBM Gate 0 / verify on xqyun — follows workbench worktree-verify + ds-build conventions.
#
# Mirrors:
#   scripts/testing/verify/run_worktree_verify_remote.sh  (isolated worktree + DS_OPENSOURCE_DIR + ctest)
#   extract/.../ds-build  (cmake build.sh -t build)
#   extract/.../ds-dev    (ST via ctest -R)
#
# Layout (xqyun; does NOT use tiantiyun /home/cache, does NOT reuse other trees' binaries):
#   REPO   = /root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct
#   BUILD  = /root/workspace/build-ssd-hbm-direct
#   CACHE  = /root/.cache/yuanrong-datasystem-third-party   # nodes.yaml xqyun thirdparty_cache
#   LOGS   = /root/workspace/nds-ssd-hbm-meta
#
# Usage:
#   bash prepare_build_and_st_xqyun.sh                 # sync + build + HeteroD2H ST
#   bash prepare_build_and_st_xqyun.sh --skip-sync
#   bash prepare_build_and_st_xqyun.sh --skip-build    # ST only (requires prior build)
#   bash prepare_build_and_st_xqyun.sh --build-only
#   ST_CTEST_REGEX='...' bash prepare_build_and_st_xqyun.sh --skip-build  # override Gate0 filter
#
set -euo pipefail

REMOTE="${REMOTE:-xqyun-32c32g}"
REPO="${REPO:-/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct}"
BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"
META="${META:-/root/workspace/nds-ssd-hbm-meta}"
# nodes.yaml → xqyun-32c32g.thirdparty_cache
CACHE="${DS_OPENSOURCE_DIR:-/root/.cache/yuanrong-datasystem-third-party}"
LOCAL="${LOCAL:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
IGNORE="${IGNORE:-/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/development/sync/sync_to_xqyun.rsyncignore}"
JOBS="${BUILD_JOBS:-16}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/gtest_filters.sh"
ST_CTEST_REGEX="${ST_CTEST_REGEX:-${GATE0_GTEST_FILTER}}"
CTEST_JOBS_ST="${CTEST_JOBS_ST:-1}"

SKIP_SYNC=0
SKIP_BUILD=0
BUILD_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --skip-sync) SKIP_SYNC=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --build-only) BUILD_ONLY=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
  esac
done

echo "== Gate0 xqyun isolated verify (ds-build/ds-dev style) =="
echo "REMOTE=$REMOTE REPO=$REPO BUILD=$BUILD CACHE=$CACHE"

if [[ "${SKIP_SYNC}" -eq 0 ]]; then
  echo "== sync local worktree -> remote (rsync, no --delete on build) =="
  ssh -o BatchMode=yes "$REMOTE" "mkdir -p '$REPO' '$META'"
  rsync -az --human-readable --info=stats2 --exclude-from="$IGNORE" "$LOCAL/" "${REMOTE}:${REPO}/"
fi

if [[ "${SKIP_BUILD}" -eq 0 ]]; then
  echo "== build (ds-build: build.sh -t build, DS_OPENSOURCE_DIR=cache) =="
  ssh -o BatchMode=yes "$REMOTE" "bash -lc '
set -euo pipefail
test -d ${CACHE}/openssl_23b00df3cf1669c598eec1e4f433ef1ca8c9d7a2e90a858e28c531726b25e5ea
mkdir -p ${META} ${BUILD}
cd ${REPO}
export DS_OPENSOURCE_DIR=${CACHE}
echo DS_OPENSOURCE_DIR=\$DS_OPENSOURCE_DIR
echo BUILD_START=\$(date -Is)
# Match run_worktree_verify_remote: -t build -B <isolated> -j N
# Note: current datasystem build.sh has no -b; cmake is default (ds-build wrapper -b is legacy).
# -X off: no NPU; device ST uses AclDeviceManagerMock when ProbePhysicalBackend=UNKNOWN
# -P off: skip python for faster Gate 0
# -i on: incremental after first configure
bash build.sh -t build -X off -P off -B ${BUILD} -j ${JOBS} -i on \
  2>&1 | tee ${META}/gate0_build.log | tail -n 60
echo BUILD_END=\$(date -Is)
test -x ${BUILD}/tests/st/ds_device_llt
echo HAS_DEVICE_LLT=1 path=${BUILD}/tests/st/ds_device_llt
'"
fi

if [[ "${BUILD_ONLY}" -eq 1 ]]; then
  echo "BUILD_ONLY done"
  exit 0
fi

echo "== ST (ds-dev style: ctest -R ${ST_CTEST_REGEX}) =="
ssh -o BatchMode=yes "$REMOTE" "bash -lc '
set -euo pipefail
test -x ${BUILD}/tests/st/ds_device_llt
mkdir -p ${BUILD}/tests/st/cluster ${META}
# same helper as run_worktree_verify_remote
ln -sf ${REPO}/tests/st/cluster/mock_obs_service.py \
  ${BUILD}/tests/st/cluster/mock_obs_service.py 2>/dev/null || true
cd ${BUILD}/tests/st
export LD_LIBRARY_PATH=\"${BUILD}/tests/st:\${LD_LIBRARY_PATH:-}\"
STAMP=\$(date -u +%Y%m%d_%H%M%S)
LOG=${META}/gate0_st_\${STAMP}.log
ln -sf \"\$LOG\" ${META}/latest_gate0_st.log
set +e
# Prefer gtest filter on ds_device_llt for device suite (ctest label may wrap whole binary)
# Gate0: 5 focused HeteroD2HTest cases only — see gtest_filters.sh / test-walkthrough.md
./ds_device_llt --gtest_filter=\"${ST_CTEST_REGEX}\" 2>&1 | tee \"\$LOG\"
RC=\${PIPESTATUS[0]}
set -e
grep -E \"\\[  PASSED  \\]|\\[  FAILED  \\]|tests from\" \"\$LOG\" | tail -n 20
echo RC=\$RC LOG=\$LOG
exit \$RC
'"
