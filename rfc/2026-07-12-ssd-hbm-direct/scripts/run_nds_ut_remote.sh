#!/usr/bin/env bash
# Run ds_ut_nds (Tasks 1–3 UT) on xqyun isolated build tree.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${DIR}/gtest_filters.sh"
REMOTE="${REMOTE:-xqyun-32c32g}"
REPO="${REPO:-/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct}"
BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"
CACHE="${DS_OPENSOURCE_DIR:-/root/.cache/yuanrong-datasystem-third-party}"
LOCAL="${LOCAL:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
IGNORE="${IGNORE:-/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/development/sync/sync_to_xqyun.rsyncignore}"
JOBS="${BUILD_JOBS:-16}"

SKIP_SYNC=0
for arg in "$@"; do
  case "$arg" in
    --skip-sync) SKIP_SYNC=1 ;;
  esac
done

if [[ "${SKIP_SYNC}" -eq 0 ]]; then
  ssh -o BatchMode=yes "$REMOTE" "mkdir -p '$REPO'"
  rsync -az --exclude-from="$IGNORE" "$LOCAL/" "${REMOTE}:${REPO}/"
fi

ssh -o BatchMode=yes "$REMOTE" "bash -lc '
set -euo pipefail
export DS_OPENSOURCE_DIR=${CACHE}
cd ${REPO}
bash build.sh -t build -X off -P off -B ${BUILD} -j ${JOBS} -i on 2>&1 | tail -n 40
test -x ${BUILD}/tests/ut/ds_ut_nds
cd ${BUILD}/tests/ut
./ds_ut_nds --gtest_filter='${NDS_UT_GTEST_FILTER}'
'"
