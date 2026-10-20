#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
RUNTIME_DIR=${RUNTIME_DIR:-$MOONCAKE_ROOT/runtime}
mkdir -p "$RUNTIME_DIR"
if [[ -f "$RUNTIME_DIR/pids" ]]; then
  while read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "Recorded Mooncake process is still alive: $pid" >&2
      exit 1
    fi
  done < "$RUNTIME_DIR/pids"
fi
: > "$RUNTIME_DIR/pids"
printf '[]\n' > "$RUNTIME_DIR/identities.json"
nohup "$MOONCAKE_ROOT/launch_master.sh" > "$RUNTIME_DIR/master.log" 2>&1 < /dev/null &
pid=$!
echo "$pid" >> "$RUNTIME_DIR/pids"
"$MOONCAKE_ROOT/venv/bin/python" "$MOONCAKE_ROOT/process_identity.py" record --pid "$pid" --state "$RUNTIME_DIR/identities.json" --task-root "$MOONCAKE_ROOT"
"$MOONCAKE_ROOT/venv/bin/python" - <<'PY'
import socket,time
for _ in range(120):
    try:
        socket.create_connection(('127.0.0.1',18551),1).close()
        break
    except OSError:
        time.sleep(.25)
else:
    raise RuntimeError('Mooncake master did not become ready')
PY
for port in 19001 19002; do
  nohup "$MOONCAKE_ROOT/venv/bin/python" "$MOONCAKE_ROOT/peer.py" --port "$port" \
    > "$RUNTIME_DIR/peer-$port.log" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" >> "$RUNTIME_DIR/pids"
  "$MOONCAKE_ROOT/venv/bin/python" "$MOONCAKE_ROOT/process_identity.py" record --pid "$pid" --state "$RUNTIME_DIR/identities.json" --task-root "$MOONCAKE_ROOT"
done
"$MOONCAKE_ROOT/venv/bin/python" - "$RUNTIME_DIR" <<'PY'
from pathlib import Path
import sys,time
root=Path(sys.argv[1])
for _ in range(240):
    if all('"state": "ready"' in (root/f'peer-{p}.log').read_text(errors='replace') for p in [19001,19002]):
        break
    time.sleep(.25)
else:
    raise RuntimeError('Mooncake peers did not become ready')
PY
cat "$RUNTIME_DIR/pids"
