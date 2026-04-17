#!/usr/bin/env bash
# =============================================================================
# verify_zmq_fault_injection_logs.sh
#
# After ZmqMetricsFaultTest.* (ds_st) completes, validates that **key log lines**
# required for fault localization appear in the captured output.
#
# Usage:
#   ./verify_zmq_fault_injection_logs.sh /path/to/captured.log
#   ./verify_zmq_fault_injection_logs.sh --run [--backend bazel|cmake] [--build-dir DIR] [--filter F]
#       Local E2E: runs verify_zmq_metrics_fault.sh (same as CMake/Bazel ST path), then runs these
#       log checks. Log path: /tmp/zmq_metrics_fault_output.txt (same default as metrics script).
#   ./verify_zmq_fault_injection_logs.sh --remote [--backend bazel|cmake]
#       Default backend: bazel (bazel run //tests/st/common/rpc/zmq:zmq_metrics_fault_test).
#       --backend cmake uses CMake ds_st under REMOTE_BUILD.
#
# Environment:
#   BAZEL_CMD        Bazel executable (default: bazel). Same as verify_zmq_metrics_fault.sh.
#   BAZEL_ST_FAULT / BAZEL_TARGET   ST test target (default: //tests/st/common/rpc/zmq:zmq_metrics_fault_test)
#   LOCAL_DS         yuanrong-datasystem root (local --run / local bazel; optional if cwd is repo)
#
# Environment (with --remote):
#   REMOTE_HOST      default root@38.76.164.55
#   REMOTE_DS        default /root/workspace/git-repos/yuanrong-datasystem
#   REMOTE_BUILD     default ${REMOTE_DS}/build
#   BUILD_BACKEND    default bazel (overridden by --backend / --cmake)
#
# Exit: 0 all mandatory patterns found; 1 missing mandatory pattern
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_METRICS="${VERIFY_METRICS:-$SCRIPT_DIR/verify_zmq_metrics_fault.sh}"
DEFAULT_METRICS_LOG="${DEFAULT_METRICS_LOG:-/tmp/zmq_metrics_fault_output.txt}"

REMOTE_HOST="${REMOTE_HOST:-root@38.76.164.55}"
REMOTE_DS="${REMOTE_DS:-/root/workspace/git-repos/yuanrong-datasystem}"
REMOTE_BUILD="${REMOTE_BUILD:-${REMOTE_DS}/build}"
BAZEL_ST_FAULT="${BAZEL_ST_FAULT:-//tests/st/common/rpc/zmq:zmq_metrics_fault_test}"
BAZEL_CMD="${BAZEL_CMD:-bazel}"
BUILD_BACKEND="${BUILD_BACKEND:-bazel}"
LOG_FILE=""
REMOTE_MODE=false
RUN_LOCAL=false
BUILD_DIR=""
FILTER="ZmqMetricsFaultTest.*"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote) REMOTE_MODE=true; shift ;;
    --run) RUN_LOCAL=true; shift ;;
    --cmake) BUILD_BACKEND=cmake; shift ;;
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --filter) FILTER="$2"; shift 2 ;;
    --backend)
      BUILD_BACKEND="$2"
      if [[ "$BUILD_BACKEND" != "bazel" && "$BUILD_BACKEND" != "cmake" ]]; then
        echo "ERROR: --backend must be bazel or cmake"
        exit 2
      fi
      shift 2
      ;;
    -h|--help)
      echo "Usage:"
      echo "  $0 <logfile>"
      echo "  $0 --run [--backend bazel|cmake] [--build-dir DIR] [--filter F]   # local: run ST + verify logs"
      echo "  $0 --remote [--backend bazel|cmake]                              # remote ssh + verify"
      echo "Env: BAZEL_CMD, BAZEL_ST_FAULT, LOCAL_DS, VERIFY_METRICS, REMOTE_*"
      exit 0
      ;;
    *)
      if [[ -f "$1" ]]; then
        LOG_FILE="$1"
        shift
      else
        echo "Usage: $0 <logfile> | $0 --run [...] | $0 --remote [...]  ($0 --help)"
        exit 2
      fi
      ;;
  esac
done

if [[ "$RUN_LOCAL" == true ]]; then
  if [[ "$REMOTE_MODE" == true ]]; then
    echo "ERROR: use either --run (local) or --remote, not both"
    exit 2
  fi
  if [[ -n "$LOG_FILE" ]]; then
    echo "ERROR: do not pass a log file path together with --run"
    exit 2
  fi
  [[ -f "$VERIFY_METRICS" ]] || { echo "ERROR: VERIFY_METRICS not found: $VERIFY_METRICS"; exit 2; }
  export BAZEL_TARGET="${BAZEL_TARGET:-$BAZEL_ST_FAULT}"
  MARGS=(--filter "$FILTER")
  if [[ "$BUILD_BACKEND" == "cmake" ]]; then
    MARGS+=(--cmake)
    [[ -n "$BUILD_DIR" ]] && MARGS+=(--build-dir "$BUILD_DIR")
  else
    MARGS+=(--backend bazel)
  fi
  echo "═══ Local E2E: $VERIFY_METRICS ${MARGS[*]} → then fault-injection log checks ═══"
  "$VERIFY_METRICS" "${MARGS[@]}"
  LOG_FILE="$DEFAULT_METRICS_LOG"
  [[ -f "$LOG_FILE" ]] || { echo "ERROR: expected log from metrics script: $LOG_FILE"; exit 2; }
elif [[ "$REMOTE_MODE" == "true" ]]; then
  LOG_FILE="$(mktemp /tmp/zmq_fault_log.XXXXXX)"
  trap 'rm -f "$LOG_FILE"' EXIT
  if [[ "$BUILD_BACKEND" == "cmake" ]]; then
    ssh -o BatchMode=yes -o ConnectTimeout=20 "$REMOTE_HOST" bash -s <<EOF | tee "$LOG_FILE"
set -euo pipefail
cd "${REMOTE_BUILD}"
./tests/st/ds_st --gtest_filter='ZmqMetricsFaultTest.*' --alsologtostderr 2>&1
EOF
  else
    ssh -o BatchMode=yes -o ConnectTimeout=20 "$REMOTE_HOST" \
      env REMOTE_DS="$REMOTE_DS" BAZEL_ST_FAULT="$BAZEL_ST_FAULT" BAZEL_CMD="$BAZEL_CMD" \
      bash -s <<'EOF' | tee "$LOG_FILE"
set -euo pipefail
cd "$REMOTE_DS"
exec "$BAZEL_CMD" run "$BAZEL_ST_FAULT" -- --gtest_filter='ZmqMetricsFaultTest.*' --alsologtostderr 2>&1
EOF
  fi
elif [[ -n "$LOG_FILE" && -f "$LOG_FILE" ]]; then
  :
else
  echo "Usage: $0 <logfile> | $0 --run [--backend bazel|cmake] ... | $0 --remote [...]  ($0 --help)"
  exit 2
fi

echo "═══════════════════════════════════════════════════════════════════"
echo " ZMQ fault-injection log verification"
echo " Log: $LOG_FILE"
echo "═══════════════════════════════════════════════════════════════════"

PASS=0
FAIL=0

need() {
  local name="$1"
  local pattern="$2"
  if grep -qE "$pattern" "$LOG_FILE"; then
    echo "  ✓  $name"
    ((++PASS)) || true
  else
    echo "  ✗  MISSING: $name  (pattern: $pattern)"
    ((++FAIL)) || true
  fi
}

echo ""
echo "── Mandatory: gtest ──"
if grep -qE '\[  FAILED  \]' "$LOG_FILE"; then
  echo "  ✗  gtest reported FAILED"
  ((++FAIL)) || true
else
  ((++PASS)) || true
  echo "  ✓  No [ FAILED ] line in summary"
fi
need "gtest PASSED line present" '\[  PASSED  \]'
# gtest_brief=1 omits per-suite "[----------] N tests from Foo"; accept summary line instead.
need "Four fault-injection cases" '\[----------\] 4 tests from ZmqMetricsFaultTest|\[==========\] 4 tests from 1 test suite ran'

echo ""
echo "── Scenario: Normal RPCs + metrics dump ──"
need "METRICS DUMP tag (normal)" '\[METRICS DUMP - Normal RPCs\]'
need "Histogram lines (zmq io latency)" 'zmq_(send|receive)_io_latency,count='
need "Self-proof ratio line" '\[SELF-PROOF\] framework_ratio='

echo ""
echo "── Scenario: Server killed (peer crash) ──"
need "Fault inject: shutdown" '\[FAULT INJECT\] Shutting down server'
need "METRICS DUMP (server killed)" '\[METRICS DUMP - Server Killed\]'
need "Isolation gw_recreate line" '\[ISOLATION\] gw_recreate total='

echo ""
echo "── Scenario: Slow server (RPC timeout, ZMQ counters clean) ──"
need "Fault inject: World / slow" '\[FAULT INJECT\] Sending .World.'
need "METRICS DUMP (slow server)" '\[METRICS DUMP - Slow Server\]'
need "Isolation ZMQ layer clean" 'ZMQ layer clean|recv\.fail=0.*recv\.eagain=0'

echo ""
echo "── Scenario: High load self-proof ──"
need "METRICS DUMP (high load)" '\[METRICS DUMP - High Load\]'
need "SELF-PROOF REPORT block" '\[SELF-PROOF REPORT\]'
need "CONCLUSION line" 'CONCLUSION:'

echo ""
echo "── Optional: ZMQ socket hard-fail tags (only if errno path hit) ──"
if grep -qE '\[ZMQ_RECV_FAIL\]|\[ZMQ_SEND_FAIL\]' "$LOG_FILE"; then
  echo "  ℹ  Found [ZMQ_RECV_FAIL] or [ZMQ_SEND_FAIL] (hard ZMQ errno path exercised)"
  ((++PASS)) || true
else
  echo "  ○  No [ZMQ_RECV_FAIL]/[ZMQ_SEND_FAIL] in this run (expected for stub poll + clean TCP)"
fi

echo ""
echo "── Optional: blocking recv timeout tag (ZMQ_RCVTIMEO path) ──"
if grep -q '\[ZMQ_RECV_TIMEOUT\]' "$LOG_FILE"; then
  echo "  ℹ  Found [ZMQ_RECV_TIMEOUT]"
  ((++PASS)) || true
else
  echo "  ○  No [ZMQ_RECV_TIMEOUT] (fault tests use RpcOptions timeout + DONTWAIT stub path)"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo " Mandatory RESULT: $PASS matched | $FAIL missing"
echo "═══════════════════════════════════════════════════════════════════"

[[ "$FAIL" -eq 0 ]]
