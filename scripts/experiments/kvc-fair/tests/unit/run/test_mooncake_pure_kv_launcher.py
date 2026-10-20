"""Run the launcher against a temporary fake executable, never Mooncake."""
import json
import os
from pathlib import Path
from tests.paths import TOOL_ROOT
import shutil
import subprocess
import sys
import tempfile
import unittest


class PureKvLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source = TOOL_ROOT / "kvcbench/run/products/mooncake"
        for name in ["launch_master.sh", "env.sh"]:
            shutil.copy2(source / name, self.root / name)
        self.binary = self.root / "venv/lib/python3.11/site-packages/mooncake/mooncake_master"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text("#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ['CAPTURE']).write_text(json.dumps(sys.argv))\n")
        self.binary.chmod(0o755)
        self.python = self.root / "venv/bin/python"
        self.python.parent.mkdir(parents=True)
        self.python.symlink_to(sys.executable)
        self.environment = {**os.environ, "MOONCAKE_ROOT": str(self.root), "RUNTIME_DIR": str(self.root / "run"),
                            "CAPTURE": str(self.root / "capture.json")}
        self.environment.pop("KVC_FEATURE_PROFILE", None)
        self.expected = [str(self.binary), "--rpc_address=127.0.0.1", "--rpc_port=18551", "--rpc_thread_num=4",
                         "--metrics_host=127.0.0.1", "--metrics_port=18553", "--enable_ha=false", "--enable_offload=false",
                         "--enable_http_metadata_server=false", "--dynamic_replication_mode=off", "--enable_oplog=false"]

    def tearDown(self):
        self.temporary.cleanup()

    def run_launcher(self):
        return subprocess.run(["bash", str(self.root / "launch_master.sh"), "--custom=literal value"],
                              env=self.environment, capture_output=True, text=True)

    def test_baseline_retains_exact_previous_argv_and_creates_no_profile_evidence(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / "capture.json").read_bytes()), self.expected + ["--custom=literal value"])
        self.assertFalse((self.root / "run/master-argv.json").exists())

    def test_pure_kv_argv_equals_actual_fake_execution_and_preserves_literal_arguments(self):
        self.environment["KVC_FEATURE_PROFILE"] = "pure-kv"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads((self.root / "capture.json").read_bytes())
        evidence = json.loads((self.root / "run/master-argv.json").read_bytes())
        self.assertEqual(evidence["argv"], actual)
        self.assertEqual(evidence["profile"], "pure-kv")
        self.assertGreater(evidence["wall_ns"], 0)
        self.assertEqual(actual[:len(self.expected)], self.expected)
        self.assertEqual(actual[-1], "--custom=literal value")
        for flag in ["--enable_metric_reporting=false", "--enable_snapshot=false", "--enable_snapshot_restore=false",
                     "--enable_disk_eviction=false", "--root_fs_dir=", "--enable_kv_events=false", "--enable_multi_tenants=false",
                     "--offload_on_evict=false", "--offload_force_evict=false", "--promotion_on_hit=false",
                     "--eviction_ratio=0", "--eviction_high_watermark_ratio=1"]:
            self.assertEqual(actual.count(flag), 1)
        self.assertEqual(list((self.root / "run").glob("*.tmp")), [])

    def test_evidence_helper_failure_prevents_master_execution_and_preserves_status(self):
        self.environment["KVC_FEATURE_PROFILE"] = "pure-kv"
        self.python.unlink()
        self.python.write_text("#!/bin/sh\nexit 23\n")
        self.python.chmod(0o755)
        self.assertEqual(self.run_launcher().returncode, 23)
        self.assertFalse((self.root / "capture.json").exists())
        self.assertFalse((self.root / "run/master-argv.json").exists())


if __name__ == "__main__":
    unittest.main()
