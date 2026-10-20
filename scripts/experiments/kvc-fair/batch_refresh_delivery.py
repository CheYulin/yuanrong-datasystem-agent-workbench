"""Temporary compatibility entry; implementation: kvcbench.report.refresh_batch."""
import sys
from kvcbench.report import refresh_batch as implementation

globals().update({k: v for k, v in vars(implementation).items() if not k.startswith("__")})
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
