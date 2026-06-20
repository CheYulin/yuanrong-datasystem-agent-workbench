"""Compatibility import shim for scripts.development.lib.datasystem_root."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "development" / "lib"
sys.path.insert(0, str(_LIB))

from datasystem_root import *  # noqa: F401,F403
