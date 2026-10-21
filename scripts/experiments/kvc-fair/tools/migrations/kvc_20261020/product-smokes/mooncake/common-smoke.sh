#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
TASK_ROOT=${TASK_ROOT:-$(dirname "$MOONCAKE_ROOT")}
OUT_DIR=${OUT_DIR:-$MOONCAKE_ROOT/common-smoke}
mkdir -p "$OUT_DIR"
printf '{}\n' > "$OUT_DIR/config.json"
trap 'bash "$MOONCAKE_ROOT/stop.sh"' EXIT
for size in 1024 1048576; do
  cell_dir="$OUT_DIR/size-$size"
  mkdir -p "$cell_dir"
  export RUNTIME_DIR="$cell_dir/runtime"
  volume=1048576
  ((size == 1048576)) && volume=8388608
  python3 "$TASK_ROOT/cgroup.py" --group server exec -- bash "$MOONCAKE_ROOT/start.sh" > "$cell_dir/start.log" 2>&1
  for operation in put get exist_hit exist_miss; do
    extra=()
    [[ "$operation" == exist_* ]] && extra+=(--skip-preload)
    python3 "$TASK_ROOT/cgroup.py" --group client exec -- "$MOONCAKE_ROOT/venv/bin/python" "$TASK_ROOT/benchmark.py" \
      --adapter "$MOONCAKE_ROOT/adapter.py" --config "$OUT_DIR/config.json" \
      --op "$operation" --size "$size" --concurrency 2 --duration 2 --warmup .1 \
      --total-bytes "$volume" --preload-bytes "$volume" \
      --run-id "mooncake-common-smoke-$size" --out "$cell_dir/$operation.json" "${extra[@]}" \
      > "$cell_dir/$operation.log" 2>&1
  done
  bash "$MOONCAKE_ROOT/stop.sh" > "$cell_dir/stop.log" 2>&1
done
trap - EXIT
"$MOONCAKE_ROOT/venv/bin/python" - "$OUT_DIR" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
cells=[]
for path in sorted(root.glob('size-*/*.json')):
    d=json.loads(path.read_text())
    cells.append({'size':d['parameters']['size'],'operation':d['parameters']['op'],'valid':d['valid'],'success':d['success'],'failures':d['failures']})
assert len(cells)==8 and all(c['valid'] and c['failures']==0 for c in cells)
result={'purpose':'correctness smoke while other source builds may be running; not performance evidence','passed':True,'cells':cells}
(root/'summary.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
PY
