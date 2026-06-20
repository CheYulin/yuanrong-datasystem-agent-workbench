"""Prevent stale script and skill routing from re-entering live docs."""

from __future__ import annotations

import unittest
from pathlib import Path


WORKBENCH = Path(__file__).resolve().parents[2]
LIVE_DOCS = (
    ".skills/README.md",
    "scripts/README.md",
    "scripts/harness/README.md",
    "docs/agent/scripts-map.md",
    "INDEX.md",
)
FORBIDDEN_TOKENS = (
    "remote_build_run_datasystem.sh",
    "rsync_agent_workbench_to_remote.sh",
    "scripts/documentation/",
    "./ops",
    "wb-verify",
    "wb-log-analysis",
    "wb-perf-research",
)


class StalePathsContractTest(unittest.TestCase):
    def test_live_docs_do_not_reference_removed_entries(self) -> None:
        for rel in LIVE_DOCS:
            path = WORKBENCH / rel
            self.assertTrue(path.is_file(), rel)
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_TOKENS:
                self.assertNotIn(token, text, f"{rel} still references {token}")


if __name__ == "__main__":
    unittest.main()
