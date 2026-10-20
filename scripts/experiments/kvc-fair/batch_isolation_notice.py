"""Temporary compatibility entry; implementation: kvcbench.delivery.isolation_notice."""
import sys
from kvcbench.delivery import isolation_notice as implementation

globals().update({k: v for k, v in vars(implementation).items() if not k.startswith("__")})
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
