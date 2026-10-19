#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
exec "$MOONCAKE_ROOT/venv/bin/mooncake_master" \
  --rpc_address=127.0.0.1 --rpc_port="${MOONCAKE_PORT:-18551}" \
  --rpc_thread_num=4 --metrics_host=127.0.0.1 --metrics_port=18553 \
  --enable_ha=false --enable_offload=false --enable_http_metadata_server=false \
  --dynamic_replication_mode=off --enable_oplog=false "$@"
