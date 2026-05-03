#!/usr/bin/env bash
# Remote-only: quick Bazel UT that locks KvMetricId tail / KV_METRIC_DESCS consistency
# (URMA + worker-get breakdown sits after allocator + ZMQ queue-flow enums).
#
# Prerequisites: yuanrong-datasystem already synced to REMOTE_DS (see rsync_datasystem.sh)
#       and incremental build usable (often run after bazel_build.sh).
#
# Usage (from this directory):
#   ./bazel_run_kv_metric_urma_layout_ut.sh
#
# Env (see repl_remote_common.inc.sh): REMOTE, REMOTE_DS, DS_OPENSOURCE_DIR_REMOTE, BAZEL_JOBS
# Optional overrides:
#   KV_METRICS_UT_TARGET   Default //tests/ut/common/metrics:metrics_test
#   KV_METRICS_UT_FILTER   Default MetricsTest.kv_metric_urma_id_layout_test
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=repl_remote_common.inc.sh
source "${SCRIPT_DIR}/repl_remote_common.inc.sh"

TARGET="${KV_METRICS_UT_TARGET:-//tests/ut/common/metrics:metrics_test}"
FILTER="${KV_METRICS_UT_FILTER:-MetricsTest.kv_metric_urma_id_layout_test}"

echo "REMOTE=${REMOTE}"
echo "REMOTE_DS=${REMOTE_DS}"
echo "DS_OP=${DS_OPENSOURCE_DIR_REMOTE}"
echo "TARGET=${TARGET}"
echo "FILTER=${FILTER}"

ssh "${REMOTE}" bash -s \
  "${REMOTE_DS}" \
  "${DS_OPENSOURCE_DIR_REMOTE}" \
  "${BAZEL_JOBS}" \
  "${TARGET}" \
  "${FILTER}" \
  <<'REMOTESCRIPT'
set -euo pipefail
REMOTE_DS="$1"
DS_OP="$2"
JOBS="$3"
TARGET="$4"
FILTER="$5"
export DS_OPENSOURCE_DIR="${DS_OP}"
mkdir -p "${DS_OPENSOURCE_DIR}"
cd "${REMOTE_DS}"

bazel test "${TARGET}" --jobs="${JOBS}" \
  --define=enable_urma=false \
  --experimental_ui_max_stdouterr_bytes=-1 \
  --test_filter="${FILTER}" \
  --test_output=all
REMOTESCRIPT
