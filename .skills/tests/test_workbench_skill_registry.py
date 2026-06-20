"""Workbench skill registry: canonical .skills/ taxonomy."""

from __future__ import annotations

import unittest
from pathlib import Path

WORKBENCH = Path(__file__).resolve().parents[2]
SKILLS = WORKBENCH / ".skills"

ENGINEERING_SKILLS = frozenset({"wb-build", "wb-dev", "wb-daily", "wb-perf"})
DELIVERABLE_SKILLS = frozenset({"wb-html-publish", "wb-docs"})
CANONICAL_WB = ENGINEERING_SKILLS | DELIVERABLE_SKILLS


class WorkbenchSkillRegistryTest(unittest.TestCase):
    def test_exactly_six_skills(self) -> None:
        present = {p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()}
        self.assertEqual(present, CANONICAL_WB)

    def test_engineering_skill_taxonomy(self) -> None:
        readme = (SKILLS / "README.md").read_text(encoding="utf-8")
        for name in ENGINEERING_SKILLS:
            self.assertIn(name, readme)
        for old_name in ("wb-verify", "wb-log-analysis", "wb-perf-research"):
            self.assertNotIn(f"`{old_name}`", readme)

    def test_ops_removed(self) -> None:
        self.assertFalse((WORKBENCH / "ops").exists())

    def test_each_skill_has_tests(self) -> None:
        for name in CANONICAL_WB:
            tests = SKILLS / name / "tests"
            self.assertTrue(tests.is_dir(), f"{name} missing tests/")

    def test_each_engineering_skill_has_contract_sections(self) -> None:
        for name in ENGINEERING_SKILLS:
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            for heading in (
                "## Purpose",
                "## When to Use",
                "## Inputs",
                "## Commands",
                "## Evidence",
                "## Pass/Fail Criteria",
            ):
                self.assertIn(heading, text, f"{name} missing {heading}")


if __name__ == "__main__":
    unittest.main()
