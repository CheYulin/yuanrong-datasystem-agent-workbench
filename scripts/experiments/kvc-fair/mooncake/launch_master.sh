#!/usr/bin/env bash
set -euo pipefail
KVC_LAUNCH_ROOT=${KVC_TOOL_ROOT:-${TASK_ROOT:-}}
if [[ -z "$KVC_LAUNCH_ROOT" ]]; then
  KVC_LAUNCH_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  while [[ "$KVC_LAUNCH_ROOT" != / && ! -d "$KVC_LAUNCH_ROOT/kvcbench" ]]; do
    KVC_LAUNCH_ROOT=$(dirname -- "$KVC_LAUNCH_ROOT")
  done
fi
exec bash "$KVC_LAUNCH_ROOT/kvcbench/run/products/mooncake/launch_master.sh" "$@"
