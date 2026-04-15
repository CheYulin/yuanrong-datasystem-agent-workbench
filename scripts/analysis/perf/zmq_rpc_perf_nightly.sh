#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../development/lib/datasystem_root.sh
. "${SCRIPT_DIR}/../../development/lib/datasystem_root.sh"
VIBE_CODING_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

REMOTE="xqyun-16c16g"
REMOTE_BASE="~/workspace/git-repos"
REMOTE_DS=""
REMOTE_VIBE=""
BUILD_DIR_REL="build"
BUILD_JOBS=16
CTEST_JOBS=1
CTEST_TIMEOUT=600
ITERATIONS=7
SKIP_SYNC=0
SKIP_BUILD=0
BASELINE_STATS=""
REGRESSION_THRESHOLD_PCT="10"
MAX_FAIL_RATIO="0"
RESULT_ROOT="${VIBE_CODING_ROOT}/results"
RUN_ID="zmq_rpc_perf_$(date +%Y%m%d_%H%M%S)"
REPORT_TEMPLATE="${SCRIPT_DIR}/zmq_rpc_perf_report.md"

declare -a SCENARIOS
SCENARIOS+=("kv_single::KVCacheClientTest\\.TestSingleKey")
SCENARIOS+=("kv_mset_mget::KVCacheClientTest\\.TestMsetAndMGet")
SCENARIOS+=("kv_mset_suite::KVClientMSetTest\\..*")
CUSTOM_SCENARIO_MODE=0

usage() {
  cat <<'EOF'
Usage:
  bash scripts/analysis/perf/zmq_rpc_perf_nightly.sh [options]

Options:
  --remote <user@host>                 Remote SSH target (default: xqyun-16c16g)
  --remote-base <path>                 Remote workspace base (default: ~/workspace/git-repos)
  --remote-ds <path>                   Remote DS path (default: <remote-base>/yuanrong-datasystem)
  --remote-vibe <path>                 Remote vibe path (default: <remote-base>/vibe-coding-files)
  --build-dir-rel <path>               Build directory under remote DS (default: build)
  --jobs <N>                           Build parallel jobs (default: 16)
  --ctest-jobs <N>                     ctest parallel jobs for scenario run (default: 1)
  --ctest-timeout <seconds>            ctest timeout per iteration (default: 600)
  --iterations <N>                     Iterations per scenario (default: 7)
  --scenario <name::ctest_regex>       Add or replace scenarios (repeatable)
  --skip-sync                          Skip rsync phase when rebuilding
  --skip-build                         Reuse existing build, skip perf rebuild
  --baseline-stats <path>              Previous scenario_stats.csv for regression comparison
  --regression-threshold-pct <value>   Max allowed P95 regression percent (default: 10)
  --max-fail-ratio <value>             Max allowed scenario fail ratio (default: 0)
  --result-root <path>                 Local result root (default: vibe/results)
  --run-id <name>                      Override run id directory name
  --report-template <path>             Markdown template path
  -h, --help                           Show this help

Examples:
  bash scripts/analysis/perf/zmq_rpc_perf_nightly.sh \
    --remote xqyun-16c16g --iterations 9

  bash scripts/analysis/perf/zmq_rpc_perf_nightly.sh \
    --skip-build \
    --baseline-stats results/zmq_rpc_perf_20260410_230000/scenario_stats.csv
EOF
}

abspath_existing() {
  local path="$1"
  if [[ -e "${path}" ]]; then
    echo "$(cd "$(dirname "${path}")" && pwd)/$(basename "${path}")"
  else
    echo "Path does not exist: ${path}" >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote)
      REMOTE="$2"
      shift 2
      ;;
    --remote-base)
      REMOTE_BASE="$2"
      shift 2
      ;;
    --remote-ds)
      REMOTE_DS="$2"
      shift 2
      ;;
    --remote-vibe)
      REMOTE_VIBE="$2"
      shift 2
      ;;
    --build-dir-rel)
      BUILD_DIR_REL="$2"
      shift 2
      ;;
    --jobs)
      BUILD_JOBS="$2"
      shift 2
      ;;
    --ctest-jobs)
      CTEST_JOBS="$2"
      shift 2
      ;;
    --ctest-timeout)
      CTEST_TIMEOUT="$2"
      shift 2
      ;;
    --iterations)
      ITERATIONS="$2"
      shift 2
      ;;
    --scenario)
      if [[ "${CUSTOM_SCENARIO_MODE}" -eq 0 ]]; then
        SCENARIOS=()
        CUSTOM_SCENARIO_MODE=1
      fi
      SCENARIOS+=("$2")
      shift 2
      ;;
    --skip-sync)
      SKIP_SYNC=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --baseline-stats)
      BASELINE_STATS="$(abspath_existing "$2")"
      shift 2
      ;;
    --regression-threshold-pct)
      REGRESSION_THRESHOLD_PCT="$2"
      shift 2
      ;;
    --max-fail-ratio)
      MAX_FAIL_RATIO="$2"
      shift 2
      ;;
    --result-root)
      RESULT_ROOT="$2"
      shift 2
      ;;
    --run-id)
      RUN_ID="$2"
      shift 2
      ;;
    --report-template)
      REPORT_TEMPLATE="$(abspath_existing "$2")"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${ITERATIONS}" =~ ^[0-9]+$ ]] || [[ "${ITERATIONS}" -le 0 ]]; then
  echo "--iterations must be positive integer, got: ${ITERATIONS}" >&2
  exit 2
fi
if ! [[ "${BUILD_JOBS}" =~ ^[0-9]+$ ]] || [[ "${BUILD_JOBS}" -le 0 ]]; then
  echo "--jobs must be positive integer, got: ${BUILD_JOBS}" >&2
  exit 2
fi
if ! [[ "${CTEST_JOBS}" =~ ^[0-9]+$ ]] || [[ "${CTEST_JOBS}" -le 0 ]]; then
  echo "--ctest-jobs must be positive integer, got: ${CTEST_JOBS}" >&2
  exit 2
fi
if ! [[ "${CTEST_TIMEOUT}" =~ ^[0-9]+$ ]] || [[ "${CTEST_TIMEOUT}" -le 0 ]]; then
  echo "--ctest-timeout must be positive integer, got: ${CTEST_TIMEOUT}" >&2
  exit 2
fi

if [[ -z "${REMOTE_DS}" ]]; then
  REMOTE_DS="${REMOTE_BASE%/}/yuanrong-datasystem"
fi
if [[ -z "${REMOTE_VIBE}" ]]; then
  REMOTE_VIBE="${REMOTE_BASE%/}/vibe-coding-files"
fi

mkdir -p "${RESULT_ROOT}"
LOCAL_RUN_DIR="$(cd "${RESULT_ROOT}" && pwd)/${RUN_ID}"
mkdir -p "${LOCAL_RUN_DIR}"

SCENARIO_FILE_LOCAL="${LOCAL_RUN_DIR}/scenarios.tsv"
{
  for item in "${SCENARIOS[@]}"; do
    name="${item%%::*}"
    regex="${item#*::}"
    if [[ -z "${name}" || -z "${regex}" || "${name}" == "${regex}" ]]; then
      echo "Invalid --scenario format, expected name::regex, got: ${item}" >&2
      exit 2
    fi
    printf '%s\t%s\n' "${name}" "${regex}"
  done
} > "${SCENARIO_FILE_LOCAL}"

echo "== zmq rpc nightly config =="
echo "REMOTE=${REMOTE}"
echo "REMOTE_DS=${REMOTE_DS}"
echo "REMOTE_VIBE=${REMOTE_VIBE}"
echo "BUILD_DIR_REL=${BUILD_DIR_REL}"
echo "BUILD_JOBS=${BUILD_JOBS}"
echo "CTEST_JOBS=${CTEST_JOBS}"
echo "CTEST_TIMEOUT=${CTEST_TIMEOUT}"
echo "ITERATIONS=${ITERATIONS}"
echo "RUN_ID=${RUN_ID}"
echo "LOCAL_RUN_DIR=${LOCAL_RUN_DIR}"
echo "BASELINE_STATS=${BASELINE_STATS:-none}"
echo "REPORT_TEMPLATE=${REPORT_TEMPLATE}"
echo

if [[ "${SKIP_BUILD}" -eq 0 ]]; then
  BUILD_CMD=(
    bash "${VIBE_CODING_ROOT}/scripts/build/remote_build_run_datasystem.sh"
    --remote "${REMOTE}"
    --remote-base "${REMOTE_BASE}"
    --remote-ds "${REMOTE_DS}"
    --remote-vibe "${REMOTE_VIBE}"
    --build-dir-rel "${BUILD_DIR_REL}"
    --jobs "${BUILD_JOBS}"
    --ctest-jobs "${CTEST_JOBS}"
    --hetero off
    --perf on
    --skip-ctest
    --skip-validate
    --skip-run-example
    --skip-wheel-install
  )
  if [[ "${SKIP_SYNC}" -eq 1 ]]; then
    BUILD_CMD+=(--skip-sync)
  fi
  echo "[step] Rebuild remote with ENABLE_PERF=on"
  "${BUILD_CMD[@]}"
else
  echo "[step] Skip build stage (--skip-build)"
fi

REMOTE_HOME="$(ssh "${REMOTE}" 'printf %s "$HOME"')"
resolve_remote_path() {
  local p="$1"
  p="${p/#\~\//${REMOTE_HOME}/}"
  p="${p/#\~/${REMOTE_HOME}}"
  p="${p/#\$HOME\//${REMOTE_HOME}/}"
  p="${p/#\$HOME/${REMOTE_HOME}}"
  printf '%s' "${p}"
}
REMOTE_DS_RESOLVED="$(resolve_remote_path "${REMOTE_DS}")"
REMOTE_BUILD_DIR="${REMOTE_DS_RESOLVED%/}/${BUILD_DIR_REL}"
REMOTE_RUN_DIR="/tmp/${RUN_ID}"

echo "[step] Upload scenario definitions to remote"
ssh "${REMOTE}" "mkdir -p '${REMOTE_RUN_DIR}'"
rsync -az "${SCENARIO_FILE_LOCAL}" "${REMOTE}:${REMOTE_RUN_DIR}/scenarios.tsv"

echo "[step] Run scenarios on remote and extract perf rows"
ssh "${REMOTE}" \
  "REMOTE_DS='${REMOTE_DS_RESOLVED}' REMOTE_BUILD_DIR='${REMOTE_BUILD_DIR}' REMOTE_RUN_DIR='${REMOTE_RUN_DIR}' ITERATIONS='${ITERATIONS}' CTEST_JOBS='${CTEST_JOBS}' CTEST_TIMEOUT='${CTEST_TIMEOUT}' bash -s" <<'EOF'
set -euo pipefail

mkdir -p "${REMOTE_RUN_DIR}/raw_logs"
SCENARIO_FILE="${REMOTE_RUN_DIR}/scenarios.tsv"
SAMPLE_CSV="${REMOTE_RUN_DIR}/scenario_samples.csv"
PERF_CSV="${REMOTE_RUN_DIR}/perf_points.csv"
META_FILE="${REMOTE_RUN_DIR}/META.txt"

{
  echo "date=$(date -Iseconds)"
  echo "hostname=$(hostname)"
  echo "remote_ds=${REMOTE_DS}"
  echo "remote_build_dir=${REMOTE_BUILD_DIR}"
  echo "iterations=${ITERATIONS}"
  echo "ctest_jobs=${CTEST_JOBS}"
  echo "ctest_timeout=${CTEST_TIMEOUT}"
  echo "git_ds=$(git -C "${REMOTE_DS}" rev-parse --short HEAD 2>/dev/null || echo no-git)"
} > "${META_FILE}"

echo "scenario,iteration,exit_code,duration_ms,log_file" > "${SAMPLE_CSV}"
echo "scenario,iteration,perf_key,count,total_ns,avg_ns,min_ns,max_ns,max_frequency,log_file" > "${PERF_CSV}"

while IFS=$'\t' read -r scenario_name scenario_regex; do
  [[ -z "${scenario_name}" ]] && continue
  for ((i=1; i<=ITERATIONS; i++)); do
    iter_log="${REMOTE_RUN_DIR}/raw_logs/${scenario_name}_iter_${i}.log"
    started="$(date +%s%3N)"
    set +e
    CTEST_OUTPUT_ON_FAILURE=1 ctest --test-dir "${REMOTE_BUILD_DIR}" --output-on-failure \
      --parallel "${CTEST_JOBS}" --timeout "${CTEST_TIMEOUT}" -R "${scenario_regex}" > "${iter_log}" 2>&1
    rc="$?"
    set -e
    ended="$(date +%s%3N)"
    elapsed_ms="$((ended - started))"
    printf '%s,%s,%s,%s,%s\n' "${scenario_name}" "${i}" "${rc}" "${elapsed_ms}" "$(basename "${iter_log}")" >> "${SAMPLE_CSV}"

    python3 - "${iter_log}" "${scenario_name}" "${i}" "${PERF_CSV}" <<'PY'
import json
import re
import sys

log_file = sys.argv[1]
scenario = sys.argv[2]
iteration = sys.argv[3]
perf_csv = sys.argv[4]

pattern = re.compile(r'(ZMQ_[A-Z0-9_]+):\s*(\{.*\})')
rows = []
with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        m = pattern.search(line)
        if not m:
            continue
        key = m.group(1)
        payload = m.group(2).strip()
        try:
            info = json.loads(payload)
        except json.JSONDecodeError:
            continue
        rows.append(
            (
                scenario,
                iteration,
                key,
                int(info.get("count", 0)),
                int(info.get("totalTime", 0)),
                int(info.get("avgTime", 0)),
                int(info.get("minTime", 0)),
                int(info.get("maxTime", 0)),
                int(info.get("maxFrequency", 0)),
                log_file.split("/")[-1],
            )
        )

if rows:
    with open(perf_csv, "a", encoding="utf-8") as out:
        for row in rows:
            out.write(",".join(str(x) for x in row) + "\n")
PY
  done
done < "${SCENARIO_FILE}"
EOF

echo "[step] Download remote run artifacts"
rsync -az "${REMOTE}:${REMOTE_RUN_DIR}/" "${LOCAL_RUN_DIR}/"

LOCAL_SAMPLE_CSV="${LOCAL_RUN_DIR}/scenario_samples.csv"
LOCAL_PERF_CSV="${LOCAL_RUN_DIR}/perf_points.csv"
SCENARIO_STATS_CSV="${LOCAL_RUN_DIR}/scenario_stats.csv"
PERF_STATS_CSV="${LOCAL_RUN_DIR}/perf_key_stats.csv"
REPORT_MD="${LOCAL_RUN_DIR}/zmq_rpc_perf_report.filled.md"

echo "[step] Aggregate CSV and render markdown report"
python3 - \
  "${LOCAL_SAMPLE_CSV}" \
  "${LOCAL_PERF_CSV}" \
  "${SCENARIO_STATS_CSV}" \
  "${PERF_STATS_CSV}" \
  "${BASELINE_STATS}" \
  "${REPORT_TEMPLATE}" \
  "${REPORT_MD}" \
  "${RUN_ID}" \
  "${REMOTE}" \
  "${REMOTE_BUILD_DIR}" \
  "${ITERATIONS}" \
  "${REGRESSION_THRESHOLD_PCT}" \
  "${MAX_FAIL_RATIO}" \
  "${LOCAL_RUN_DIR}" <<'PY'
import csv
import datetime as dt
import math
import os
import sys
from collections import defaultdict

(
    sample_csv,
    perf_csv,
    scenario_stats_csv,
    perf_stats_csv,
    baseline_stats_csv,
    template_path,
    report_path,
    run_id,
    remote,
    build_dir,
    iterations,
    regression_threshold_pct,
    max_fail_ratio,
    run_dir,
) = sys.argv[1:]

regression_threshold_pct = float(regression_threshold_pct)
max_fail_ratio = float(max_fail_ratio)

def percentile(values, p):
    if not values:
        return 0.0
    vals = sorted(float(v) for v in values)
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return vals[int(k)]
    return vals[f] * (c - k) + vals[c] * (k - f)

scenario_rows = []
with open(sample_csv, "r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        scenario_rows.append(row)

scenario_values = defaultdict(list)
scenario_failures = defaultdict(int)
scenario_total = defaultdict(int)
for row in scenario_rows:
    name = row["scenario"]
    duration_ms = float(row["duration_ms"])
    rc = int(row["exit_code"])
    scenario_values[name].append(duration_ms)
    scenario_total[name] += 1
    if rc != 0:
        scenario_failures[name] += 1

baseline_p95 = {}
if baseline_stats_csv and os.path.isfile(baseline_stats_csv):
    with open(baseline_stats_csv, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                baseline_p95[row["scenario"]] = float(row["p95_ms"])
            except Exception:
                continue

scenario_stats_rows = []
for scenario in sorted(scenario_values.keys()):
    vals = scenario_values[scenario]
    total = scenario_total[scenario]
    fail_count = scenario_failures[scenario]
    fail_ratio = (fail_count / total) if total else 0.0
    p50 = percentile(vals, 50)
    p95 = percentile(vals, 95)
    p99 = percentile(vals, 99)
    base = baseline_p95.get(scenario)
    if base and base > 0:
        regression_pct = ((p95 - base) / base) * 100.0
        regression_text = f"{regression_pct:.2f}%"
        regression_ok = regression_pct <= regression_threshold_pct
    elif baseline_stats_csv:
        regression_pct = None
        regression_text = "N/A (missing scenario in baseline)"
        regression_ok = False
    else:
        regression_pct = None
        regression_text = "N/A (no baseline)"
        regression_ok = True

    stability_ok = fail_ratio <= max_fail_ratio
    status = "PASS" if (stability_ok and regression_ok) else "FAIL"

    scenario_stats_rows.append(
        {
            "scenario": scenario,
            "samples": total,
            "pass_count": total - fail_count,
            "fail_count": fail_count,
            "fail_ratio": fail_ratio,
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "regression_pct_vs_baseline_p95": regression_pct if regression_pct is not None else "",
            "regression_text": regression_text,
            "status": status,
        }
    )

with open(scenario_stats_csv, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "scenario",
            "samples",
            "pass_count",
            "fail_count",
            "fail_ratio",
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "regression_pct_vs_baseline_p95",
            "status",
        ],
    )
    writer.writeheader()
    for row in scenario_stats_rows:
        out = row.copy()
        out["fail_ratio"] = f'{row["fail_ratio"]:.4f}'
        out["p50_ms"] = f'{row["p50_ms"]:.3f}'
        out["p95_ms"] = f'{row["p95_ms"]:.3f}'
        out["p99_ms"] = f'{row["p99_ms"]:.3f}'
        if out["regression_pct_vs_baseline_p95"] != "":
            out["regression_pct_vs_baseline_p95"] = f'{row["regression_pct_vs_baseline_p95"]:.2f}'
        writer.writerow(out)

perf_rows = []
if os.path.isfile(perf_csv):
    with open(perf_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                avg_us = float(row["avg_ns"]) / 1000.0
            except Exception:
                continue
            perf_rows.append((row["scenario"], row["perf_key"], avg_us))

perf_group = defaultdict(list)
for scenario, perf_key, avg_us in perf_rows:
    perf_group[(scenario, perf_key)].append(avg_us)

perf_stats_rows = []
for (scenario, perf_key), values in sorted(perf_group.items()):
    perf_stats_rows.append(
        {
            "scenario": scenario,
            "perf_key": perf_key,
            "samples": len(values),
            "p50_avg_us": percentile(values, 50),
            "p95_avg_us": percentile(values, 95),
            "p99_avg_us": percentile(values, 99),
        }
    )

with open(perf_stats_csv, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["scenario", "perf_key", "samples", "p50_avg_us", "p95_avg_us", "p99_avg_us"],
    )
    writer.writeheader()
    for row in perf_stats_rows:
        out = row.copy()
        out["p50_avg_us"] = f'{row["p50_avg_us"]:.3f}'
        out["p95_avg_us"] = f'{row["p95_avg_us"]:.3f}'
        out["p99_avg_us"] = f'{row["p99_avg_us"]:.3f}'
        writer.writerow(out)

if not scenario_stats_rows:
    overall_status = "FAIL"
    overall_note = "No scenario samples were collected. Check ctest logs and remote environment."
else:
    failed = [r for r in scenario_stats_rows if r["status"] != "PASS"]
    overall_status = "PASS" if not failed else "FAIL"
    if failed:
        bad_names = ", ".join(r["scenario"] for r in failed)
        overall_note = f"Regression gate failed for scenario(s): {bad_names}."
    else:
        overall_note = "All scenarios passed latency/stability gates."

scenario_table = []
for row in scenario_stats_rows:
    scenario_table.append(
        f'| {row["scenario"]} | {row["samples"]} | {row["fail_ratio"]:.2%} | '
        f'{row["p50_ms"]:.3f} | {row["p95_ms"]:.3f} | {row["p99_ms"]:.3f} | '
        f'{row["regression_text"]} | {row["status"]} |'
    )
if not scenario_table:
    scenario_table.append("| (no data) | 0 | 0.00% | 0 | 0 | 0 | N/A | FAIL |")

perf_table = []
for row in perf_stats_rows:
    perf_table.append(
        f'| {row["scenario"]} | {row["perf_key"]} | {row["samples"]} | '
        f'{row["p50_avg_us"]:.3f} | {row["p95_avg_us"]:.3f} | {row["p99_avg_us"]:.3f} |'
    )
if not perf_table:
    perf_table.append("| (no data) | (no zmq perf rows parsed) | 0 | 0 | 0 | 0 |")

artifacts = "\n".join(
    [
        f"- `{os.path.join(run_dir, 'META.txt')}`",
        f"- `{sample_csv}`",
        f"- `{scenario_stats_csv}`",
        f"- `{perf_csv}`",
        f"- `{perf_stats_csv}`",
        f"- `{os.path.join(run_dir, 'raw_logs')}`",
    ]
)

with open(template_path, "r", encoding="utf-8") as f:
    template = f.read()

filled = template
replacements = {
    "{{RUN_ID}}": run_id,
    "{{GENERATED_AT}}": dt.datetime.now().isoformat(timespec="seconds"),
    "{{REMOTE}}": remote,
    "{{BUILD_DIR}}": build_dir,
    "{{ITERATIONS}}": str(iterations),
    "{{BASELINE_PATH}}": baseline_stats_csv if baseline_stats_csv else "none",
    "{{REGRESSION_THRESHOLD_PCT}}": f"{regression_threshold_pct:g}",
    "{{MAX_FAIL_RATIO}}": f"{max_fail_ratio:g}",
    "{{OVERALL_STATUS}}": overall_status,
    "{{OVERALL_NOTE}}": overall_note,
    "{{SCENARIO_TABLE}}": "\n".join(scenario_table),
    "{{PERF_TABLE}}": "\n".join(perf_table),
    "{{ARTIFACTS}}": artifacts,
}
for key, value in replacements.items():
    filled = filled.replace(key, value)

with open(report_path, "w", encoding="utf-8") as f:
    f.write(filled)
PY

cat > "${LOCAL_RUN_DIR}/HOW_TO_USE_NEXT_TIME.md" <<EOF
# How To Reuse This Baseline

Use this run's scenario stats as baseline:

\`\`\`bash
bash scripts/analysis/perf/zmq_rpc_perf_nightly.sh \\
  --remote ${REMOTE} \\
  --baseline-stats "${SCENARIO_STATS_CSV}"
\`\`\`
EOF

echo
echo "ZMQ RPC nightly finished."
echo "Run dir: ${LOCAL_RUN_DIR}"
echo "Report:  ${REPORT_MD}"
echo "Stats:   ${SCENARIO_STATS_CSV}"
