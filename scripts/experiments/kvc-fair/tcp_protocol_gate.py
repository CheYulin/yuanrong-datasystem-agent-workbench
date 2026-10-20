"""Compatibility entry; canonical owner: kvcbench.compat.legacy.tcp_protocol_gate."""
import sys
from kvcbench.compat.legacy import tcp_protocol_gate as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
