#!/usr/bin/env bash
set -euo pipefail
TOOL_ROOT=${KVC_TOOL_ROOT:-${TASK_ROOT:?set TASK_ROOT or KVC_TOOL_ROOT}}
exec bash "$TOOL_ROOT/kvcbench/build/recipes/lmcache/build_lmcache.sh"
