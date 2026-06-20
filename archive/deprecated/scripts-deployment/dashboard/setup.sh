#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# dashboard — setup & launcher (no venv needed — uses system Python + user
# site-packages which already has flask, pyyaml, paramiko)
#
# Usage:
#   bash setup.sh       # verify dependencies are available
#   bash setup.sh run   # start the dashboard
#   bash setup.sh stop  # stop the running dashboard
#   bash setup.sh logs  # tail logs
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"
LOG="${SCRIPT_DIR}/dashboard.log"

# User site-packages (where flask/paramiko/pyyaml are installed)
USER_SITE="$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null)"
PYTHONPATH="${USER_SITE}:${SRC_DIR}"

check_deps() {
  echo "Checking dependencies…"
  for pkg in flask paramiko yaml; do
    python3 -c "import ${pkg}" 2>/dev/null && echo "  ✓ ${pkg}" || echo "  ✗ ${pkg} — run: pip install --user ${pkg}"
  done
}

do_run() {
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

do_stop() {
  pkill -f "[d]ashboard " && echo "Dashboard stopped" || echo "Not running"
}

do_logs() {
  tail -f "$LOG"
}

CMD="${1:-}"
case "$CMD" in
  run)  do_run ;;
  stop) do_stop ;;
  logs) do_logs ;;
  "")
     check_deps
     echo ""
     echo "Already installed. Run: bash ${SCRIPT_DIR}/setup.sh run"
     ;;
  *)    echo "Usage: $0 {run|stop|logs}" ;;
esac
