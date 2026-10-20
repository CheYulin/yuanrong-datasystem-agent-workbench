"""Explicit source package inventory, independent of build or report operations."""
from pathlib import Path
from kvcbench.platform.files import read_json, regular

INVENTORY = "bundle-inputs.json"


def inventory(root):
    root = Path(root).resolve(strict=True)
    record = read_json(regular(root, INVENTORY))
    paths = record.get("paths", [])
    if record.get("schema_version") != 1 or not paths or paths != sorted(set(paths)) or INVENTORY not in paths:
        raise ValueError("Bundle inventory must be a sorted, unique, versioned complete list")
    for name in paths:
        path = Path(name)
        if any(part.startswith(".") or part in {"__pycache__", "venv", "venv311"} for part in path.parts):
            raise ValueError("Runtime caches must not enter the source bundle")
        regular(root, name)
    package = {p.relative_to(root).as_posix() for p in (root / "kvcbench").rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    if package - set(paths):
        raise ValueError("Bundle inventory omits package files: " + repr(sorted(package - set(paths))))
    return paths
