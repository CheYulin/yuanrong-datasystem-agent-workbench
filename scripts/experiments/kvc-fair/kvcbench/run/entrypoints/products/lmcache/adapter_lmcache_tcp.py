"""Compatibility entry; implementation: kvcbench.run.products.lmcache.tcp_adapter."""
import sys
from pathlib import Path

for parent in Path(__file__).resolve().parents:
    if (parent / "kvcbench").is_dir():
        sys.path.insert(0, str(parent))
        break
from kvcbench.run.products.lmcache import tcp_adapter as implementation
globals().update({key: value for key, value in vars(implementation).items() if not key.startswith("__")})
sys.modules[__name__] = implementation
