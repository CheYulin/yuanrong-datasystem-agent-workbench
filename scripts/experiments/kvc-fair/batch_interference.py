"""Temporary compatibility entry; implementation: kvcbench.observe.interference."""
import sys
from kvcbench.observe import interference as implementation

globals().update({k: v for k, v in vars(implementation).items() if not k.startswith("__")})
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
