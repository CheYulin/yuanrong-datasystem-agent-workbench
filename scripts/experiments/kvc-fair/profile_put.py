"""Compatibility entry; canonical owner: kvcbench.diagnostics.put."""
import sys
from kvcbench.diagnostics import put as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
