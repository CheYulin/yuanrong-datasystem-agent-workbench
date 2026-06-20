"""Contract tests for wb-build."""

from __future__ import annotations

import unittest
from pathlib import Path


WORKBENCH = Path(__file__).resolve().parents[3]
SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class WbBuildContractTest(unittest.TestCase):
    def test_skill_routes_to_build_profiles(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: wb-build", text)
        self.assertIn("build.quick", text)
        self.assertIn("build.full", text)
        self.assertIn("ds_harness.py build", text)

    def test_build_scripts_exist(self) -> None:
        for rel in (
            "scripts/build/build_cmake.sh",
            "scripts/build/build_bazel.sh",
            "scripts/build/rsync_datasystem_remote_bazel.sh",
        ):
            self.assertTrue((WORKBENCH / rel).is_file(), rel)

    def test_build_wrappers_resolve_sibling_datasystem(self) -> None:
        for rel in ("scripts/build/build_cmake.sh", "scripts/build/build_bazel.sh"):
            text = (WORKBENCH / rel).read_text(encoding="utf-8")
            self.assertIn("../../../yuanrong-datasystem", text, rel)
            self.assertNotIn('${SCRIPT_DIR}/../../yuanrong-datasystem', text, rel)


if __name__ == "__main__":
    unittest.main()
