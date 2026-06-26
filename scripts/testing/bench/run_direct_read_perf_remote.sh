#!/usr/bin/env bash
# Cross-node ObjectClient Get latency A/B: gateway vs direct read (ST benchmark).
#
# Emits avg / p99 / p9999 (microseconds) via CrossNodeGetLatencyBenchmark ST.
# Default: 256KB payload, 50 warmup, 1000 measured Gets per path.
#
# Usage:
#   bash scripts/testing/bench/run_direct_read_perf_remote.sh \
#     --worktree client-direct-read-flow \
#     --branch feature/client-direct-read-flow \
#     --sync-local
#
#   DS_DIRECT_READ_PERF_ITERS=2000 DS_DIRECT_READ_PERF_SIZE=1048576 \
#     bash scripts/testing/bench/run_direct_read_perf_remote.sh --sync-local
#
# Environment (forwarded to ctest):
#   DS_DIRECT_READ_PERF=1            (always set by this script)
#   DS_DIRECT_READ_PERF_MODE          local | remote | all (default all)
#   DS_DIRECT_READ_PERF_ITERS         default 1000
#   DS_DIRECT_READ_PERF_WARMUP        default 50
#   DS_DIRECT_READ_PERF_SIZE          default 262144 (256KB)
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_DIR="$(cd "${BENCH_DIR}/../verify" && pwd)"
LIB_DIR="$(cd "${BENCH_DIR}/../../lib" && pwd)"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/remote_defaults.sh"
. "${LIB_DIR}/timing.sh"

NODE="$(node_role_default verify_st)"
WORKTREE_SLUG=""
WORKTREE_BRANCH=""
EVIDENCE_DIR=""
SYNC_LOCAL=0
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worktree) WORKTREE_SLUG="$2"; shift 2 ;;
    --branch) WORKTREE_BRANCH="$2"; shift 2 ;;
    --node) NODE="$2"; shift 2 ;;
    --evidence-dir) EVIDENCE_DIR="$2"; shift 2 ;;
    --sync-local) SYNC_LOCAL=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help)
      sed -n '1,22p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${WORKTREE_SLUG}" ]] || WORKTREE_SLUG="client-direct-read-flow"
WORKTREE_BRANCH="${WORKTREE_BRANCH:-feature/${WORKTREE_SLUG}}"
EVIDENCE_DIR="${EVIDENCE_DIR:-${BENCH_DIR}/../../../results/harness/direct-read-perf-$(date -u +%Y%m%d_%H%M%S)}"
mkdir -p "${EVIDENCE_DIR}"

PERF_ITERS="${DS_DIRECT_READ_PERF_ITERS:-1000}"
PERF_WARMUP="${DS_DIRECT_READ_PERF_WARMUP:-50}"
PERF_SIZE="${DS_DIRECT_READ_PERF_SIZE:-262144}"
PERF_MODE="${DS_DIRECT_READ_PERF_MODE:-all}"

VERIFY_ARGS=(
  --worktree "${WORKTREE_SLUG}"
  --branch "${WORKTREE_BRANCH}"
  --node "${NODE}"
  --phase st
  --ctest-regex 'CrossNode.*LatencyBenchmark'
)

[[ "${SYNC_LOCAL}" -eq 1 ]] && VERIFY_ARGS+=(--sync-local)
[[ "${SKIP_BUILD}" -eq 1 ]] && VERIFY_ARGS+=(--skip-build)

banner "Direct read perf A/B on ${NODE}"
log_info "Evidence: ${EVIDENCE_DIR}"
log_info "iters=${PERF_ITERS} warmup=${PERF_WARMUP} size=${PERF_SIZE} mode=${PERF_MODE}"

LOG_FILE="${EVIDENCE_DIR}/direct_read_perf.log"
export DS_DIRECT_READ_PERF=1
export DS_DIRECT_READ_PERF_ITERS="${PERF_ITERS}"
export DS_DIRECT_READ_PERF_WARMUP="${PERF_WARMUP}"
export DS_DIRECT_READ_PERF_SIZE="${PERF_SIZE}"
export DS_DIRECT_READ_PERF_MODE="${PERF_MODE}"
export ST_CTEST_REGEX='CrossNode.*LatencyBenchmark'

set +e
bash "${VERIFY_DIR}/run_worktree_verify_remote.sh" "${VERIFY_ARGS[@]}" 2>&1 | tee "${LOG_FILE}"
RC=${PIPESTATUS[0]}
set -e

python3 - <<PY
import json, pathlib, re, sys

log_path = pathlib.Path("${LOG_FILE}")
text = log_path.read_text(encoding="utf-8", errors="replace")
rows = []
for line in text.splitlines():
    m = re.search(r"DIRECT_READ_PERF_JSON=(\{.*\})", line)
    if m:
        rows.append(json.loads(m.group(1)))

def fmt_us(v):
    return round(v / 1000.0, 3)

def compare_pair(rows, gw_name, dr_name):
    gw = next((r for r in rows if r["scenario"] == gw_name), None)
    dr = next((r for r in rows if r["scenario"] == dr_name), None)
    if gw is None or dr is None:
        return None
    out = {}
    for key in ("avg_us", "p99_us", "p9999_us"):
        g = gw[key]
        d = dr[key]
        out[key] = {
            "gateway_ms": fmt_us(g),
            "direct_ms": fmt_us(d),
            "delta_ms": round(fmt_us(d - g), 3),
            "delta_pct": round((d - g) / g * 100.0, 2) if g > 0 else None,
        }
    return out

summary = {
    "tool": "client_direct_read_st",
    "test": "CrossNode*LatencyBenchmark*",
    "status": "PASS" if ${RC} == 0 and len(rows) >= 2 else "FAIL",
    "exit_code": ${RC},
    "config": {
        "iters": int("${PERF_ITERS}"),
        "warmup": int("${PERF_WARMUP}"),
        "payload_bytes": int("${PERF_SIZE}"),
        "mode": "${PERF_MODE}",
    },
    "paths": rows,
    "comparison_ms": {},
    "primary_comparison": "remote_only",
    "log": str(log_path),
}
local_cmp = compare_pair(rows, "cross_node_local_gateway", "cross_node_local_direct_forced")
cold_256_cmp = compare_pair(rows, "cross_node_cold_256k_gateway", "cross_node_cold_256k_direct_forced")
cold_8m_cmp = compare_pair(rows, "cross_node_cold_8m_gateway", "cross_node_cold_8m_direct_forced")
remote_cmp = compare_pair(rows, "remote_only_gateway", "remote_only_direct")
if local_cmp is not None:
    summary["comparison_ms"]["local_forced"] = local_cmp
if cold_256_cmp is not None:
    summary["comparison_ms"]["cold_cross_node_256k"] = cold_256_cmp
if cold_8m_cmp is not None:
    summary["comparison_ms"]["cold_cross_node_8m"] = cold_8m_cmp
if remote_cmp is not None:
    summary["comparison_ms"]["remote_only"] = remote_cmp

out = pathlib.Path("${EVIDENCE_DIR}/direct_read_perf.json")
out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

md = pathlib.Path("${EVIDENCE_DIR}/direct_read_perf.md")
lines = [
    "# Direct read cross-node Get latency",
    "",
    f"- status: **{summary['status']}**",
    f"- iters: {summary['config']['iters']} (warmup {summary['config']['warmup']})",
    f"- payload: {summary['config']['payload_bytes']} bytes",
    f"- mode: {summary['config']['mode']}",
    "",
    "| scenario | avg (ms) | p99 (ms) | p99.99 (ms) |",
    "| --- | ---: | ---: | ---: |",
]
for row in rows:
    lines.append(
        f"| {row['scenario']} | {fmt_us(row['avg_us'])} | {fmt_us(row['p99_us'])} | {fmt_us(row['p9999_us'])} |"
    )
if summary["comparison_ms"]:
    lines.extend(["", "## comparisons (direct − gateway)", ""])
    for label, comp in summary["comparison_ms"].items():
        lines.append(f"### {label}")
        for key, values in comp.items():
            metric = key.replace("_us", "")
            lines.append(
                f"- **{metric}**: {values['delta_ms']} ms ({values['delta_pct']}%) "
                f"[gateway {values['gateway_ms']} ms → direct {values['direct_ms']} ms]"
            )
        lines.append("")
md.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(json.dumps(summary, indent=2))
if summary["status"] != "PASS":
    sys.exit(1)
PY
PARSE_RC=$?

if [[ "${RC}" -ne 0 || "${PARSE_RC}" -ne 0 ]]; then
  log_error "Direct read perf FAILED. Log: ${LOG_FILE}"
  exit 1
fi

log_info "Direct read perf OK. Results: ${EVIDENCE_DIR}/direct_read_perf.json"
