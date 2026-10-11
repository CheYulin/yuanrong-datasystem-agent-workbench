"""Contract tests for wb-daily."""

from __future__ import annotations

import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class WbDailyContractTest(unittest.TestCase):
    def test_skill_routes_to_daily_profile(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: wb-daily", text)
        self.assertIn("daily.full", text)
        self.assertIn("coverage.json", text)
        self.assertIn("perf_hotspots.md", text)
        self.assertIn("ds_harness.py daily", text)


if __name__ == "__main__":
    unittest.main()
