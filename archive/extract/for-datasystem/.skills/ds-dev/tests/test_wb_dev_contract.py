"""Contract tests for wb-dev."""

from __future__ import annotations

import unittest
from pathlib import Path


WORKBENCH = Path(__file__).resolve().parents[3]
SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class WbDevContractTest(unittest.TestCase):
    def test_skill_routes_to_dev_profile(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: wb-dev", text)
        self.assertIn("dev.quick", text)
        self.assertIn("ds_harness.py dev", text)

    def test_dev_gate_scripts_exist(self) -> None:
        for rel in (
            "scripts/lint/check_cpp_line_width.sh",
            "scripts/testing/verify/smoke/run_smoke_remote.sh",
            "scripts/testing/verify/ut/run_ut_remote.sh",
            "scripts/testing/verify/st/run_st_remote.sh",
        ):
            self.assertTrue((WORKBENCH / rel).is_file(), rel)

    def test_remote_runners_use_remote_base(self) -> None:
        for rel in (
            "scripts/testing/verify/smoke/run_smoke_remote.sh",
            "scripts/testing/verify/ut/run_ut_remote.sh",
            "scripts/testing/verify/st/run_st_remote.sh",
        ):
            text = (WORKBENCH / rel).read_text(encoding="utf-8")
            self.assertIn("REMOTE_BASE", text, rel)
            self.assertIn("BUILD_DIR", text, rel)
            self.assertIn("No tests were found", text, rel)
            self.assertIn("CTEST_STATUS", text, rel)
            self.assertNotIn("cd ~/workspace/git-repos/yuanrong-datasystem", text, rel)
            self.assertIn("Running locally", text, rel)


if __name__ == "__main__":
    unittest.main()
