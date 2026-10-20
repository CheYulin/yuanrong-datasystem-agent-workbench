"""Compatibility entry; canonical owner: kvcbench.diagnostics.datasystem_runner."""
import sys
from kvcbench.diagnostics import datasystem_runner as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
