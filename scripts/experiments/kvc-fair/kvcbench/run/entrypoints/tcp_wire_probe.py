"""Compatibility entry; canonical owner: kvcbench.diagnostics.tcp_wire."""
import sys
from kvcbench.diagnostics import tcp_wire as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
