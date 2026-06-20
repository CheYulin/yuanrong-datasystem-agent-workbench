# scripts/lib

Shared shell/Python helpers for workbench scripts.

Source from any script under `scripts/`:

```bash
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../lib" && pwd)"  # adjust ../ depth
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/common.sh"
```

`nodes.yaml` resolves via `scripts/config/nodes.yaml`.
