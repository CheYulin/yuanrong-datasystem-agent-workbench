#!/usr/bin/env bash
# kvtest Benchmark set_local smoke on tiantiyun: build kvtest + run 3 rounds.
#
# Usage:
#   bash scripts/testing/bench/run_kvtest_smoke_remote.sh [--node tiantiyun-80c128g]
#   bash scripts/testing/bench/run_kvtest_smoke_remote.sh --evidence-dir results/harness/manual-kvtest
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
SDK_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node) NODE="$2"; shift 2 ;;
    --evidence-dir) EVIDENCE_DIR="$2"; shift 2 ;;
    --worker) WORKER_ADDR="$2"; shift 2 ;;
    --sdk) SDK_DIR="$2"; shift 2 ;;
    --skip-bootstrap) SKIP_BOOTSTRAP=1; shift ;;
    -h|--help)
      sed -n '1,12p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

init_remote "${NODE}"
DS_REMOTE="${REMOTE_BASE}/yuanrong-datasystem"
KVTEST_REMOTE="${DS_REMOTE}/tests/kvtest"

banner "kvtest smoke on ${REMOTE}"

if [[ "${SKIP_BOOTSTRAP}" -eq 0 ]]; then
  bash "${BENCH_DIR}/bootstrap_bench_cluster.sh" --node "${NODE}" --workers "${WORKER_ADDR}"
fi

LOG_FILE="${EVIDENCE_DIR:-/tmp}/kvtest_smoke.log"
mkdir -p "$(dirname "${LOG_FILE}")"

ssh_remote "${REMOTE}" bash -s -- "${WORKER_ADDR}" "${DS_REMOTE}" "${KVTEST_REMOTE}" "${SDK_DIR}" <<'REMOTE' | tee "${LOG_FILE}"
set -euo pipefail
export PATH="${HOME}/.local/bin:${PATH}"
WORKER_ADDR="$1"
DS_REMOTE="$2"
KVTEST_REMOTE="$3"
SDK_ARG="${4:-}"

export JD_HOST_IP="${JD_HOST_IP:-127.0.0.1}"

resolve_sdk() {
  local sdk="$1"
  if [[ -n "${sdk}" && -d "${sdk}/include" && -d "${sdk}/lib" ]]; then
    echo "${sdk}"
    return
  fi
  for candidate in \
    "${DS_REMOTE}/output/cpp" \
    "${DS_REMOTE}/build/output/cpp" \
    "${DS_REMOTE}/build/output/datasystem/sdk/cpp" \
    "${DS_REMOTE}/output/datasystem/sdk/cpp"; do
    if [[ -d "${candidate}/include" && -d "${candidate}/lib" ]]; then
      echo "${candidate}"
      return
    fi
  done
  echo "[kvtest-smoke] SDK not found; running cmake build (may take several minutes)..." >&2
  cd "${DS_REMOTE}"
  bash build.sh -t build -b cmake -B build -j "$(nproc)" 2>&1 | tail -20
  for candidate in \
    "${DS_REMOTE}/output/cpp" \
    "${DS_REMOTE}/build/output/datasystem/sdk/cpp"; do
    if [[ -d "${candidate}/include" && -d "${candidate}/lib" ]]; then
      echo "${candidate}"
      return
    fi
  done
  echo "[kvtest-smoke] ERROR: cannot resolve SDK after build" >&2
  exit 1
}

SDK_DIR="$(resolve_sdk "${SDK_ARG}")"
echo "[kvtest-smoke] SDK: ${SDK_DIR}"

cd "${KVTEST_REMOTE}"
if [[ ! -x "${KVTEST_REMOTE}/output/kvtest" ]]; then
  ./build.sh -s "${SDK_DIR}" -j "$(nproc)" 2>&1 | tail -20
else
  echo "[kvtest-smoke] Reusing existing ${KVTEST_REMOTE}/output/kvtest"
fi

OUT="${KVTEST_REMOTE}/output"
mkdir -p "${OUT}/config"
cat > "${OUT}/config/smoke_set_local.json" <<EOF
{
  "mode": "benchmark",
  "instance_id": 0,
  "listen_port": 9000,
  "etcd_address": "127.0.0.1:2379",
  "test_mode": "set_local",
  "worker_memory_mb": 4096,
  "num_threads": 8,
  "total_rounds": 3,
  "data_sizes": ["1MB"],
  "set_api": "string_view",
  "cleanup_method": "del"
}
EOF

cd "${OUT}"
echo "=== kvtest set_local smoke ==="
START=$(date +%s)
LD_LIBRARY_PATH=./lib:${LD_LIBRARY_PATH:-} ./kvtest config/smoke_set_local.json
RC=$?
ELAPSED=$(( $(date +%s) - START ))
echo "[kvtest-smoke] elapsed_sec=${ELAPSED} exit_code=${RC}"

if [[ -f benchmark_phases.csv ]]; then
  echo "=== benchmark_phases.csv (head) ==="
  head -5 benchmark_phases.csv
fi

curl -sf http://127.0.0.1:9000/stats 2>/dev/null | head -20 || true
exit "${RC}"
REMOTE
SMOKE_RC=${PIPESTATUS[0]}

if [[ -n "${EVIDENCE_DIR}" ]]; then
  mkdir -p "${EVIDENCE_DIR}"
  scp -q "${REMOTE}:${KVTEST_REMOTE}/output/benchmark_phases.csv" "${EVIDENCE_DIR}/" 2>/dev/null || true
  python3 - <<PY
import csv, json, pathlib
ev = pathlib.Path("${EVIDENCE_DIR}")
rows = []
csv_path = ev / "benchmark_phases.csv"
if csv_path.exists():
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
summary = {
    "tool": "kvtest",
    "mode": "set_local",
    "worker": "${WORKER_ADDR}",
    "status": "PASS" if ${SMOKE_RC} == 0 and rows else "FAIL",
    "exit_code": ${SMOKE_RC},
    "phases": rows[:6],
    "log": "${LOG_FILE}",
}
(ev / "bench_results.json").write_text(json.dumps(summary, indent=2) + "\n")
PY
fi

if [[ "${SMOKE_RC}" -ne 0 ]]; then
  log_error "kvtest smoke FAILED (${NODE}). Log: ${LOG_FILE}"
  exit "${SMOKE_RC}"
fi

log_info "kvtest smoke OK (${NODE}). Log: ${LOG_FILE}"
