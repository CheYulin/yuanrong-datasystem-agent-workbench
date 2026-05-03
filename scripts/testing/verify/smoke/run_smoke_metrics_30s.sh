#!/usr/bin/env bash
# ~30s wall-clock DataSystem smoke with metrics every 2s (log_monitor_interval_ms=2000).
# Wraps run_smoke.py with tuned RPC load + prints collected metrics after the run.
#
# Prereq: built datasystem_worker + yr wheel. Sibling layout:
#   yuanrong-datasystem-agent-workbench/
#   yuanrong-datasystem/
# Remote: rsync → build whl → pip install → run; see REMOTE_SMOKE.md
#
# Usage (from workbench root or any cwd):
#   bash scripts/testing/verify/smoke/run_smoke_metrics_30s.sh
#   bash scripts/testing/verify/smoke/run_smoke_metrics_30s.sh --read-loop-sec 16 --min-zmq-metric-count 8
# Env:
#   READ_LOOP_SEC / LOG_MONITOR_INTERVAL_MS  optional defaults below.
#   Verify/build is usually run on dev host (e.g. xqyun-32c32g); this script only invokes python3.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_PY="${SCRIPT_DIR}/run_smoke.py"
WORKBENCH_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

if [[ ! -f "${SMOKE_PY}" ]]; then
  echo "Missing ${SMOKE_PY}" >&2
  exit 1
fi

# Tuned in run_smoke.py docstring for ~30s end-to-end; raise read-loop / keys if ZMQ gate fails.
READ_LOOP_SEC="${READ_LOOP_SEC:-12}"
LOG_MS="${LOG_MONITOR_INTERVAL_MS:-2000}"

RUN_LOG="$(mktemp)"
trap 'rm -f "${RUN_LOG}"' EXIT

echo "[run_smoke_metrics_30s] workbench=${WORKBENCH_ROOT}"
echo "[run_smoke_metrics_30s] log_monitor_interval_ms=${LOG_MS} (expect ~${LOG_MS}ms between metrics_summary ticks)"
echo "[run_smoke_metrics_30s] read_loop_sec=${READ_LOOP_SEC} (override with READ_LOOP_SEC=... or pass --read-loop-sec)"

set +e
python3 "${SMOKE_PY}" \
  --read-loop-sec "${READ_LOOP_SEC}" \
  --keys 80 \
  --tenants 2 \
  --clients-per-tenant 2 \
  --min-zmq-metric-count 5 \
  --log-monitor-interval-ms "${LOG_MS}" \
  "$@" >"${RUN_LOG}" 2>&1
PyStatus=$?
set -e
cat "${RUN_LOG}"

# Log line format: [HH:MM:SS] Log output: /path/to/results/smoke_test_YYYYMMDD_HHMMSS
LOG_DIR="$(grep -m1 'Log output:' "${RUN_LOG}" | sed 's/^.*Log output: //' | tr -d '\r' | sed 's/[[:space:]]*$//')"
if [[ -z "${LOG_DIR}" || ! -d "${LOG_DIR}" ]]; then
  echo "[run_smoke_metrics_30s] WARN: could not parse result dir from log; skip metrics print" >&2
  exit "${PyStatus}"
fi

echo ""
echo "======== ${LOG_DIR}/metrics_summary.txt ========"
if [[ -f "${LOG_DIR}/metrics_summary.txt" ]]; then
  cat "${LOG_DIR}/metrics_summary.txt"
else
  echo "(no metrics_summary.txt yet)"
fi

echo ""
echo "======== metrics_summary JSON lines (aggregated client/worker copies) ========"
# Shallow scan of copied artifacts (run_smoke flattens client_*/worker-* into LOG_DIR)
shopt -s nullglob
_json_hits=0
for f in "${LOG_DIR}"/client_glog_* "${LOG_DIR}"/worker-*; do
  [[ -f "${f}" ]] || continue
  if grep -q '"event":"metrics_summary"' "${f}" 2>/dev/null; then
    _n="$(grep -c '"event":"metrics_summary"' "${f}" 2>/dev/null || true)"
    echo "${f##*/}: ${_n} line(s)"
    _json_hits=$((_json_hits + _n))
  fi
done
echo "total metrics_summary JSON lines (matched files): ${_json_hits}"
shopt -u nullglob

echo ""
echo "[run_smoke_metrics_30s] full artifacts: ${LOG_DIR}"
exit "${PyStatus}"
