"""Compatibility entry; canonical owner: kvcbench.diagnostics.batch."""
import sys
from kvcbench.diagnostics import batch as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
