"""Compatibility entry; canonical owner: kvcbench.compat.legacy.prepare_env."""
import sys
from kvcbench.compat.legacy import prepare_env as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
