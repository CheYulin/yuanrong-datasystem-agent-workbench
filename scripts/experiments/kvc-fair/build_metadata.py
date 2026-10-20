#!/usr/bin/env python3
"""Compatibility entry for the canonical build metadata module."""
import sys
from kvcbench.build import metadata as implementation

if __name__ == "__main__":
    raise SystemExit(implementation.main())
sys.modules[__name__] = implementation
