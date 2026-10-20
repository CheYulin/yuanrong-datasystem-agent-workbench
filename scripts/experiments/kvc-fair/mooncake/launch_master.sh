#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
master="$MOONCAKE_ROOT/venv/lib/python3.11/site-packages/mooncake/mooncake_master"
args=(--rpc_address=127.0.0.1 --rpc_port="${MOONCAKE_PORT:-18551}"
  --rpc_thread_num=4 --metrics_host=127.0.0.1 --metrics_port=18553
  --enable_ha=false --enable_offload=false --enable_http_metadata_server=false
  --dynamic_replication_mode=off --enable_oplog=false)
if [[ "${KVC_FEATURE_PROFILE:-}" == pure-kv ]]; then
  args+=(--enable_metric_reporting=false --enable_snapshot=false
    --enable_snapshot_restore=false --enable_disk_eviction=false --root_fs_dir=
    --enable_kv_events=false --enable_multi_tenants=false
    --offload_on_evict=false --offload_force_evict=false --promotion_on_hit=false
    --eviction_ratio=0 --eviction_high_watermark_ratio=1)
  "$MOONCAKE_ROOT/venv/bin/python" - "${RUNTIME_DIR:-$MOONCAKE_ROOT/runtime}/master-argv.json" "$master" "${args[@]}" "$@" <<'PY'
import json
import os
from pathlib import Path
import sys
import time

target = Path(sys.argv[1])
target.parent.mkdir(parents=True, exist_ok=True)
temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
with temporary.open("x") as stream:
    json.dump({"profile": "pure-kv", "argv": sys.argv[2:], "wall_ns": time.time_ns()}, stream, indent=2)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, target)
PY
fi
exec "$master" "${args[@]}" "$@"
