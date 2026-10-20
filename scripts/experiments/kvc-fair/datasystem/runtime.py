"""Compatibility entry; implementation: kvcbench.run.products.datasystem.runtime."""
import sys
from pathlib import Path

for parent in Path(__file__).resolve().parents:
    if (parent / "kvcbench").is_dir():
        sys.path.insert(0, str(parent))
        break
from kvcbench.run.products.datasystem import runtime as implementation
globals().update({key: value for key, value in vars(implementation).items() if not key.startswith("__")})
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
