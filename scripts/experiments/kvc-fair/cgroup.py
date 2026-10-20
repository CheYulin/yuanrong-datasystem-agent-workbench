"""Compatibility entry; canonical implementation: kvcbench.run.isolation."""
import sys
from kvcbench.run import isolation as implementation
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
