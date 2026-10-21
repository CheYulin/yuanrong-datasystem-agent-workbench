"""Compatibility entry; implementation: kvcbench.run.products.mooncake.peer."""
import sys
from pathlib import Path

for parent in Path(__file__).resolve().parents:
    if (parent / "kvcbench").is_dir():
        sys.path.insert(0, str(parent))
        break
if __name__ == "__main__":
    import runpy
    runpy.run_module("kvcbench.run.products.mooncake.peer", run_name="__main__")
