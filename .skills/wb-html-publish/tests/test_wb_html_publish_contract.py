"""TDD contract tests for wb-html-publish (workbench-only, not datasystem product)."""

from __future__ import annotations

import unittest
from pathlib import Path


WORKBENCH = Path(__file__).resolve().parents[3]
SKILL_DIR = Path(__file__).resolve().parents[1]


class WbHtmlPublishContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    def test_forbids_local_htmls_directory(self) -> None:
        self.assertIn("Do not", self.skill_text)
        self.assertIn("/var/www/html", self.skill_text)
        # Mention of local htmls is allowed only as a prohibition
        self.assertRegex(self.skill_text, r"(?i)do not.*htmls")

    def test_publish_script_exists(self) -> None:
        script = WORKBENCH / "scripts/development/sync/publish_htmls_git.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(script.stat().st_mode & 0o111, "publish_htmls_git.sh must be executable")
        harness = WORKBENCH / "scripts/harness/run_skill_html_verify_remote.sh"
        self.assertTrue(harness.is_file())
        self.assertIn("run_skill_html_verify_remote.sh", self.skill_text)
        self.assertIn("xqyun", self.skill_text)

    def test_not_in_datasystem_skills(self) -> None:
        ds_skills = WORKBENCH.parent / "yuanrong-datasystem/.skills"
        self.assertFalse((ds_skills / "wb-html-publish").exists())


if __name__ == "__main__":
    unittest.main()
