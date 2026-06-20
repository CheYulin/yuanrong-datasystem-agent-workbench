#!/usr/bin/env bash
# dsbench SINGLE smoke on tiantiyun (or --node): bootstrap + dsbench show + minimal kv run.
#
# Usage:
#   bash scripts/testing/bench/run_dsbench_smoke_remote.sh [--node tiantiyun-80c128g]
#   bash scripts/testing/bench/run_dsbench_smoke_remote.sh --evidence-dir results/harness/manual-dsbench
#
# Writes bench_results.json summary when EVIDENCE_DIR is set.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${BENCH_DIR}/../../development/lib" && pwd)"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/remote_defaults.sh"
. "${LIB_DIR}/timing.sh"

NODE="$(node_role_default build)"
EVIDENCE_DIR=""
WORKER_ADDR="${WORKER_ADDR:-127.0.0.1:31501}"
SKIP_BOOTSTRAP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node) NODE="$2"; shift 2 ;;
    --evidence-dir) EVIDENCE_DIR="$2"; shift 2 ;;
    --worker) WORKER_ADDR="$2"; shift 2 ;;
    --skip-bootstrap) SKIP_BOOTSTRAP=1; shift ;;
    -h|--help)
      sed -n '1,14p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

init_remote "${NODE}"
DS_REMOTE="${REMOTE_BASE}/yuanrong-datasystem"

banner "dsbench smoke on ${REMOTE}"

if [[ "${SKIP_BOOTSTRAP}" -eq 0 ]]; then
  bash "${BENCH_DIR}/bootstrap_bench_cluster.sh" --node "${NODE}" --workers "${WORKER_ADDR}"
fi

LOG_FILE="${EVIDENCE_DIR:-/tmp}/dsbench_smoke.log"
mkdir -p "$(dirname "${LOG_FILE}")"

ssh_remote "${REMOTE}" bash -s -- "${WORKER_ADDR}" "${DS_REMOTE}" <<'REMOTE' | tee "${LOG_FILE}"
set -euo pipefail
export PATH="${HOME}/.local/bin:${PATH}"
WORKER_ADDR="$1"
DS_REMOTE="$2"

if ! command -v dsbench >/dev/null 2>&1; then
  WHEEL="$(find "${DS_REMOTE}/output" "${DS_REMOTE}/build/output" -name 'openyuanrong_datasystem*.whl' 2>/dev/null | head -1 || true)"
  if [[ -n "${WHEEL}" ]]; then
    echo "[dsbench-smoke] Installing wheel: ${WHEEL}"
    python3 -m pip install --user --force-reinstall "${WHEEL}"
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
fi

command -v dsbench >/dev/null 2>&1 || {
  echo "[dsbench-smoke] ERROR: dsbench not available" >&2
  exit 1
}

echo "=== dsbench show ==="
dsbench show || true

echo ""
echo "=== dsbench kv SINGLE smoke ==="
START=$(date +%s)
dsbench kv \
  -n 50 -s 1MB -c 4 -t 1 -b 1 \
  -p "bench_smoke" \
  -S "${WORKER_ADDR}" \
  -G "${WORKER_ADDR}"
RC=$?
ELAPSED=$(( $(date +%s) - START ))
echo "[dsbench-smoke] elapsed_sec=${ELAPSED} exit_code=${RC}"
exit "${RC}"
REMOTE
SMOKE_RC=${PIPESTATUS[0]}

if [[ -n "${EVIDENCE_DIR}" ]]; then
  mkdir -p "${EVIDENCE_DIR}"
  python3 - <<PY
import json, re, pathlib
log = pathlib.Path("${LOG_FILE}").read_text(encoding="utf-8", errors="replace")
has_table = bool(re.search(r"\|\s*\d+\s*\|\s*set\s*\|", log))
failed = bool(re.search(r"ERROR: WarmUp|ERROR: dsbench_cpp execution failed", log))
summary = {
    "tool": "dsbench",
    "mode": "single",
    "worker": "${WORKER_ADDR}",
    "status": "PASS" if ${SMOKE_RC} == 0 and has_table and not failed else "FAIL",
    "exit_code": ${SMOKE_RC},
    "log": "${LOG_FILE}",
}
pathlib.Path("${EVIDENCE_DIR}/bench_results.json").write_text(json.dumps(summary, indent=2) + "\n")
PY
fi

if [[ "${SMOKE_RC}" -ne 0 ]]; then
  log_error "dsbench smoke FAILED (${NODE}). Log: ${LOG_FILE}"
  exit "${SMOKE_RC}"
fi

log_info "dsbench smoke OK (${NODE}). Log: ${LOG_FILE}"
