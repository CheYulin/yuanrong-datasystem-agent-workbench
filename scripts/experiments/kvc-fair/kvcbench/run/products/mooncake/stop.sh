#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
RUNTIME_DIR=${RUNTIME_DIR:-$MOONCAKE_ROOT/runtime}
[[ -f "$RUNTIME_DIR/identities.json" ]] || exit 0
exec "$MOONCAKE_ROOT/venv/bin/python" "$MOONCAKE_ROOT/process_identity.py" stop \
  --state "$RUNTIME_DIR/identities.json" --task-root "$MOONCAKE_ROOT"
