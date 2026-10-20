"""Compatibility entry; canonical owner: kvcbench.compat.legacy.tcp_stage."""
import sys
from kvcbench.compat.legacy import tcp_stage as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
