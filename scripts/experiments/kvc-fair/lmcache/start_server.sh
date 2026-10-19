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
cd "$LMCACHE_ROOT"
exec "$LMCACHE_ROOT/venv311/bin/python" -m lmcache.cli.main server \
  --host 127.0.0.1 --port "$PORT" \
  --http-host 127.0.0.1 --http-port "$HTTP_PORT" \
  --prometheus-port "$((19090 + SERVER_INDEX))" \
  --l1-size-gb "$L1_GIB" --eviction-policy LRU --chunk-size 1 \
  --supported-transfer-mode engine_driven \
  --shm-name "kvcfair20261020_lmcache_$SERVER_INDEX" --no-l1-use-lazy \
  --worker-reap-timeout-seconds 0 "$@"
