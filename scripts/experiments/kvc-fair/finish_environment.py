"""Compatibility entry; canonical implementation: kvcbench.run.environment_restore."""
import sys
from kvcbench.run import environment_restore as implementation
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
