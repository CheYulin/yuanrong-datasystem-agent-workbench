"""Temporary compatibility entry; implementation: kvcbench.run.products.mooncake_batch."""
import sys
from kvcbench.run.products import mooncake_batch as implementation

globals().update({k: v for k, v in vars(implementation).items() if not k.startswith("__")})
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
