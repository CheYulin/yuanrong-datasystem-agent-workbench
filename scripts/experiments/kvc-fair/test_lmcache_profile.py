"""Execute the launch wrapper with a fake final interpreter; never start LMCache."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


WRAPPER = Path(__file__).with_name("lmcache") / "start_server_tcp.sh"


class LMCacheProfileTests(unittest.TestCase):
    def launch(self, parent, extra=None):
        root, logs = parent / "lmcache", parent / "logs"
        (root / "venv311/bin").mkdir(parents=True); logs.mkdir()
        python = root / "venv311/bin/python"
        python.write_text("#!" + sys.executable + "\n" +
            "import json,os,sys\nfrom pathlib import Path\n"
            "if sys.argv[1:2]==['-']:\n os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n"
            "fields=Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()\n"
            "Path(os.environ['FIXTURE_CAPTURE']).write_text(json.dumps({'argv':sys.argv[1:],'pid':os.getpid(),'start_ticks':fields[19],'env':{k:os.environ.get(k) for k in ['LMCACHE_COORDINATOR_EVENT_REPORTING','LMCACHE_TRACK_USAGE','LMCACHE_LOG_LEVEL']}}))\n")
        python.chmod(0o755)
        env = {key: value for key, value in os.environ.items() if key not in {"LMCACHE_FEATURE_PROFILE", "KVC_FEATURE_PROFILE", "LMCACHE_COORDINATOR_EVENT_REPORTING"}}
        capture = parent / "final.json"
        env.update(LMCACHE_ROOT=str(root), LOG_DIR=str(logs), FIXTURE_CAPTURE=str(capture))
        env.update(extra or {})
        result = subprocess.run(["bash", str(WRAPPER)], env=env, capture_output=True, text=True, timeout=5)
        final = json.loads(capture.read_text()) if capture.exists() else None
        manifest = logs / "server-0-launch.json"
        return result, final, json.loads(manifest.read_text()) if manifest.exists() else None

    def test_default_baseline_keeps_original_flags_and_no_new_manifest_or_env(self):
        with tempfile.TemporaryDirectory() as directory:
            result, final, manifest = self.launch(Path(directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = ["-m", "lmcache.cli.main", "server", "--host", "127.0.0.1", "--port", "15550",
                        "--http-host", "127.0.0.1", "--http-port", "18080", "--prometheus-port", "19090",
                        "--l1-size-gb", "4", "--eviction-policy", "LRU", "--chunk-size", "1",
                        "--supported-transfer-mode", "engine_driven", "--shm-name", "", "--no-l1-use-lazy",
                        "--worker-reap-timeout-seconds", "0"]
            self.assertEqual(final["argv"], expected)
            self.assertIsNone(manifest)
            self.assertIsNone(final["env"]["LMCACHE_COORDINATOR_EVENT_REPORTING"])
            self.assertEqual(final["env"]["LMCACHE_TRACK_USAGE"], "false")

    def test_pure_profile_fallback_preserves_transport_and_records_preexec_pid_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            result, final, manifest = self.launch(Path(directory), {"KVC_FEATURE_PROFILE": "pure-kv"})
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = final["argv"]
            self.assertEqual(argv[argv.index("--eviction-policy") + 1], "noop")
            for name in ["--disable-observability", "--disable-metrics", "--disable-logging"]:
                self.assertIn(name, argv)
            self.assertEqual(argv[argv.index("--kv-event-log-size") + 1], "0")
            self.assertEqual(argv[argv.index("--coordinator-url") + 1], "")
            self.assertEqual(argv[argv.index("--shm-name") + 1], "")
            self.assertIn("--http-port", argv)
            self.assertEqual(final["env"]["LMCACHE_COORDINATOR_EVENT_REPORTING"], "false")
            self.assertEqual(manifest["profile"], "pure-kv")
            self.assertEqual(manifest["pid"], final["pid"])
            self.assertEqual(manifest["start_ticks"], final["start_ticks"])
            self.assertEqual(manifest["exec_argv"][1:], argv)
            self.assertIn("not a post-exec live", manifest["scope"])

    def test_explicit_legacy_profile_selection_has_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            result, final, manifest = self.launch(Path(directory), {"LMCACHE_FEATURE_PROFILE": "baseline", "KVC_FEATURE_PROFILE": "pure-kv"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(final["argv"][final["argv"].index("--eviction-policy") + 1], "LRU")
            self.assertIsNone(manifest)

    def test_unknown_profile_never_executes_product(self):
        with tempfile.TemporaryDirectory() as directory:
            result, final, manifest = self.launch(Path(directory), {"KVC_FEATURE_PROFILE": "unknown"})
            self.assertEqual(result.returncode, 2)
            self.assertIsNone(final)
            self.assertIsNone(manifest)


if __name__ == "__main__":
    unittest.main()
