#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/home/worktrees/kvc-fair-20261020}
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
SOURCE=$TASK_ROOT/datasystem
EVIDENCE=$SOURCE/.evidence/build/common-harness
RUN_DIR=$TASK_ROOT/runtime/ds-common-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$EVIDENCE"
printf '{"log_dir":"%s/client-logs"}\n' "$RUN_DIR" > "$EVIDENCE/config.json"
trap 'python3 "$SCRIPT_DIR/runtime.py" stop --run-dir "$RUN_DIR" --stop-timeout 2 >> "$EVIDENCE/stop.log" 2>&1' EXIT
python3 "$SCRIPT_DIR/runtime.py" start --cgroup /sys/fs/cgroup/kvcfair20261020/server --run-dir "$RUN_DIR" > "$EVIDENCE/start.json"
for size in 1024 1048576; do
    for operation in put get exist_hit exist_miss; do
        skip=()
        if [[ "$operation" == exist_* ]]; then
            skip=(--skip-preload)
        fi
        run_id="ds-common-$size"
        if [[ "$operation" == put ]]; then
            run_id="ds-put-$size"
        fi
        python3 "$TASK_ROOT/cgroup.py" exec -- "$SOURCE/.venv/bin/python" "$TASK_ROOT/benchmark.py" \
            --adapter "$SCRIPT_DIR/adapter_datasystem.py" --config "$EVIDENCE/config.json" \
            --op "$operation" --size "$size" --concurrency 2 --duration 0.5 --warmup 0.1 \
            --total-bytes 4194304 --preload-bytes 4194304 --run-id "$run_id" \
            --out "$EVIDENCE/${size}-${operation}.json" "${skip[@]}"
    done
done
python3 "$SCRIPT_DIR/runtime.py" stop --run-dir "$RUN_DIR" --stop-timeout 2 > "$EVIDENCE/stop.log"
trap - EXIT
