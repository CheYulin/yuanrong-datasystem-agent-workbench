"""Compatibility entry; canonical owner: kvcbench.diagnostics.mooncake_queue."""
import sys
from kvcbench.diagnostics import mooncake_queue as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
