#!/usr/bin/env bash
# Background loop for monitor_mr1119_ci.sh (fallback when crontab unavailable).
#
# Usage:
#   bash scripts/harness/monitor_mr1119_ci_loop.sh start
#   bash scripts/harness/monitor_mr1119_ci_loop.sh stop
#   bash scripts/harness/monitor_mr1119_ci_loop.sh status

set -euo pipefail

INTERVAL_SEC="${INTERVAL_SEC:-300}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITOR="${SCRIPT_DIR}/monitor_mr1119_ci.sh"
WORKBENCH="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LOG_DIR="${WORKBENCH}/logs"
PID_FILE="${LOG_DIR}/mr1119-ci-monitor.pid"
LOG_FILE="${LOG_DIR}/mr1119-ci-monitor.log"

mkdir -p "${LOG_DIR}"

start() {
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "already running pid=$(cat "${PID_FILE}")"
    exit 0
  fi
  nohup bash -c "
    while true; do
      bash \"${MONITOR}\" >> \"${LOG_FILE}\" 2>&1 || true
      sleep ${INTERVAL_SEC}
    done
  " >/dev/null 2>&1 &
  echo $! > "${PID_FILE}"
  echo "started pid=$(cat "${PID_FILE}") interval=${INTERVAL_SEC}s log=${LOG_FILE}"
}

stop() {
  if [[ ! -f "${PID_FILE}" ]]; then
    echo "not running"
    exit 0
  fi
  kill "$(cat "${PID_FILE}")" 2>/dev/null || true
  rm -f "${PID_FILE}"
  echo "stopped"
}

status() {
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "running pid=$(cat "${PID_FILE}") log=${LOG_FILE}"
    tail -5 "${LOG_FILE}" 2>/dev/null || true
  else
    echo "not running"
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
