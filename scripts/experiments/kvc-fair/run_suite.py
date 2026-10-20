"""Compatibility entry; canonical owner: kvcbench.compat.legacy.run_suite."""
import sys
from kvcbench.compat.legacy import run_suite as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
