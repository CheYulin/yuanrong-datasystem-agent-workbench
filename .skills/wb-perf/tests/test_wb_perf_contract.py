"""Contract tests for wb-perf."""

from __future__ import annotations

import unittest
from pathlib import Path


WORKBENCH = Path(__file__).resolve().parents[3]
SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class WbPerfContractTest(unittest.TestCase):
    def test_skill_routes_to_perf_profiles(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: wb-perf", text)
        self.assertIn("perf.hotspot", text)
        self.assertIn("perf.regression", text)
        self.assertIn("ds_harness.py perf", text)

    def test_perf_scripts_exist(self) -> None:
        for rel in (
            "scripts/analysis/perf/run_kv_lock_ebpf_workflow.sh",
            "scripts/analysis/perf/kv_executor_perf_analysis.py",
            "scripts/analysis/perf/zmq_rpc_perf_nightly.sh",
            "scripts/metrics/gen_kv_perf_report.py",
        ):
            self.assertTrue((WORKBENCH / rel).is_file(), rel)


if __name__ == "__main__":
    unittest.main()
