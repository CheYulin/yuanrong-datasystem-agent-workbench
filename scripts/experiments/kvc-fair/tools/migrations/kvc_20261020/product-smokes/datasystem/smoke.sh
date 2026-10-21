#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/home/worktrees/kvc-fair-20261020}
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
SOURCE=$TASK_ROOT/datasystem
EVIDENCE=$SOURCE/.evidence/build
SERVER_CGROUP=${SERVER_CGROUP:-/sys/fs/cgroup/kvcfair20261020/server}
RUN_DIR=${RUN_DIR:-$TASK_ROOT/runtime/ds-smoke-$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$EVIDENCE"
trap 'python3 "$SCRIPT_DIR/runtime.py" stop --run-dir "$RUN_DIR" --stop-timeout 2 >> "$EVIDENCE/smoke-stop.log" 2>&1' EXIT
python3 "$SCRIPT_DIR/runtime.py" start --cgroup "$SERVER_CGROUP" --run-dir "$RUN_DIR" > "$EVIDENCE/smoke-start.json"
cp "$RUN_DIR/processes.json" "$EVIDENCE/smoke-runtime.json"
set +e
DATASYSTEM_LOG_V=1 python3 "$TASK_ROOT/cgroup.py" exec -- "$SOURCE/.venv/bin/python" "$SCRIPT_DIR/smoke.py" \
    --output "$EVIDENCE/smoke-result.json" --log-dir "$RUN_DIR/client-logs" > "$EVIDENCE/smoke.log" 2>&1
result=$?
set -e
printf '%s\n' "$result" > "$EVIDENCE/smoke-exit-code"
python3 "$SCRIPT_DIR/runtime.py" stop --run-dir "$RUN_DIR" --stop-timeout 2 > "$EVIDENCE/smoke-stop.log"
trap - EXIT
python3 "$SCRIPT_DIR/build_summary.py" --task-root "$TASK_ROOT" --out "$EVIDENCE/build-summary.json"
cat "$EVIDENCE/smoke.log"
exit "$result"
