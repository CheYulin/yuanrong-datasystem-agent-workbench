#!/usr/bin/env bash
set -euo pipefail
TOOL_ROOT=${KVC_TOOL_ROOT:-${TASK_ROOT:?set TASK_ROOT}}
exec bash "$TOOL_ROOT/kvcbench/build/recipes/mooncake/localdeps-install.sh"
