"""TDD contract tests for wb-docs."""

from __future__ import annotations

import unittest
from pathlib import Path

WORKBENCH = Path(__file__).resolve().parents[3]
SKILL_DIR = Path(__file__).resolve().parents[1]


class WbDocsContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    def test_documented_scripts_exist(self) -> None:
        scripts = (
            "scripts/metrics/gen_kv_perf_report.py",
            "scripts/analysis/generate_bugfix_fema_report.py",
            "scripts/development/git/generate_commit_message.sh",
        )
        for rel in scripts:
            self.assertTrue((WORKBENCH / rel).is_file(), rel)
            self.assertIn(rel.split("/")[-1], self.skill_text)

    def test_workbook_docs_exist(self) -> None:
        for name in ("sheet1-call-chain.md", "sheet2-urma-capi.md", "sheet3-tcp-rpc.md"):
            path = WORKBENCH / "docs/observable/workbook" / name
            self.assertTrue(path.is_file(), name)
        self.assertIn("docs/observable/workbook", self.skill_text)

    def test_uses_script_paths_not_ops_commands(self) -> None:
        self.assertNotIn("docs.kv_observability", self.skill_text)


if __name__ == "__main__":
    unittest.main()
