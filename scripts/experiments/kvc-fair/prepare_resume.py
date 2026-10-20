"""Compatibility entry; canonical owner: kvcbench.compat.legacy.prepare_resume."""
import sys
from kvcbench.compat.legacy import prepare_resume as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
