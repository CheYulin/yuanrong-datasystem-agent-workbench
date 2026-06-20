#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# dashboard — start / stop / logs
#
# Usage:
#   bash start.sh       # start (background)
#   bash start.sh stop  # stop
#   bash start.sh logs  # tail logs
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"
LOG="${SCRIPT_DIR}/dashboard.log"

# Resolve user site-packages once
USER_SITE="$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null)"
PYTHONPATH="${USER_SITE}:${SRC_DIR}"

start() {
  if pgrep -f "[d]ashboard " > /dev/null 2>&1; then
    echo "Dashboard already running. PID: $(pgrep -f '[d]ashboard ')"
    return
  fi
  PYTHONPATH="${PYTHONPATH}" nohup python3 -m dashboard --port 8765 >> "$LOG" 2>&1 &
  echo "Dashboard started. PID: $!"
  sleep 2
  curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8765/ || true
  echo "Open http://localhost:8765"
}

stop() {
  pkill -f "[d]ashboard " && echo "Dashboard stopped" || echo "Not running"
}

case "${1:-}" in
  stop) stop ;;
  logs) tail -f "$LOG" ;;
  *)    start ;;
esac
