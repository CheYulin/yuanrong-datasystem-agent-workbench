"""Temporary compatibility entry; implementation: kvcbench.collect.audits.epochs."""
import sys
from kvcbench.collect.audits import epochs as implementation

globals().update({k: v for k, v in vars(implementation).items() if not k.startswith("__")})
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
