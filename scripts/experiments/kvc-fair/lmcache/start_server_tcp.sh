#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/home/worktrees/kvc-fair-20261020}
LMCACHE_ROOT=${LMCACHE_ROOT:-$TASK_ROOT/lmcache}
SERVER_INDEX=${SERVER_INDEX:-0}
PORT=${PORT:-$((15550 + SERVER_INDEX))}
HTTP_PORT=${HTTP_PORT:-$((18080 + SERVER_INDEX))}
L1_GIB=${L1_GIB:-4}
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export PYTHONHASHSEED=0 LMCACHE_LOG_LEVEL=ERROR
export LMCACHE_TRACK_USAGE=false
FEATURE_PROFILE=${LMCACHE_FEATURE_PROFILE:-${KVC_FEATURE_PROFILE:-baseline}}
EVICTION_POLICY=LRU
FEATURE_ARGS=()
case "$FEATURE_PROFILE" in
  baseline) ;;
  pure-kv)
    EVICTION_POLICY=noop
    FEATURE_ARGS=(--disable-observability --disable-metrics --disable-logging
      --kv-event-log-size 0 --coordinator-url '')
    export LMCACHE_COORDINATOR_EVENT_REPORTING=false
    ;;
  *) printf 'Unknown LMCACHE_FEATURE_PROFILE: %s\n' "$FEATURE_PROFILE" >&2; exit 2 ;;
esac
cd "$LMCACHE_ROOT"
COMMAND=("$LMCACHE_ROOT/venv311/bin/python" -m lmcache.cli.main server \
  --host 127.0.0.1 --port "$PORT" \
  --http-host 127.0.0.1 --http-port "$HTTP_PORT" \
  --prometheus-port "$((19090 + SERVER_INDEX))" \
  --l1-size-gb "$L1_GIB" --eviction-policy "$EVICTION_POLICY" --chunk-size 1 \
  --supported-transfer-mode engine_driven \
  --shm-name '' --no-l1-use-lazy \
  --worker-reap-timeout-seconds 0 "${FEATURE_ARGS[@]}" "$@")
if [[ "$FEATURE_PROFILE" == pure-kv && -n "${LOG_DIR:-}" ]]; then
  "$LMCACHE_ROOT/venv311/bin/python" - "$LOG_DIR/server-$SERVER_INDEX-launch.json" "$FEATURE_PROFILE" "${COMMAND[@]}" <<'PY'
import json
import os
from pathlib import Path
import sys
import time

pid = os.getppid()
fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
record = {"schema_version": 1, "profile": sys.argv[2], "pid": pid, "start_ticks": fields[19],
          "recorded_wall_ns": time.time_ns(), "recorded_mono_ns": time.monotonic_ns(),
          "exec_argv": sys.argv[3:],
          "env": {key: os.environ.get(key) for key in ["KVC_FEATURE_PROFILE", "LMCACHE_FEATURE_PROFILE", "LMCACHE_LOG_LEVEL", "LMCACHE_TRACK_USAGE", "LMCACHE_COORDINATOR_EVENT_REPORTING"]},
          "scope": "Pre-exec argv intent and existing shell PID/start_ticks. Successful exec preserves this PID; this record alone is not a post-exec live command-line or configuration observation."}
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("x") as stream:
    json.dump(record, stream, indent=2)
    stream.write("\n")
PY
fi
exec "${COMMAND[@]}"
