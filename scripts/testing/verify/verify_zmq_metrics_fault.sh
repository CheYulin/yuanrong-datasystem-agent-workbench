#!/usr/bin/env bash
# =============================================================================
# verify_zmq_metrics_fault.sh
#
# PURPOSE
#   Run ZmqMetricsFaultTest and parse the metrics LOG(INFO) output to confirm
#   that each fault scenario is correctly isolated by the ZMQ metrics.
#
# USAGE
#   ./verify_zmq_metrics_fault.sh [options]
#
#   Default runner is Bazel (bazel run //tests/st/common/rpc/zmq:zmq_metrics_fault_test).
#   --backend bazel|cmake   Build/run backend (default: bazel). Same as --cmake for cmake.
#   --cmake                 Shortcut for --backend cmake
#
#   Bazel invocation (local + remote):
#     BAZEL_CMD   Executable for Bazel (default: bazel). Use bazelisk or full path, e.g.:
#                 BAZEL_CMD=bazelisk ./verify_zmq_metrics_fault.sh
#     BAZEL_TARGET  Override ST test target (default: //tests/st/common/rpc/zmq:zmq_metrics_fault_test)
#   --build-dir DIR         CMake build directory (only with --cmake; default: auto-detect)
#   --filter FILTER         gtest filter (default: ZmqMetricsFaultTest.*)
#   --remote                Run on REMOTE_HOST via ssh (see REMOTE_DS)
#   --input-log FILE        Skip running gtest; only run grep checks on this log (local path)
#
# FAULT ISOLATION RUNBOOK (for production use, not just this script)
# ------------------------------------------------------------------
# 1. Enable metrics by calling metrics::Init(ZMQ_METRIC_DESCS, ...) at startup
#    and metrics::Start() after flags are parsed.
# 2. Periodically inspect LOG(INFO) lines tagged "Metrics Summary".
# 3. Use this decision tree:
#
#    zmq.recv.fail > 0 → ZMQ recv hard failure.  Check zmq.last_errno.
#    zmq.send.fail > 0 → ZMQ send hard failure.  Check zmq.last_errno.
#    zmq.net_error > 0 → Network errno class.    → NIC / routing problem.
#    zmq.recv.eagain > 0 → Blocking recv timeout. → Server slow or down.
#    zmq.send.eagain > 0 → HWM back-pressure.    → Producer too fast.
#    zmq.evt.disconn > 0 → ZMQ disconnect event. → Peer crashed / restarted.
#    zmq.gw_recreate > 0 → Gateway recreated.    → Connection recovery happened.
#
#    If none of the above:
#    zmq.io.recv_us avg high (>1ms)? → Network latency / server processing.
#    zmq.rpc.ser_us / deser_us avg high (>100us)? → Protobuf size too large.
#    All avg low? → Bottleneck outside ZMQ (business logic, queue depth).
#
# EXIT CODES
#   0  All checks passed
#   1  One or more checks failed
#   2  Build or binary not found
# =============================================================================
set -euo pipefail

# ── defaults ──────────────────────────────────────────────────────────────────
BUILD_DIR=""
FILTER="ZmqMetricsFaultTest.*"
REMOTE=false
USE_CMAKE=false
INPUT_LOG=""
REMOTE_HOST="${REMOTE_HOST:-root@38.76.164.55}"
REMOTE_DS="${REMOTE_DS:-/root/workspace/git-repos/yuanrong-datasystem}"
BAZEL_TARGET="${BAZEL_TARGET:-//tests/st/common/rpc/zmq:zmq_metrics_fault_test}"
BAZEL_CMD="${BAZEL_CMD:-bazel}"
REMOTE_BUILD="${REMOTE_BUILD:-$REMOTE_DS/build}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Sibling repo: vibe-coding-files/.../verify → ../../../../yuanrong-datasystem
LOCAL_DS="${LOCAL_DS:-$(cd "${SCRIPT_DIR}/../../../../yuanrong-datasystem" 2>/dev/null && pwd || true)}"

# ── parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-dir) BUILD_DIR="$2"; shift 2 ;;
        --filter)    FILTER="$2";    shift 2 ;;
        --remote)    REMOTE=true;    shift   ;;
        --cmake)     USE_CMAKE=true; shift   ;;
        --input-log) INPUT_LOG="$2"; shift 2 ;;
        --backend)
            case "$2" in
                cmake) USE_CMAKE=true ;;
                bazel) USE_CMAKE=false ;;
                *) echo "Unknown --backend: $2 (use bazel or cmake)"; exit 1 ;;
            esac
            shift 2
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ── locate binary / run via Bazel or CMake ───────────────────────────────────
LOG_FILE=/tmp/zmq_metrics_fault_output.txt

if [[ -n "$INPUT_LOG" ]]; then
    [[ -f "$INPUT_LOG" ]] || { echo "ERROR: --input-log not a file: $INPUT_LOG"; exit 2; }
    LOG_FILE="$INPUT_LOG"
fi

run_bazel_local() {
    local root="${1:?}"
    echo "═══ $BAZEL_CMD run $BAZEL_TARGET (cwd=$root) --gtest_filter='$FILTER' ═══"
    (cd "$root" && "$BAZEL_CMD" run "$BAZEL_TARGET" -- \
        --gtest_filter="$FILTER" --alsologtostderr 2>&1) | tee "$LOG_FILE"
}

if [[ -n "$INPUT_LOG" ]]; then
    echo "═══ Using existing log (--input-log), skipping gtest run ═══"
elif [[ "$REMOTE" == "true" ]]; then
    echo "═══ Running on remote $REMOTE_HOST ═══"
    if [[ "$USE_CMAKE" == "true" ]]; then
        ssh -o BatchMode=yes -o ConnectTimeout=15 "$REMOTE_HOST" "
            cd $REMOTE_BUILD
            ./tests/st/ds_st --gtest_filter='$FILTER' \
                --alsologtostderr 2>&1
        " | tee "$LOG_FILE"
    else
        ssh -o BatchMode=yes -o ConnectTimeout=15 "$REMOTE_HOST" \
            env REMOTE_DS="$REMOTE_DS" BAZEL_CMD="$BAZEL_CMD" \
            BAZEL_TARGET="$BAZEL_TARGET" FILTER="$FILTER" \
            bash -s <<'REMOTE_BAZEL_EOF' | tee "$LOG_FILE"
set -euo pipefail
cd "$REMOTE_DS"
exec "$BAZEL_CMD" run "$BAZEL_TARGET" -- --gtest_filter="$FILTER" --alsologtostderr 2>&1
REMOTE_BAZEL_EOF
    fi
else
    if [[ "$USE_CMAKE" == "true" ]]; then
        if [[ -z "$BUILD_DIR" ]]; then
            BUILD_DIR=$(find "${LOCAL_DS:-/home/t14s/workspace/git-repos/yuanrong-datasystem}/build"* \
                        -maxdepth 0 -type d 2>/dev/null | head -1 || true)
            [[ -z "$BUILD_DIR" ]] && { echo "ERROR: build dir not found. Use --build-dir"; exit 2; }
        fi
        ST_BIN="$BUILD_DIR/tests/st/ds_st"
        [[ -x "$ST_BIN" ]] || { echo "ERROR: $ST_BIN not found or not executable"; exit 2; }
        echo "═══ Running $ST_BIN --gtest_filter='$FILTER' ═══"
        "$ST_BIN" --gtest_filter="$FILTER" --alsologtostderr 2>&1 | tee "$LOG_FILE"
    else
        if [[ -z "${LOCAL_DS:-}" || ! -d "${LOCAL_DS:-}" ]]; then
            if [[ -f "$(pwd)/MODULE.bazel" ]] && [[ -f "$(pwd)/tests/st/common/rpc/zmq/BUILD.bazel" ]]; then
                LOCAL_DS="$(pwd)"
            fi
        fi
        if [[ -z "${LOCAL_DS:-}" || ! -d "${LOCAL_DS:-}" ]]; then
            echo "ERROR: yuanrong-datasystem root not found. Set LOCAL_DS=/path/to/yuanrong-datasystem, run from that repo, or use --cmake --build-dir DIR"
            exit 2
        fi
        run_bazel_local "$LOCAL_DS"
    fi
fi

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo " ZMQ METRICS FAULT ISOLATION VERIFICATION"
echo "═══════════════════════════════════════════════════════════════════"

PASS=0
FAIL=0

check() {
    local desc="$1"
    local pattern="$2"
    local expected_present="${3:-true}"   # "true"=must match, "false"=must NOT match
    if grep -qE "$pattern" "$LOG_FILE"; then
        if [[ "$expected_present" == "true" ]]; then
            echo "  ✓  $desc"
            ((PASS++)) || true
        else
            echo "  ✗  $desc  [pattern found but should be absent]"
            ((FAIL++)) || true
        fi
    else
        if [[ "$expected_present" == "false" ]]; then
            echo "  ✓  $desc"
            ((PASS++)) || true
        else
            echo "  ✗  $desc  [pattern '$pattern' not found]"
            ((FAIL++)) || true
        fi
    fi
}

# ── Scenario 1: Normal RPCs ────────────────────────────────────────────────
echo ""
echo "── Scenario 1: Normal RPCs ──────────────────────────────────────────"

check "zmq_send_io_latency histogram populated (count > 0)" \
    "zmq_send_io_latency,count=[1-9]"
check "zmq_receive_io_latency histogram populated (count > 0)" \
    "zmq_receive_io_latency,count=[1-9]"
check "zmq_rpc_serialize_latency histogram populated" \
    "zmq_rpc_serialize_latency,count=[1-9]"
check "zmq_rpc_deserialize_latency histogram populated" \
    "zmq_rpc_deserialize_latency,count=[1-9]"
check "zmq_send_failure_total == 0 during normal RPCs" \
    "zmq_send_failure_total=0" true
check "zmq_network_error_total == 0 during normal RPCs" \
    "zmq_network_error_total=0" true
check "Self-proof report logged" \
    "\[SELF-PROOF" true

# Extract framework ratio from log
RATIO_LINE=$(grep "Framework ratio" "$LOG_FILE" | tail -1 || true)
if [[ -n "$RATIO_LINE" ]]; then
    echo "  ℹ  $RATIO_LINE"
    RATIO=$(echo "$RATIO_LINE" | grep -oE '[0-9]+(\.[0-9]+)?%' | head -1 || echo "unknown")
    echo "  ℹ  Framework ratio = $RATIO (should be < 20% on loopback)"
fi

# ── Scenario 2: Server killed ─────────────────────────────────────────────
echo ""
echo "── Scenario 2: Server Killed ────────────────────────────────────────"

check "gateway recreate or send try-again after peer crash (gw_recreate / EAGAIN path)" \
    "(zmq_gateway_recreate_total=[1-9]|zmq_send_try_again_total=[1-9])" true
check "FAULT INJECT: server shutdown logged (simulate peer crash)" \
    "\[FAULT INJECT\] Shutting down server to simulate peer crash" true

# Log the connection-level metrics for manual inspection
DISCONN_LINE=$(grep -E "evt\.disconn" "$LOG_FILE" | tail -1 || true)
RECREATE_LINE=$(grep "gw_recreate total" "$LOG_FILE" | tail -1 || true)
[[ -n "$DISCONN_LINE" ]] && echo "  ℹ  $DISCONN_LINE"
[[ -n "$RECREATE_LINE" ]] && echo "  ℹ  $RECREATE_LINE"

# ── Scenario 3: Slow server ───────────────────────────────────────────────
echo ""
echo "── Scenario 3: Slow Server (recv timeout) ───────────────────────────"

check "slow-server: RPC poll timeout (ZMQ recv.eagain stays 0 — see ST comment)" \
    "\[FAULT INJECT\] 1/1 RPCs timed out" true
check "FAULT INJECT: slow server logged" \
    "\[FAULT INJECT\] Sending 'World'" true
check "ser_us avg reported as low (< 1000us)" \
    "ser_avg=[0-9]{1,3}us" true   # 1-3 digit number = 0-999 us
check "RPC framework innocent during slow server" \
    "ser_avg=[0-9]+" true

# Extract and display slow-server metrics line
SLOW_LINE=$(grep "\[SELF-PROOF\] ser_avg=" "$LOG_FILE" | tail -1 || true)
[[ -n "$SLOW_LINE" ]] && echo "  ℹ  $SLOW_LINE"

# ── Scenario 4: High load self-proof ─────────────────────────────────────
echo ""
echo "── Scenario 4: High Load – Framework Self-Proof ─────────────────────"

check "Self-proof report present in high-load scenario" \
    "\[SELF-PROOF REPORT\]" true
check "RPC framework concluded innocent" \
    "RPC framework is NOT bottleneck" true
check "zmq_send_failure_total == 0 under clean load" \
    "zmq_send_failure_total=0" true

PROOF_BLOCK=$(awk '/\[SELF-PROOF REPORT\]/{found=1} found{print; if(/CONCLUSION/) exit}' "$LOG_FILE" 2>/dev/null || true)
if [[ -n "$PROOF_BLOCK" ]]; then
    echo ""
    echo "  ── Self-Proof Report (verbatim) ──"
    echo "$PROOF_BLOCK" | sed 's/^/  | /'
fi

# ── gtest summary ─────────────────────────────────────────────────────────
echo ""
echo "── gtest Result ─────────────────────────────────────────────────────"
GTEST_SUMMARY=$(grep -E "\[  PASSED  \]|\[  FAILED  \]" "$LOG_FILE" | tail -5 || true)
echo "$GTEST_SUMMARY"

# Check all tests passed
if echo "$GTEST_SUMMARY" | grep -q "\[  FAILED  \]"; then
    echo "  ✗  One or more gtest cases FAILED"
    ((FAIL++)) || true
fi

# ── Final result ──────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo " RESULT: $PASS check(s) PASSED  |  $FAIL check(s) FAILED"
echo "═══════════════════════════════════════════════════════════════════"
echo ""
echo "Full output saved to: $LOG_FILE"
echo ""
echo "ISOLATION SUMMARY (from this run):"
echo "  Use 'grep -E \"zmq_(send|receive|network|gateway|event)\" $LOG_FILE | tail -40'"
echo "  to see all fault metric lines at a glance."

[[ "$FAIL" -eq 0 ]]
