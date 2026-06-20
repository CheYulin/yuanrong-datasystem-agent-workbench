"""Contracts for ds_harness profiles and evidence schema."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


WORKBENCH = Path(__file__).resolve().parents[2]
PROFILES = WORKBENCH / "scripts/harness/profiles.yaml"
HARNESS = WORKBENCH / "scripts/harness/ds_harness.py"


class HarnessProfilesContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load(PROFILES.read_text(encoding="utf-8"))

    def test_required_profiles_exist_with_unique_skill_owner(self) -> None:
        profiles = self.config["profiles"]
        expected = {
            "build.quick": "wb-build",
            "build.full": "wb-build",
            "dev.quick": "wb-dev",
            "dev.default": "wb-dev",
            "daily.full": "wb-daily",
            "perf.hotspot": "wb-perf",
            "perf.regression": "wb-perf",
            "bench.dsbench.smoke": "wb-perf",
            "bench.kvtest.smoke": "wb-perf",
        }
        self.assertEqual({name: profiles[name]["skill"] for name in expected}, expected)

    def test_script_owners_exist_and_are_canonical(self) -> None:
        canonical = {"wb-build", "wb-dev", "wb-daily", "wb-perf", "wb-html-publish", "wb-docs"}
        for rel, owner in self.config["script_owners"].items():
            self.assertIn(owner, canonical, rel)
            if rel.startswith("scripts/"):
                self.assertTrue((WORKBENCH / rel).is_file(), rel)

    def test_profile_steps_have_owned_uses(self) -> None:
        owners = self.config["script_owners"]
        for profile_name, profile in self.config["profiles"].items():
            self.assertIn("skill", profile, profile_name)
            self.assertIn("command_group", profile, profile_name)
            self.assertTrue(profile.get("steps"), profile_name)
            for step in profile["steps"]:
                uses = step.get("uses")
                if isinstance(uses, dict):
                    values = [v for v in uses.values() if v]
                else:
                    values = [uses]
                for rel in values:
                    if str(rel).startswith("scripts/"):
                        self.assertIn(rel, owners, f"{profile_name}:{step['id']} missing owner")

    def test_evidence_schema_declares_required_files(self) -> None:
        required = set(self.config["evidence_schema"]["required_files"])
        self.assertEqual(required, {"summary.json", "steps.jsonl"})

    def test_acceptance_metrics_are_quantified(self) -> None:
        metrics = self.config["acceptance_metrics"]
        self.assertEqual(set(metrics), {"build", "dev", "dev_full", "daily", "perf"})
        self.assertEqual(metrics["build"]["max_long_tail_entries"], 10)
        self.assertEqual(metrics["dev"]["max_minutes"], {"smoke": 5, "ut": 30, "st": 60})
        self.assertNotIn("build", metrics["dev"]["required_steps"])
        self.assertIn("build", metrics["dev_full"]["required_steps"])
        self.assertEqual(metrics["daily"]["perf"]["p95_regression_percent_max"], 10)
        self.assertGreaterEqual(metrics["perf"]["min_supported_sources"], 1)
        for group, spec in metrics.items():
            self.assertEqual(spec["required_dry_run_status"], "DRY_RUN", group)
            self.assertEqual(spec["required_real_status"], "PASS", group)
            self.assertIn("summary.json", spec["required_evidence"], group)
            self.assertIn("steps.jsonl", spec["required_evidence"], group)

    def test_plan_required_capabilities_are_mapped(self) -> None:
        profile_text = PROFILES.read_text(encoding="utf-8")
        for token in (
            "scripts/build/build_cmake.sh",
            "scripts/build/build_bazel.sh",
            "scripts/testing/verify/smoke/run_smoke_remote.sh",
            "scripts/testing/verify/ut/run_ut_remote.sh",
            "scripts/testing/verify/st/run_st_remote.sh",
            "build.sh -b {backend} -c html",
            "scripts/analysis/perf/zmq_rpc_perf_nightly.sh",
            "scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh",
            "scripts/testing/bench/run_dsbench_smoke_remote.sh",
            "scripts/testing/bench/run_kvtest_smoke_remote.sh",
        ):
            self.assertIn(token, profile_text)

    def test_dev_quick_skips_build_in_test_gates(self) -> None:
        profile = self.config["profiles"]["dev.quick"]
        step_ids = [step["id"] for step in profile["steps"]]
        self.assertNotIn("build", step_ids)
        for step in profile["steps"]:
            if step["id"] in {"smoke", "ut", "st"}:
                self.assertIn("--skip-build", step["command"], f"dev.quick:{step['id']}")

    def test_skill_verify_maps_all_canonical_skills(self) -> None:
        verify = self.config.get("skill_verify", {})
        expected = {
            "wb-build",
            "wb-dev",
            "wb-daily",
            "wb-perf",
            "wb-docs",
            "wb-html-publish",
        }
        self.assertEqual(set(verify), expected)
        for skill, spec in verify.items():
            self.assertIn("node", spec, skill)
            self.assertTrue(spec.get("steps"), skill)

    def test_dev_and_daily_build_once_then_skip_build(self) -> None:
        profiles = self.config["profiles"]
        for profile_name in ("dev.default", "daily.full"):
            profile = profiles[profile_name]
            self.assertTrue(profile.get("stop_on_failure"), profile_name)
            step_ids = [step["id"] for step in profile["steps"]]
            self.assertIn("build", step_ids, profile_name)
            for step in profile["steps"]:
                if step["id"] == "build":
                    self.assertNotIn("| tail", step["command"], f"{profile_name}:build")
                if step["id"] in {"smoke", "ut", "st"}:
                    self.assertIn("--skip-build", step["command"], f"{profile_name}:{step['id']}")

    def test_scripts_lib_shims_exist(self) -> None:
        for name in (
            "load_nodes.sh",
            "remote_defaults.sh",
            "rsync_excludes.sh",
            "build_backend.sh",
            "timing.sh",
            "cmake_test_env.sh",
            "common.sh",
            "datasystem_root.sh",
            "datasystem_root.py",
        ):
            self.assertTrue((WORKBENCH / "scripts/lib" / name).is_file(), name)

    def test_verify_skill_entry_exists(self) -> None:
        self.assertTrue((WORKBENCH / "scripts/harness/verify_skill.sh").is_file())
        self.assertTrue((WORKBENCH / "scripts/harness/verify_skill.py").is_file())

    def test_dry_run_json_and_evidence_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            proc = subprocess.run(
                [
                    "python3",
                    str(HARNESS),
                    "build",
                    "--backend",
                    "cmake",
                    "--dry-run",
                    "--json",
                    "--evidence-dir",
                    str(out_dir),
                ],
                cwd=WORKBENCH,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            summary = json.loads(proc.stdout)
            self.assertEqual(summary["status"], "DRY_RUN")
            self.assertEqual(summary["profile"], "build.quick")
            self.assertEqual(summary["skill"], "wb-build")
            self.assertIn("acceptance_verdict", summary)
            self.assertIn("metrics", summary)
            self.assertTrue((out_dir / "summary.json").is_file())
            self.assertTrue((out_dir / "steps.jsonl").is_file())
            self.assertTrue((out_dir / "build_timing.csv").is_file())


if __name__ == "__main__":
    unittest.main()
