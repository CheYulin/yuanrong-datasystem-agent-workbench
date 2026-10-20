"""Compatibility entry; canonical owner: kvcbench.compat.legacy.verify_tcp."""
import sys
from kvcbench.compat.legacy import verify_tcp as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
