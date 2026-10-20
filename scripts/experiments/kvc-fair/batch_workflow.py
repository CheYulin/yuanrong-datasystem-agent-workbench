"""Compatibility entry; canonical owner: kvcbench.compat.legacy.batch_workflow."""
import sys
from kvcbench.compat.legacy import batch_workflow as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
