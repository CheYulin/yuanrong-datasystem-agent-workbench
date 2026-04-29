#!/usr/bin/env bash
# Bazel run zmq_rpc_queue_latency_repl on remote
# Usage:
#   ./bazel_run.sh           # run repl, default 5 seconds
#   ./bazel_run.sh 10        # run repl, 10 seconds
set -euo pipefail

REMOTE="root@xqyun-32c32g"
REMOTE_DS="/root/workspace/git-repos/yuanrong-datasystem"
DS_OPENSOURCE_DIR_REMOTE="/root/.cache/yuanrong-datasystem-third-party"
LOCAL_RESULTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../results" && pwd)"

mkdir -p "${LOCAL_RESULTS_DIR}"

DURATION_ARG="${1:-5}"

echo "REMOTE=${REMOTE}"
echo "REMOTE_DS=${REMOTE_DS}"
echo "DS_OPENSOURCE_DIR=${DS_OPENSOURCE_DIR_REMOTE}"
echo "DURATION_ARG=${DURATION_ARG}"
echo "LOCAL_RESULTS_DIR=${LOCAL_RESULTS_DIR}"
echo

LOGFILE="${LOCAL_RESULTS_DIR}/zmq_rpc_queue_latency_repl.log"

echo "=== Running zmq_rpc_queue_latency_repl (duration=${DURATION_ARG}s) ==="
echo "=== Log: ${LOGFILE} ==="

REMOTE_CMD="cd /root/workspace/git-repos/yuanrong-datasystem && bazel run '//tests/st/common/rpc/zmq:zmq_rpc_queue_latency_repl' -- --logtostderr=1 --duration=${DURATION_ARG}"

ssh "${REMOTE}" "${REMOTE_CMD}" 2>&1 | tee "${LOGFILE}"

echo
echo "Run done. Logs in ${LOCAL_RESULTS_DIR}/"
ls -lh "${LOCAL_RESULTS_DIR}/"
