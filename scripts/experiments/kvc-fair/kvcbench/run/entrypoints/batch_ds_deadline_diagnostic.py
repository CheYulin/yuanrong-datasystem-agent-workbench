"""Compatibility entry; canonical owner: kvcbench.diagnostics.datasystem_deadline."""
import sys
from kvcbench.diagnostics import datasystem_deadline as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
