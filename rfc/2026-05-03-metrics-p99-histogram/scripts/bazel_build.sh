#!/usr/bin/env bash
# Remote Bazel build: metrics UT package + ST histogram_p99_perf_test (ds_cc_test executable).
# Always passes --define=enable_urma=false so builds do not require URMA SDK / select(//:enable_urma).
#
# Usage:
#   bash scripts/bazel_build.sh
#   BAZEL_JOBS=16 bash scripts/bazel_build.sh
#   BAZEL_EXTRA_OPTS='--some_flag' bash scripts/bazel_build.sh
#
set -euo pipefail

REMOTE="${REMOTE:-root@xqyun-32c32g}"
REMOTE_DS="${REMOTE_DS:-/root/workspace/git-repos/yuanrong-datasystem}"
DS_OPENSOURCE_DIR_REMOTE="${DS_OPENSOURCE_DIR_REMOTE:-/root/.cache/yuanrong-datasystem-third-party}"
BAZEL_JOBS="${BAZEL_JOBS:-32}"
# Root //:enable_urma config_setting only matches enable_urma=true; force false so URMA
# sources/deps stay out. Append more bazel flags via BAZEL_EXTRA_OPTS.
BAZEL_NO_URMA=(--define=enable_urma=false)

echo "REMOTE=${REMOTE}"
echo "REMOTE_DS=${REMOTE_DS}"
echo "DS_OPENSOURCE_DIR_REMOTE=${DS_OPENSOURCE_DIR_REMOTE}"
echo "BAZEL_JOBS=${BAZEL_JOBS}"
echo "BAZEL_NO_URMA=${BAZEL_NO_URMA[*]}"
echo

ssh "${REMOTE}" bash -s <<EOF
set -euo pipefail
export DS_OPENSOURCE_DIR='${DS_OPENSOURCE_DIR_REMOTE}'
mkdir -p "\${DS_OPENSOURCE_DIR}"

cd '${REMOTE_DS}'

bazel info release 2>/dev/null || true

echo "=== bazel build (metrics UT + ST p99 perf) ==="
bazel build \
  '//tests/ut/common/metrics/...' \
  '//tests/st/common/metrics/...' \
  --jobs=${BAZEL_JOBS} \
  ${BAZEL_NO_URMA[@]} \
  ${BAZEL_EXTRA_OPTS:-}
EOF

echo "build done."
