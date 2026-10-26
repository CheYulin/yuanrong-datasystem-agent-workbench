import copy
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from kvclab.remote import state, settings_from, artifact, artifact_index
from kvclab.remote_support import atomic, job_id, physical_cpus, validate_request
from kvclab.remote_observe import Monitor
from kvclab.adapters.datasystem import Adapter
from kvclab.remote_runtime import Engine, workload_sizes
from kvclab.remote_preload import pool_values


def request():
    return {"job_id": "job-001", "action": "run", "config": {
        "nodes": 1, "workers_per_node": 2, "clients": 2, "server_cpus": 4,
        "client_cpus": 2, "memory_gib": 8, "products": ["datasystem", "mooncake"],
        "batches": [1, 16, 256, 4096], "value_sizes": [1024],
        "operations": ["put", "get", "exist_hit", "exist_miss"], "duration_s": 2, "repeats": 1}}


class AdmissionTests(unittest.TestCase):
    def test_complete_native_case_is_accepted(self):
        value = request()
        self.assertIs(validate_request(value, {}), value)

    def test_host_path_command_and_unknown_fields_are_rejected(self):
        for key in ["host", "root", "command", "env"]:
            value = request()
            value["config"][key] = "/tmp/arbitrary"
            with self.assertRaises(ValueError):
                validate_request(value, {})

    def test_nonfinite_bool_and_resource_overflow_are_rejected(self):
        for name, value in [("duration_s", float("nan")), ("duration_s", float("inf")),
                            ("clients", True), ("nodes", 8), ("server_cpus", 25), ("master_cpus", 8)]:
            sample = request()
            sample["config"][name] = value
            with self.assertRaises(ValueError, msg=name):
                validate_request(sample, {})

    def test_sparse_or_duplicate_batches_cannot_fake_cardinality(self):
        for values in [[True], [1.0], [1, 1], [2], []]:
            value = request()
            value["config"]["batches"] = values
            with self.assertRaises(ValueError):
                validate_request(value, {})

    def test_value_size_memory_admission(self):
        value = request()
        value["config"].update(clients=64, value_sizes=[1 << 20])
        with self.assertRaises(ValueError):
            validate_request(value, {})

    def test_bounded_complete_variants(self):
        value = request()
        variant = copy.deepcopy(value["config"])
        variant.update(nodes=2, workers_per_node=2, profile="worker_scaling", master_cpus=2)
        value["plan"] = {"variants": [{"config": variant}]}
        validate_request(value, {})
        variant["products"] = ["mooncake"]
        with self.assertRaises(ValueError):
            validate_request(value, {})

    def test_request_id_cannot_escape_or_inject_arguments(self):
        for value in ["../job", "/tmp/run", "a/b", "--all", "x;id", "a\ncommand", "a" * 81, None]:
            with self.assertRaises(ValueError):
                job_id(value)


class StateTests(unittest.TestCase):
    def test_running_requires_live_matching_pid_ticks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "jobs/job-001").mkdir(parents=True)
            atomic(root / "active-execution.json", {"job_id": "job-001", "pid": 123, "start_ticks": "999"})
            with patch("kvclab.remote.ticks", return_value="999"):
                self.assertTrue(state({"state_root": directory}, "job-001")["running"])
            with patch("kvclab.remote.ticks", return_value="998"):
                value = state({"state_root": directory}, "job-001")
                self.assertFalse(value["running"])
                self.assertTrue(value["interrupted"])

    def test_terminal_receipt_is_not_inferred_from_a_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "jobs/job-001"
            job.mkdir(parents=True)
            complete = {"kind": "complete", "status": "completed_with_failures", "results": [{"status": "failed"}]}
            atomic(job / "complete.json", complete)
            atomic(root / "active-execution.json", {"job_id": "job-001", "pid": 123, "start_ticks": "999", "terminal": True})
            with patch("kvclab.remote.ticks", return_value="999"):
                value = state({"state_root": directory}, "job-001")
            self.assertFalse(value["running"])
            self.assertEqual(value["complete"], complete)
            self.assertFalse(value["interrupted"])

    def test_status_log_tail_is_bounded_and_partial_lines_tolerated(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "jobs/job-001"
            job.mkdir(parents=True)
            with (job / "events.jsonl").open("w") as stream:
                for index in range(1000):
                    stream.write(json.dumps({"kind": "log", "index": index, "payload": "x" * 200}) + "\n")
                stream.write("{partial")
            value = state({"state_root": directory}, "job-001")
            self.assertLessEqual(len(value["recent_events"]), 30)
            self.assertEqual(value["recent_events"][-1]["index"], 999)

    def test_private_cgroup_prefix_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.json"
            config = {name: directory + "/" + name for name in ["native_root", "build_catalog", "fair_root", "state_root"]}
            config["cgroup_root"] = "/sys/fs/cgroup/system.slice"
            atomic(path, config)
            with self.assertRaises(ValueError):
                settings_from(path)


class ObservationTests(unittest.TestCase):
    def test_physical_core_selection_excludes_smt_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number, socket, core in [(0, 0, 0), (1, 1, 0), (40, 0, 0), (41, 1, 0), (2, 0, 1)]:
                path = root / ("cpu" + str(number)) / "topology"
                path.mkdir(parents=True)
                (path / "physical_package_id").write_text(str(socket))
                (path / "core_id").write_text(str(core))
            self.assertEqual(physical_cpus(root), [0, 1, 2])

    def test_metrics_are_windowed_and_missing_is_null(self):
        monitor = Monitor("/unused", "/unused")
        def sample(time, usage, memory):
            return {"mono_ns": time, "roles": {name: {"cpu.stat": {"usage_usec": usage, "throttled_usec": usage // 10},
                    "memory.current": memory} for name in ["server", "client", "server/master"]}}
        monitor.rows = [sample(0, 0, 100 << 30), sample(1_000_000_000, 100_000, 1 << 30), sample(2_000_000_000, 500_000, 2 << 30), sample(3_000_000_000, 900_000, 9 << 30)]
        result = monitor.summarize(1_000_000_000, 2_000_000_000)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["server_cpu_cores"], .4)
        self.assertEqual(result["master_throttled_usec"], 40000)
        self.assertEqual(result["peak_charged_memory_gib"], 2)
        self.assertIsNone(monitor.summarize(10, 20)["server_cpu_cores"])


class NativeAdapterTests(unittest.TestCase):
    def adapter(self, client):
        yr = types.ModuleType("yr")
        ds = types.ModuleType("yr.datasystem")
        kv = types.ModuleType("yr.datasystem.kv_client")
        obj = types.ModuleType("yr.datasystem.object_client")
        ds.KVClient = lambda **kwargs: client.capture(kwargs)
        kv.DataPlacementPolicy = types.SimpleNamespace(PREFERRED_META_OWNER=2)
        obj.WriteMode = types.SimpleNamespace(NONE_L2_CACHE=0)
        with patch.dict("sys.modules", {"yr": yr, "yr.datasystem": ds, "yr.datasystem.kv_client": kv,
                                       "yr.datasystem.object_client": obj}):
            return Adapter({"endpoints": ["127.70.1.1:32531", "127.70.2.1:32532"], "client_index": 1,
                            "value_size": 4, "log_dir": "/tmp/test-kvc-lab-sdk"})

    def client(self):
        class Client:
            def capture(self, arguments):
                self.arguments = arguments
                return self
            def init(self):
                pass
            def mset(self, keys, values, **kwargs):
                self.values = values
                return []
            def get(self, keys, **kwargs):
                return self.values
            def exist(self, keys):
                return [True] * len(keys)
        return Client()

    def test_native_policy_and_no_scalar_fallback(self):
        client = self.client()
        adapter = self.adapter(client)
        self.assertEqual(client.arguments["data_placement_policy"], 2)
        self.assertFalse(client.arguments["enable_local_cache"])
        self.assertEqual(client.arguments["host"], "127.70.2.1")
        adapter.put_batch(["a", "b"], [b"1234", b"5678"])
        self.assertEqual(adapter.get_batch(["a", "b"]), [b"1234", b"5678"])
        self.assertEqual(adapter.exists_batch(["a", "b"]), [True, True])
        self.assertFalse(adapter.metadata["one_hop_verified"])

    def test_partial_or_malformed_native_results_fail_closed(self):
        client = self.client()
        adapter = self.adapter(client)
        client.mset = lambda *args, **kwargs: ["b"]
        with self.assertRaises(RuntimeError):
            adapter.put_batch(["a", "b"], [b"1234", b"5678"])
        client.get = lambda *args, **kwargs: [b"1234"]
        with self.assertRaises(RuntimeError):
            adapter.get_batch(["a", "b"])
        client.exist = lambda *args, **kwargs: [1, 0]
        with self.assertRaises(RuntimeError):
            adapter.exists_batch(["a", "b"])
        with self.assertRaises(ValueError):
            adapter.exists_batch(["a", "a"])


class CommonPoolTests(unittest.TestCase):
    def test_one_common_pool_covers_every_selected_batch(self):
        config = request()["config"]
        for clients in [1, 2, 8, 32]:
            config["clients"] = clients
            pool, puts = workload_sizes(config, 1024)
            for batch in config["batches"]:
                self.assertEqual(pool % (clients * batch), 0)
                self.assertGreaterEqual(pool, clients * batch)
                self.assertEqual(puts[batch] % (clients * batch), 0)

    def test_common_values_match_the_existing_fair_loop(self):
        import hashlib
        actual = pool_values(3, 1024)
        expected = [hashlib.shake_256(f"batch-v1-c3-v{i}".encode()).digest(1024) for i in range(8)]
        self.assertEqual(actual, expected[:4])

    def test_serial_native_cases_skip_duplicate_preload_and_keep_master_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            experiment = request()
            experiment["config"].update(batches=[1, 16], master_cpus=2)
            settings = {"state_root": str(root / "state"), "native_root": str(root / "native"), "fair_root": str(root / "fair")}
            engine = Engine(settings, experiment, lambda value: None)
            cell = engine.job / "deployment"
            cell.mkdir()
            engine.deployment = {"python": "/fake/python", "endpoints": ["127.0.0.1:1"], "workers": 2}
            commands = []
            def execute(name, argv, group, output, **kwargs):
                commands.append((name, list(argv)))
                target = Path(argv[argv.index("--out") + 1])
                if name == "common-preload":
                    atomic(target, {"valid": True})
                else:
                    atomic(target, {"valid": True, "measure_start_mono_ns": 1, "measure_end_mono_ns": 2,
                                    "keys_s": 10, "batch_qps": 10, "p99": 5, "duration_s": 1})
                return 0
            engine.command = execute
            class FakeMonitor:
                def __init__(self, *args):
                    pass
                def start(self):
                    pass
                def stop(self):
                    pass
                def summarize(self, *args):
                    return {}
            try:
                with patch("kvclab.remote_runtime.Monitor", FakeMonitor), patch("kvclab.remote_runtime.shutil.which", return_value=None):
                    engine.measure("mooncake", cell, root / "group", repeats=[0], sizes=[1024])
            finally:
                engine.events.close()
            preload = [argv for name, argv in commands if name == "common-preload"]
            cases = [argv for name, argv in commands if name == "native-case"]
            self.assertEqual(len(preload), 1)
            self.assertEqual(len(cases), 8)
            pool = preload[0][preload[0].index("--pool-keys") + 1]
            reads, writes = set(), set()
            for argv in cases:
                self.assertIn("--skip-preload", argv)
                self.assertEqual(argv[argv.index("--pool-keys") + 1], pool)
                run = argv[argv.index("--run-id") + 1]
                (writes if argv[argv.index("--op") + 1] == "put" else reads).add(run)
            self.assertEqual(len(reads), 1)
            self.assertEqual(len(writes), 2)
            self.assertFalse(reads & writes)
            self.assertTrue(all(row["master_cpus"] == 2 and row["status"] == "valid" for row in engine.results))


class ArtifactTests(unittest.TestCase):
    def setup_job(self, root):
        job = root / "jobs/job-001"
        job.mkdir(parents=True)
        (job / "case").mkdir()
        (job / "case/raw.json").write_bytes(b'{"valid": false}\n')
        (job / "case/accepted.json").write_bytes(b'{"passed": true}\n')
        with (job / "events.jsonl").open("w") as stream:
            stream.write(json.dumps({"kind": "step", "artifact": "case/accepted.json"}) + "\n")
            stream.write(json.dumps({"kind": "result", "result": {"raw_artifact": "case/raw.json"}}) + "\n")
            stream.write("{partial")
        return job

    def test_event_bound_raw_download_hashes_exact_bytes_before_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {"state_root": directory}
            job = self.setup_job(Path(directory))
            result = artifact(settings, "job-001", "case/raw.json")
            data = (job / "case/raw.json").read_bytes()
            self.assertEqual(result["kind"], "artifact")
            self.assertEqual(base64.b64decode(result["data"], validate=True), data)
            self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(result["size"], len(data))
            self.assertFalse((job / "complete.json").exists())
            names = {item["name"] for item in artifact_index(settings, "job-001")}
            self.assertEqual(names, {"events.jsonl", "case/raw.json", "case/accepted.json"})

    def test_arbitrary_traversal_absolute_and_unlisted_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(Path(directory))
            (job / "secret.txt").write_text("not an experiment artifact")
            for name in ["../secret.txt", "/etc/passwd", "case/../case/raw.json", "case//raw.json", "secret.txt", "."]:
                with self.assertRaises(ValueError, msg=name):
                    artifact({"state_root": directory}, "job-001", name)

    def test_symlink_leaf_and_ancestor_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = self.setup_job(root)
            target = root / "outside.json"
            target.write_text("{}")
            (job / "case/raw.json").unlink()
            (job / "case/raw.json").symlink_to(target)
            with self.assertRaises(ValueError):
                artifact({"state_root": directory}, "job-001", "case/raw.json")
            (job / "case/accepted.json").unlink()
            (job / "case/raw.json").unlink()
            (job / "case").rmdir()
            (job / "case").symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                artifact({"state_root": directory}, "job-001", "case/raw.json")

    def test_oversized_files_are_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(Path(directory))
            with (job / "case/raw.json").open("wb") as stream:
                stream.truncate((16 << 20) + 1)
            with self.assertRaisesRegex(ValueError, "16MiB"):
                artifact({"state_root": directory}, "job-001", "case/raw.json")
            entry = next(row for row in artifact_index({"state_root": directory}, "job-001") if row["name"] == "case/raw.json")
            self.assertFalse(entry["downloadable"])


if __name__ == "__main__":
    unittest.main()
