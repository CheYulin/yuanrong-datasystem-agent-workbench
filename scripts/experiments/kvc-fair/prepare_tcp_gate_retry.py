"""Compatibility entry; canonical owner: kvcbench.compat.legacy.prepare_tcp_gate_retry."""
import sys
from kvcbench.compat.legacy import prepare_tcp_gate_retry as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
