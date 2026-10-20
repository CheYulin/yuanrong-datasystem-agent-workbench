"""Local mock regressions for batch identities, bytes, barriers and admission."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import batch_benchmark
import batch_runner
import batch_stage


class Clock:
    def __init__(self):
        self.ns = 0

    def monotonic_ns(self):
        self.ns += 1_000_000
        return self.ns

    def monotonic(self):
        return self.monotonic_ns() / 1e9

    def sleep(self, seconds):
        self.ns += int(seconds * 1e9)


class Connection:
    def __init__(self, clock):
        self.clock, self.messages, self.stage = clock, [], "setup"
        self.receives = 0
        self.closed = False

    def send(self, message):
        self.messages.append(message)

    def recv(self):
        self.receives += 1
        if self.receives < 3:
            self.stage = "warm" if self.receives == 1 else "measure"
            return {"start_ns": self.clock.ns}
        self.stage = "post" if self.receives == 3 else "close"
        return {"validate": True} if self.receives == 3 else {"close": True}

    def close(self):
        self.closed = True


class Adapter:
    def __init__(self, connection, config, drop_restore=False):
        self.connection, self.config = connection, config
        self.drop_restore = drop_restore
        self.data, self.calls = {}, []
        self.closed = False
        self.metadata = {"fixture": True}

    def record(self, operation, keys, values=None):
        self.calls.append((self.connection.stage, operation, tuple(keys), values))

    def put_batch(self, keys, values):
        self.record("put", keys, tuple(values))
        if not (self.drop_restore and self.connection.stage == "post"):
            self.data.update(zip(keys, values, strict=True))

    def get_batch(self, keys):
        self.record("get", keys)
        return [self.data[key] for key in keys]

    def exists_batch(self, keys):
        self.record("exists", keys)
        return [key in self.data for key in keys]

    def close(self):
        self.closed = True


class WorkerTests(unittest.TestCase):
    def run_worker(self, *, index=3, batch=16, op="exist_hit", pool_keys=65536,
                   put_keys=262144, config=None, skip_preload=False, drop_restore=False, duration=.05):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config or {"preload_batch": 256}))
            clock = Clock()
            connection = Connection(clock)
            made = []

            def load(path, settings):
                adapter = Adapter(connection, settings, drop_restore=drop_restore)
                made.append(adapter)
                return adapter

            args = {"adapter": "/fixture/adapter.py", "config": str(path), "concurrency": 8,
                    "size": 32, "batch": batch, "pool_keys": pool_keys, "put_keys": put_keys,
                    "skip_preload": skip_preload, "run_id": "same-repeat-and-product",
                    "warmup": 0, "duration": duration, "op": op}
            with patch.object(batch_benchmark, "load_adapter", side_effect=load), \
                    patch.object(batch_benchmark, "time", clock):
                batch_benchmark.worker(index, args, connection)
            return made[0], connection, args

    def test_private_8192_key_pool_stable_across_native_batch_sizes(self):
        pools = []
        for batch in (1, 16, 256, 4096):
            adapter, connection, _ = self.run_worker(batch=batch)
            self.assertFalse(any("fatal" in message for message in connection.messages))
            setup_keys = [key for stage, op, keys, values in adapter.calls if stage == "setup" and op == "put" for key in keys]
            self.assertEqual(len(setup_keys), 8192)
            self.assertEqual(len(set(setup_keys)), 8192)
            self.assertTrue(all("-c3-pool-" in key for key in setup_keys))
            pools.append(setup_keys)
        self.assertTrue(all(pool == pools[0] for pool in pools))

    def test_clients_have_disjoint_same_length_pools(self):
        result = []
        for index in (0, 1, 7):
            adapter, _, _ = self.run_worker(index=index)
            result.append({key for stage, op, keys, _ in adapter.calls if stage == "setup" and op == "put" for key in keys})
        self.assertTrue(all(len(pool) == 8192 for pool in result))
        self.assertTrue(result[0].isdisjoint(result[1]))
        self.assertTrue(result[1].isdisjoint(result[2]))

    def test_fresh_put_total_and_no_reuse_within_or_across_batch_namespaces(self):
        inserted = []
        for batch in (16, 4096):
            adapter, connection, args = self.run_worker(batch=batch, op="put")
            calls = [call for call in adapter.calls if call[:2] == ("measure", "put")]
            self.assertEqual(len(calls), args["put_keys"] // 8 // batch)
            keys = [key for _, _, batch_keys, _ in calls for key in batch_keys]
            self.assertEqual(len(keys), 32768)
            self.assertEqual(len(set(keys)), 32768)
            self.assertTrue(all(f"-insert-b{batch}-" in key for key in keys))
            inserted.append(set(keys))
            metrics = next(message for message in connection.messages if "success" in message)
            self.assertEqual(metrics["success"], args["put_keys"] // 8 // batch)
            self.assertEqual(metrics["failures"], 0)
            self.assertEqual(sum(metrics["histogram"].values()), metrics["success"])
            # Post-validation begins after raw measurement metrics/barrier.
            self.assertEqual(connection.receives, 4)
            self.assertTrue(any(call[:2] == ("post", "get") for call in adapter.calls))
        self.assertTrue(inserted[0].isdisjoint(inserted[1]))

    def test_lmcache_setup_scalar_one_but_measured_exists_is_native_full_list(self):
        adapter, connection, _ = self.run_worker(batch=4096, config={"preload_batch": 1, "get_check_batch": 1})
        self.assertFalse(any("fatal" in message for message in connection.messages))
        self.assertTrue(all(len(keys) == 1 for stage, op, keys, _ in adapter.calls if stage == "setup" and op == "put"))
        measured = [keys for stage, op, keys, _ in adapter.calls if stage == "measure" and op == "exists"]
        self.assertGreater(len(measured), 0)
        self.assertTrue(all(len(keys) == 4096 for keys in measured))
        self.assertFalse(any(stage == "measure" and op in {"put", "get"} for stage, op, _, _ in adapter.calls))

    def test_measured_miss_uses_separate_absent_namespace(self):
        adapter, connection, _ = self.run_worker(batch=16, op="exist_miss")
        measured = [keys for stage, op, keys, _ in adapter.calls if stage == "measure" and op == "exists"]
        self.assertTrue(measured)
        self.assertTrue(all("-absent-" in key for keys in measured for key in keys))
        self.assertFalse(any("fatal" in message for message in connection.messages))

    def test_upsert_changes_versions_then_restores_all_private_pool_bytes(self):
        adapter, connection, _ = self.run_worker(batch=2, op="upsert", pool_keys=64)
        self.assertFalse(any("fatal" in message for message in connection.messages))
        self.assertEqual(adapter.config["write_mode"], "upsert")
        writes = [values for stage, op, _, values in adapter.calls if stage == "measure" and op == "put"]
        self.assertGreater(len(set(writes)), 2)
        for key, value in adapter.data.items():
            position = int(key.rsplit("-", 1)[1])
            expected = hashlib.shake_256(f"batch-v1-c3-v{position % 4}".encode()).digest(32)
            self.assertEqual(value, expected)
        self.assertTrue(adapter.closed)
        self.assertTrue(connection.closed)

    def test_restore_ack_without_rewrite_is_not_silent_success(self):
        _, connection, _ = self.run_worker(batch=2, op="upsert", pool_keys=64, drop_restore=True, duration=.047)
        self.assertTrue(any("fatal" in message for message in connection.messages),
                        "restored baseline must be read back before the next operation/B")


class AdmissionTests(unittest.TestCase):
    def base_fixture(self, parent):
        base, bundle = parent / "baseline", parent / "bundle"
        for relative in ("formal-tcp-001/builds", "scripts/datasystem", "configs", "datasystem",
                         "mooncake/source", "mooncake/deps", "mooncake/venv", "mooncake/wheels",
                         "lmcache/source", "lmcache/venv311"):
            (base / relative).mkdir(parents=True, exist_ok=True)
        bundle.mkdir()
        (base / "tcp-stage.json").write_text(json.dumps({"native_artifacts_revalidated": {}}))
        (base / "products.json").write_text(json.dumps({"products": []}))
        plan = [{"repeat": repeat, "product": product, "size": size, "concurrency": concurrency}
                for repeat in range(3) for product in ["datasystem", "mooncake", "lmcache"]
                for size in [1024, 65536, 1048576] for concurrency in [2, 8, 16]]
        (base / "formal-tcp-001/plan.json").write_text(json.dumps(plan))
        (bundle / "fixture.py").write_text("VALUE = 1\n")
        return base, bundle

    def test_explicit_invalid_baseline_is_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            base, bundle = self.base_fixture(parent)
            (base / "formal-tcp-001/summary.json").write_text(json.dumps(
                {"ended": "fixture-end", "valid": False, "failed": [], "completed": []}))
            args = SimpleNamespace(base=base, root=parent / "new-root", bundle=bundle,
                                   cgroup=parent / "fake-cgroup")
            with patch.object(batch_stage, "drain"):
                with self.assertRaises(RuntimeError):
                    batch_stage.stage(args)
            self.assertFalse(args.root.exists())

    def test_gate_valid_boolean_alone_cannot_admit_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gate").mkdir()
            (root / "gate/summary.json").write_text('{"valid": true}')
            out = root / "out"
            out.mkdir()
            runner = object.__new__(batch_runner.Runner)
            runner.root, runner.out = root, out
            runner.args = SimpleNamespace(action="run", repeats=3, gate="gate")
            runner.products = {name: {} for name in ["datasystem", "mooncake", "lmcache"]}
            runner.event = lambda *args, **kwargs: None
            runner.lifecycle = lambda row, number: "fake-cell"
            fake = SimpleNamespace(returncode=0, send_signal=lambda sig: None, wait=lambda timeout: 0)
            with patch.object(batch_runner, "drain", return_value={}), \
                    patch.object(batch_runner, "plan", return_value=[]), \
                    patch.object(batch_runner.subprocess, "Popen", return_value=fake) as popen:
                self.assertEqual(runner.run(), 1)
                popen.assert_not_called()
            summary = json.loads((out / "summary.json").read_text())
            self.assertIn("complete successful native batch gate", summary["failed"][0])

    def test_deployment_overlay_preserves_baseline_and_freezes_executed_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            base, bundle = self.base_fixture(parent)
            plan = json.loads((base / "formal-tcp-001/plan.json").read_text())
            completed = [f"r{row['repeat']}-{row['product']}-s{row['size']}-c{row['concurrency']}" for row in plan]
            (base / "formal-tcp-001/summary.json").write_text(json.dumps(
                {"ended": "fixture-end", "valid": True, "failed": [], "completed": completed}))
            original = b"ORIGINAL_SHARED_DEFAULT = True\n"
            replacement = b"PER_WORKER_DATABASE_PATH = True\n"
            (base / "scripts/datasystem/runtime.py").write_bytes(original)
            (bundle / "datasystem").mkdir()
            (bundle / "datasystem/runtime.py").write_bytes(replacement)
            args = SimpleNamespace(base=base, root=parent / "new-root", bundle=bundle,
                                   cgroup=parent / "fake-cgroup", mooncake_queue=8192)
            with patch.object(batch_stage, "drain"):
                record = batch_stage.stage(args)
            relative = "scripts/datasystem/runtime.py"
            self.assertEqual((base / relative).read_bytes(), original)
            self.assertEqual((args.root / relative).read_bytes(), replacement)
            self.assertEqual(record["datasystem_runtime_override"]["sha256"], record["inputs"][relative]["sha256"])
            self.assertFalse((args.root / relative).is_symlink())

    def test_missing_or_symlinked_runtime_overlay_cannot_silently_use_shared_default(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            bundle, stage = parent / "bundle", parent / "stage"
            bundle.mkdir(); stage.mkdir()
            with self.assertRaises(ValueError):
                batch_stage.copy_datasystem_runtime(bundle, stage)
            (bundle / "datasystem").mkdir()
            source = parent / "untrusted.py"
            source.write_text("SOURCE = True\n")
            (bundle / "datasystem/runtime.py").symlink_to(source)
            with self.assertRaises(ValueError):
                batch_stage.copy_datasystem_runtime(bundle, stage)
            self.assertFalse((stage / "scripts/datasystem/runtime.py").exists())

    def test_symlink_parent_cannot_stage_inside_immutable_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            base, bundle = self.base_fixture(parent)
            plan = json.loads((base / "formal-tcp-001/plan.json").read_text())
            completed = [f"r{row['repeat']}-{row['product']}-s{row['size']}-c{row['concurrency']}" for row in plan]
            (base / "formal-tcp-001/summary.json").write_text(json.dumps(
                {"ended": "fixture-end", "valid": True, "failed": [], "completed": completed}))
            alias = parent / "sibling-looking-alias"
            alias.symlink_to(base, target_is_directory=True)
            args = SimpleNamespace(base=base, root=alias / "new-root", bundle=bundle,
                                   cgroup=parent / "fake-cgroup")
            with patch.object(batch_stage, "drain"):
                with self.assertRaisesRegex(ValueError, "sibling"):
                    batch_stage.stage(args)
            self.assertFalse((base / "new-root").exists())

    def test_second_monitor_start_failure_still_writes_summary_and_drains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "out"
            out.mkdir()
            runner = object.__new__(batch_runner.Runner)
            runner.root, runner.out = root, out
            runner.args = SimpleNamespace(action="run", repeats=3, gate="gate")
            runner.event = lambda *args, **kwargs: None
            runner.check_gate = lambda: None
            monitor = SimpleNamespace(returncode=0, send_signal=lambda sig: None, wait=lambda timeout: 0)
            with patch.object(batch_runner, "drain", return_value={}) as drain, \
                    patch.object(batch_runner, "plan", return_value=[]), \
                    patch.object(batch_runner.subprocess, "Popen", side_effect=[monitor, OSError("fixture second monitor spawn")]):
                self.assertEqual(runner.run(), 1)
                drain.assert_called()
            summary = json.loads((out / "summary.json").read_text())
            self.assertFalse(summary["valid"])
            self.assertIn("fixture second monitor spawn", summary["failed"][0])
            self.assertTrue(summary["ended"])

    def test_measurement_run_id_and_key_bytes_do_not_depend_on_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = object.__new__(batch_runner.Runner)
            runner.root, runner.out = root, root / "output"
            runner.out.mkdir()
            runner.args = SimpleNamespace(action="run", duration=20, warmup=3, put_keys=262144)
            runner.products = {
                name: {"python": "/fixture/python", "adapter": f"/fixture/{name}.py",
                       "config": f"/fixture/{name}.json", "start": ["/fixture/start", name],
                       "stop": ["/fixture/stop", name]}
                for name in ["datasystem", "mooncake", "lmcache"]}
            runner.event = lambda *args, **kwargs: None
            run_ids = []

            def execute(argv, log, env, timeout=1200):
                if "--op" in argv:
                    run_ids.append(argv[argv.index("--run-id") + 1])
                    out = Path(argv[argv.index("--out") + 1])
                    out.write_text(json.dumps({"valid": True, "success": 1, "keys_s": 16, "duration_s": 20}))

            runner.execute = execute
            with patch.object(batch_runner, "drain", return_value={}), patch.object(batch_runner.time, "sleep"):
                for number, name in enumerate(runner.products, 1):
                    runner.lifecycle({"repeat": 0, "product": name,
                                      "operations": [{"batch": 16, "operation": "get", "supported": True}]}, number)
            self.assertEqual(len(run_ids), 3)
            self.assertEqual(len(set(run_ids)), 1, "same repeat requires identical key bytes across products")


if __name__ == "__main__":
    unittest.main()
