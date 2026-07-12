#!/usr/bin/env bash
# After binmock / Stage A/B: extract observability smoke signals from a log.
# Usage: bash run_obs_smoke.sh /path/to/worker_or_client.INFO.log
set -euo pipefail

LOG="${1:-}"
if [[ -z "${LOG}" || ! -f "${LOG}" ]]; then
  echo "Usage: $0 <glog-file>"
  exit 2
fi

echo "=== obs smoke: ${LOG} ==="

echo "--- Perf keys (NDS/HBM/Spill/H2D related) ---"
grep -E 'WORKER_NDS_|HBM_IPC_|WORKER_SPILL_READ|WORKER_SPILL_GET|CLIENT_MGET_H2D|CLIENT_H2D_|HETERO_CLIENT_MGET' "${LOG}" \
  | tail -n 80 || echo "(no matching Perf lines yet — expected before Task 8 lands)"

echo "--- nds_ structured log keywords ---"
grep -E 'nds_(hbm_|eligible|skip|io |fallback|deliver)' "${LOG}" | tail -n 50 \
  || echo "(no nds_ keywords yet)"

echo "--- fallback reasons ---"
grep -E 'nds_fallback reason=|nds_skip reason=' "${LOG}" | sed 's/.*reason=/reason=/' | sort | uniq -c \
  || true

echo "--- metrics_summary mention ---"
grep -c 'metrics_summary' "${LOG}" || true

WB_GEN=""
for c in \
  "$(cd "$(dirname "$0")/../../../scripts/metrics" 2>/dev/null && pwd)/gen_kv_perf_report.py" \
  "${HOME}/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/metrics/gen_kv_perf_report.py"; do
  if [[ -f "$c" ]]; then WB_GEN="$c"; break; fi
done
if [[ -n "${WB_GEN}" ]]; then
  echo "--- gen_kv_perf_report (filtered) ---"
  python3 "${WB_GEN}" --perf-keys 'WORKER_NDS_,HBM_IPC_,WORKER_SPILL,CLIENT_MGET_H2D,CLIENT_H2D_,HETERO_CLIENT_MGET' "${LOG}" 2>/dev/null \
    | tail -n 60 || true
else
  echo "HINT: install/run workbench scripts/metrics/gen_kv_perf_report.py for full tree"
fi

echo "=== done ==="
