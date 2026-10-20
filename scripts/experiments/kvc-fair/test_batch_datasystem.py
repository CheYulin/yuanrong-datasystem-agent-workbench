import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch


from kvcbench.run.products import datasystem_batch as batch


class FakeClient:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.failed = []

    def mset(self, keys, values, **options):
        self.calls.append(("mset", keys, values, options))
        for key, value in zip(keys, values):
            if key not in self.failed:
                self.data[key] = value
        return list(self.failed)

    def get(self, keys, **options):
        self.calls.append(("get", keys, options))
        return [self.data.get(key) for key in keys]

    def exist(self, keys):
        self.calls.append(("exist", keys))
        return [key in self.data for key in keys]


class BatchContractTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.closed = []
        self.owner = types.SimpleNamespace(client=self.client, close=lambda: self.closed.append(True))
        self.loader = patch.object(batch, "_load_client", return_value=(self.owner, "ram-only"))
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.adapter = batch.new_adapter({"value_size": 1024})

    def test_every_requested_size_uses_one_native_call_and_preserves_order(self):
        for count in [1, 16, 256, 4096]:
            with self.subTest(batch=count):
                keys = [f"b{count}-k{i}" for i in range(count)]
                values = [bytes([i % 251]) * 1024 for i in range(count)]
                self.client.calls.clear()
                self.adapter.put_batch(keys, values)
                self.assertEqual(len(self.client.calls), 1)
                self.assertEqual(self.client.calls[0][0], "mset")
                self.assertIs(self.client.calls[0][1], keys)
                self.assertIs(self.client.calls[0][2], values)
                self.assertEqual(self.client.calls[0][3], {"write_mode": "ram-only", "ttl_second": 0})
                self.assertEqual(self.adapter.get_batch(keys[::-1]), values[::-1])
                self.assertEqual(self.client.calls[-1][0], "get")
                self.assertEqual(self.client.calls[-1][2], {"convert_to_str": False, "sub_timeout_ms": 0})
                self.assertEqual(self.adapter.exists_batch(keys), [True] * count)
                self.assertEqual(len(self.client.calls), 3)

    def test_upsert_uses_mset_and_returns_changed_values(self):
        self.adapter.put_batch(["k"], [b"a" * 1024])
        self.adapter.put_batch(["k"], [b"b" * 1024])
        self.assertEqual(self.adapter.get_batch(["k"]), [b"b" * 1024])
        self.assertEqual([call[0] for call in self.client.calls], ["mset", "mset", "get"])

    def test_partial_put_raises_without_retry_or_rollback(self):
        self.client.failed = ["bad"]
        with self.assertRaises(batch.BatchWriteError) as caught:
            self.adapter.put_batch(["good", "bad"], [b"a" * 1024, b"b" * 1024])
        self.assertEqual(caught.exception.failed_keys, ("bad",))
        self.assertEqual(caught.exception.requested_count, 2)
        self.assertEqual(self.client.data, {"good": b"a" * 1024})
        self.assertEqual(len(self.client.calls), 1)

    def test_mixed_get_missing_is_failure_and_exists_preserves_false(self):
        self.adapter.put_batch(["good"], [b"a" * 1024])
        with self.assertRaisesRegex(RuntimeError, "missing"):
            self.adapter.get_batch(["good", "absent"])
        self.assertEqual(self.adapter.exists_batch(["absent", "good"]), [False, True])
        self.assertEqual(self.adapter.exists_batch(["absent"]), [False])

    def test_native_errors_propagate_unchanged_without_fallback(self):
        for method, public, values in [("mset", self.adapter.put_batch, [["x"], [b"a" * 1024]]),
                                       ("get", self.adapter.get_batch, [["x"]]),
                                       ("exist", self.adapter.exists_batch, [["x"]])]:
            with self.subTest(method=method):
                error = RuntimeError("transport timed out; outcome unknown")
                with patch.object(self.client, method, side_effect=error) as sdk:
                    with self.assertRaises(RuntimeError) as caught:
                        public(*values)
                    self.assertIs(caught.exception, error)
                    sdk.assert_called_once()

    def test_invalid_batch_inputs_never_call_sdk(self):
        for keys in [[], "key", ["a", "a"], [""], [1], [str(i) for i in range(10001)]]:
            with self.subTest(keys=repr(keys[:2])):
                with self.assertRaises(ValueError):
                    self.adapter.get_batch(keys)
        for values in [[], [b"a"], [bytearray(1024)], ["a" * 1024]]:
            with self.assertRaises(ValueError):
                self.adapter.put_batch(["a"], values)
        self.assertEqual(self.client.calls, [])

    def test_malformed_sdk_return_values_cannot_count_as_success(self):
        for value in [[], [None], [b"x"], [bytearray(1024)], b"x" * 1024]:
            with self.subTest(get=value):
                with patch.object(self.client, "get", return_value=value):
                    with self.assertRaises(RuntimeError):
                        self.adapter.get_batch(["k"])
        for value in [[], [0], [1], [None], False]:
            with self.subTest(exist=value):
                with patch.object(self.client, "exist", return_value=value):
                    with self.assertRaises(RuntimeError):
                        self.adapter.exists_batch(["k"])
        with patch.object(self.client, "mset", return_value=None):
            with self.assertRaises(RuntimeError):
                self.adapter.put_batch(["k"], [b"x" * 1024])

    def test_metadata_does_not_claim_one_rpc_or_actual_transport_observation(self):
        meta = self.adapter.metadata
        self.assertEqual(meta["max_batch_keys"], {"put": 10000, "get": 10000, "exists": 100000})
        self.assertEqual(meta["get_native_chunks"]["max_keys_per_worker_chunk"], 1024)
        self.assertIn("gate required", meta["transport_requirement"])
        self.assertTrue(meta["validation_in_api_timing"])
        self.assertFalse(meta["scalar_fallback"])
        self.assertFalse(meta["adapter_batch_splitting"])

    def test_close_delegates_to_original_sdk_owner(self):
        self.adapter.close()
        self.assertEqual(self.closed, [True])
        self.assertIsNone(self.adapter.client)


class LoaderTests(unittest.TestCase):
    def test_explicit_absolute_adapter_is_loaded_without_sdk_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fake_adapter.py"
            path.write_text("from types import SimpleNamespace\n"
                            "WriteMode=SimpleNamespace(NONE_L2_CACHE='ram')\n"
                            "def new_adapter(config): return SimpleNamespace(client=config['sentinel'])\n")
            sentinel = object()
            owner, mode = batch._load_client({"scalar_adapter": str(path), "sentinel": sentinel})
            self.assertIs(owner.client, sentinel)
            self.assertEqual(mode, "ram")

    def test_relative_or_missing_adapter_is_rejected(self):
        for path in ["relative.py", "/nonexistent/kvc-batch-adapter.py"]:
            with self.assertRaises(ValueError):
                batch._load_client({"scalar_adapter": path})


if __name__ == "__main__":
    unittest.main()
