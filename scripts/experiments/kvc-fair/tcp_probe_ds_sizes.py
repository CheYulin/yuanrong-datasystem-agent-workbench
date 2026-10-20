"""Compatibility entry; canonical owner: kvcbench.diagnostics.datasystem_sizes."""
import sys
from kvcbench.diagnostics import datasystem_sizes as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
