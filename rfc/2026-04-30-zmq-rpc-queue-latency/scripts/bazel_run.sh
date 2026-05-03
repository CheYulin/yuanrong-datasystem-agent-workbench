#!/usr/bin/env bash
# Bazel **test** (ds_cc_test) zmq_rpc_queue_latency_repl on remote; logs land in LOCAL results/
#
# Default: redirect all output on remote to REMOTE_REPL_LOG_PATH, then scp pull.
# Avoids pushing a high-volume log stream through an SSH tty (can add latency / noise).
#
# Usage (from this directory):
#   ./bazel_run.sh              # 5s, remote file + scp (quiet)
#   ./bazel_run.sh 10           # 10s
#   ./bazel_run.sh --tee 10     # live ssh | tee (debug)
#
# Environment:
#   REMOTE_REPL_LOG_PATH                 Remote file before scp (default /tmp/…_capture.log)
#   ZMQ_RPC_QUEUE_LATENCY_SEC            Passed via --test_env from positional seconds (default 5)
#   ZMQ_RPC_QUEUE_LATENCY_TEST_TIMEOUT   Remote bazel --test_timeout sec (default 3600; passed from this host)
#   Hard-coded: `--experimental_ui_max_stdouterr_bytes=-1` keeps full test stdout when combined with a large build log.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=repl_remote_common.inc.sh
source "${SCRIPT_DIR}/repl_remote_common.inc.sh"

mkdir -p "${LOCAL_RESULTS_DIR}"

TEE_LOCAL=0
POSITIONAL_DURATION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tee) TEE_LOCAL=1 ;;
    -h|--help)
      sed -n '1,22p' "$0"
      exit 0
      ;;
    -*)
      echo "Unknown option: $1 (try --tee for live stream)" >&2
      exit 1
      ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then POSITIONAL_DURATION="$1"
      else echo "Unknown arg: $1" >&2; exit 1
      fi
      ;;
  esac
  shift
done

DURATION_ARG="${POSITIONAL_DURATION:-5}"

LOCAL_TEST_TIMEOUT="${ZMQ_RPC_QUEUE_LATENCY_TEST_TIMEOUT:-3600}"
echo "REMOTE=${REMOTE}"
echo "REMOTE_DS=${REMOTE_DS}"
echo "DS_OPENSOURCE_DIR=${DS_OPENSOURCE_DIR_REMOTE}"
echo "BAZEL_JOBS=${BAZEL_JOBS}"
echo "DURATION_ARG=${DURATION_ARG}"
echo "LOCAL_RESULTS_DIR=${LOCAL_RESULTS_DIR}"
echo "REMOTE_REPL_LOG_PATH=${REMOTE_REPL_LOG_PATH}"
if [[ "${TEE_LOCAL}" -eq 1 ]]; then
  echo "OUTPUT_MODE=ssh_stream_tee"
else
  echo "OUTPUT_MODE=remote_file_then_scp"
fi
echo

LOGFILE="${LOCAL_RESULTS_DIR}/${REPL_LOG_NAME}"

echo "=== Running zmq_rpc_queue_latency_repl (duration=${DURATION_ARG}s) ==="

if [[ "${TEE_LOCAL}" -eq 1 ]]; then
  echo "=== Log: ${LOGFILE} (ssh | tee) ==="
  {
    ssh "${REMOTE}" bash -s "${REMOTE_DS}" "${DS_OPENSOURCE_DIR_REMOTE}" "${BAZEL_JOBS}" \
      "${REPL_BAZEL_TARGET}" "${DURATION_ARG}" "${LOCAL_TEST_TIMEOUT}" <<'REMOTESCRIPT'
set -euo pipefail
REMOTE_DS="$1"
DS_OP="$2"
JOBS="$3"
TARGET="$4"
DUR="$5"
TEST_TIMEOUT="$6"
export DS_OPENSOURCE_DIR="${DS_OP}"
mkdir -p "${DS_OPENSOURCE_DIR}"
cd "${REMOTE_DS}"
bazel test "${TARGET}" --jobs="${JOBS}" \
  --experimental_ui_max_stdouterr_bytes=-1 \
  --test_output=all \
  --test_timeout="${TEST_TIMEOUT}" \
  --test_env=ZMQ_RPC_QUEUE_LATENCY_SEC="${DUR}" \
  --test_arg=--logtostderr=1 \
  --test_arg=--v=0
REMOTESCRIPT
  } 2>&1 | tee "${LOGFILE}"
  RUN_RC=${PIPESTATUS[0]}
else
  echo "=== Remote redirect -> ${REMOTE_REPL_LOG_PATH}, then scp -> ${LOGFILE} ==="
  set +e
  ssh "${REMOTE}" bash -s "${REMOTE_DS}" "${DS_OPENSOURCE_DIR_REMOTE}" "${BAZEL_JOBS}" \
    "${REPL_BAZEL_TARGET}" "${DURATION_ARG}" "${REMOTE_REPL_LOG_PATH}" "${LOCAL_TEST_TIMEOUT}" <<'REMOTESCRIPT'
set -euo pipefail
REMOTE_DS="$1"
DS_OP="$2"
JOBS="$3"
TARGET="$4"
DUR="$5"
OUT="$6"
TEST_TIMEOUT="$7"
export DS_OPENSOURCE_DIR="${DS_OP}"
mkdir -p "${DS_OPENSOURCE_DIR}"
cd "${REMOTE_DS}"
umask 077
rm -f "${OUT}"
bazel test "${TARGET}" --jobs="${JOBS}" \
  --experimental_ui_max_stdouterr_bytes=-1 \
  --test_output=all \
  --test_timeout="${TEST_TIMEOUT}" \
  --test_env=ZMQ_RPC_QUEUE_LATENCY_SEC="${DUR}" \
  --test_arg=--logtostderr=1 \
  --test_arg=--v=0 >"${OUT}" 2>&1
REMOTESCRIPT
  RUN_RC=$?
  set -e
  rm -f "${LOGFILE}"
  if ! scp "${REMOTE}:${REMOTE_REPL_LOG_PATH}" "${LOGFILE}"; then
    echo "ERROR: scp failed (remote log: ${REMOTE_REPL_LOG_PATH}) ssh_exit=${RUN_RC}" >&2
    exit "${RUN_RC}"
  fi
  SZ=$(wc -c <"${LOGFILE}" | tr -d ' ')
  echo "Pulled remote log (${SZ} bytes) -> ${LOGFILE}"
fi

echo
echo "Run done. Logs in ${LOCAL_RESULTS_DIR}/"
ls -lh "${LOCAL_RESULTS_DIR}/"
exit "${RUN_RC}"
