"""Tests for harness evidence parsers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "harness"
sys.path.insert(0, str(HARNESS))

from parsers.evidence import parse_ctest_log  # noqa: E402


class EvidenceParserTest(unittest.TestCase):
    def test_parse_ctest_log_counts(self) -> None:
        text = """
100% tests passed, 0 tests failed out of 3
  1/3 Test foo Passed
  2/3 Test bar Passed
  3/3 Test baz Passed
"""
        data = parse_ctest_log(text)
        self.assertEqual(data["passed"], 3)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(data["success_rate_percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
