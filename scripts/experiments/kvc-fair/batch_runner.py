"""Temporary compatibility entry for the canonical run controller."""
import sys
from kvcbench.run import runner as implementation
if __name__ == "__main__":
    from kvcbench.observe.session import Session
    implementation.main(observer_factory=Session)
else:
    sys.modules[__name__] = implementation
