"""Local native-boundary fixtures; no Mooncake process or benchmark is run."""
import os
import sys
import types
import unittest
from unittest.mock import patch

import batch_mooncake


class FakeStore:
    instances = []

    def __init__(self):
        self.calls = []
        self.data = {}
        self.write_status = 0
        self.get_result = None
        self.exist_result = None
        self.close_status = 0
        self.instances.append(self)

    def setup(self, *args):
        self.calls.append(("setup", args))
        return 0

    def put_batch(self, keys, values, config):
        self.calls.append(("put_batch", keys, values, config))
        if self.write_status == 0:
            for key, value in zip(keys, values):
                self.data.setdefault(key, value)
        return self.write_status

    def upsert_batch(self, keys, values, config):
        self.calls.append(("upsert_batch", keys, values, config))
        if self.write_status == 0:
            self.data.update(zip(keys, values))
        return self.write_status

    def get_batch(self, keys):
        self.calls.append(("get_batch", keys))
        return self.get_result if self.get_result is not None else [self.data.get(key, b"") for key in keys]

    def batch_is_exist(self, keys):
        self.calls.append(("batch_is_exist", keys))
        return self.exist_result if self.exist_result is not None else [int(key in self.data) for key in keys]

    def close(self):
        self.calls.append(("close",))
        return self.close_status

    def put(self, *args):
        raise AssertionError("Scalar PUT fallback is forbidden")

    def get(self, *args):
        raise AssertionError("Scalar GET fallback is forbidden")

    def is_exist(self, *args):
        raise AssertionError("Scalar EXIST fallback is forbidden")


class BatchMooncakeTests(unittest.TestCase):
    def setUp(self):
        store = types.ModuleType("mooncake.store")
        store.MooncakeDistributedStore = FakeStore
        store.ReplicateConfig = types.SimpleNamespace
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"mooncake": types.ModuleType("mooncake"), "mooncake.store": store}).start()
        patch.dict(os.environ, {"MC_STORE_MEMCPY": "0", "MC_STORE_LOCAL_HOT_CACHE_SIZE": "0"}).start()
        self.adapter = batch_mooncake.new_adapter({"value_size": 1024})
        self.native = self.adapter.store

    def test_all_requested_batch_sizes_use_exactly_one_native_call_per_operation(self):
        for count in [1, 16, 256, 4096]:
            with self.subTest(count=count):
                keys = [f"b{count}-k{i}" for i in range(count)]
                values = [i.to_bytes(4, "little") + bytes(1020) for i in range(count)]
                start = len(self.native.calls)
                self.assertIsNone(self.adapter.put_batch(keys, values))
                self.assertEqual(self.adapter.get_batch(keys), values)
                self.assertEqual(self.adapter.exists_batch(keys), [True] * count)
                calls = self.native.calls[start:]
                self.assertEqual([call[0] for call in calls], ["put_batch", "get_batch", "batch_is_exist"])
                self.assertIs(calls[0][1], keys)
                self.assertIs(calls[0][2], values)
                self.assertEqual(calls[0][3].replica_num, 1)
                self.assertEqual(calls[0][3].nof_replica_num, 0)
                self.assertEqual(calls[0][3].dfs_replica_num, 0)

    def test_explicit_upsert_dispatches_native_upsert_once(self):
        adapter = batch_mooncake.new_adapter({"write_mode": "upsert", "value_size": 1})
        native = adapter.store
        adapter.put_batch(["key"], [b"a"])
        adapter.put_batch(["key"], [b"b"])
        self.assertEqual([call[0] for call in native.calls], ["setup", "upsert_batch", "upsert_batch"])
        self.assertEqual(native.data["key"], b"b")
        self.assertEqual(adapter.metadata["native_api"]["put_batch"], "upsert_batch")

    def test_metadata_keeps_native_semantic_and_accounting_limits(self):
        metadata = self.adapter.metadata
        self.assertTrue(metadata["validation_in_api_timing"])
        self.assertEqual(metadata["native_batch_calls_per_adapter_call"], 1)
        self.assertEqual(metadata["native_semantics"]["write_existing_key"], "success_without_rewrite")
        self.assertIn("unknown", metadata["native_semantics"]["write_failure_completed_keys"])
        self.assertFalse(metadata["native_semantics"]["python_single_key_loop"])
        self.assertIsNone(metadata["native_semantics"]["native_max_batch_keys"])

    def test_setup_has_no_client_store_and_uses_tcp(self):
        args = self.native.calls[0][1]
        self.assertEqual(args[1:6], ("P2PHANDSHAKE", 0, 128 << 20, "tcp", ""))

    def test_write_failure_preserves_unknown_partial_success_without_retry(self):
        self.native.write_status = -705
        with self.assertRaises(batch_mooncake.BatchOperationError) as caught:
            self.adapter.put_batch(["a", "b"], [bytes(1024)] * 2)
        self.assertEqual(caught.exception.details["native_status"], -705)
        self.assertIsNone(caught.exception.details["completed_keys"])
        self.assertEqual([call[0] for call in self.native.calls], ["setup", "put_batch"])

    def test_noninteger_or_nonzero_write_status_is_not_success(self):
        for status in [False, None, [], 3, -1]:
            with self.subTest(status=status):
                self.native.write_status = status
                with self.assertRaises(batch_mooncake.BatchOperationError):
                    self.adapter.put_batch(["key"], [bytes(1024)])

    def test_missing_get_and_read_error_are_not_followed_by_exists_queries(self):
        self.native.get_result = [bytes(1024), b""]
        with self.assertRaises(batch_mooncake.BatchOperationError) as caught:
            self.adapter.get_batch(["hit", "missing-or-error"])
        self.assertEqual(caught.exception.details["invalid_indices"], [1])
        self.assertEqual([call[0] for call in self.native.calls], ["setup", "get_batch"])

    def test_get_malformed_shape_type_or_size_rejected(self):
        for result in [[], [bytes(1024)] * 2, ["string"], [None], [bytes(1023)], (bytes(1024),)]:
            with self.subTest(result_type=type(result).__name__, count=len(result)):
                self.native.get_result = result
                with self.assertRaises(batch_mooncake.BatchOperationError):
                    self.adapter.get_batch(["key"])

    def test_exists_preserves_hits_misses_in_original_order(self):
        self.native.exist_result = [1, 0, 1, 0]
        self.assertEqual(self.adapter.exists_batch(["a", "b", "c", "d"]), [True, False, True, False])

    def test_exists_every_nonzero_one_native_code_is_an_error(self):
        for code in [-1, -706, 2, True, None, 1.0]:
            with self.subTest(code=code):
                self.native.exist_result = [1, code]
                with self.assertRaises(batch_mooncake.BatchOperationError) as caught:
                    self.adapter.exists_batch(["a", "b"])
                self.assertEqual(caught.exception.details["error_count"], 1)
                self.assertEqual(caught.exception.details["errors"], [(1, code)])

    def test_exists_shape_mismatch_rejected(self):
        self.native.exist_result = [1]
        with self.assertRaises(batch_mooncake.BatchOperationError):
            self.adapter.exists_batch(["a", "b"])

    def test_invalid_keys_values_and_duplicate_keys_make_no_native_call(self):
        start = len(self.native.calls)
        for keys in [[], "key", [""], ["a", "a"], [1], [None]]:
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.adapter.get_batch(keys)
        for values in [[], "value", [b""], [bytearray(1024)], [bytes(1023)], [bytes(1024)] * 2]:
            with self.subTest(values_type=type(values).__name__), self.assertRaises(ValueError):
                self.adapter.put_batch(["key"], values)
        self.assertEqual(len(self.native.calls), start)

    def test_tuple_inputs_are_supported_without_scalar_fallback(self):
        self.adapter.put_batch(("a", "b"), (bytes(1024), bytes(1024)))
        self.assertEqual(self.adapter.get_batch(("b", "a")), [bytes(1024)] * 2)
        self.assertIsInstance(self.native.calls[1][1], list)

    def test_tcp_guards_and_invalid_configuration_fail_before_setup(self):
        count = len(FakeStore.instances)
        for name in ["MC_STORE_MEMCPY", "MC_STORE_LOCAL_HOT_CACHE_SIZE"]:
            with self.subTest(name=name), patch.dict(os.environ, {name: "1"}), self.assertRaises(ValueError):
                batch_mooncake.new_adapter({})
        for config in [{"write_mode": "overwrite"}, {"value_size": 0}, {"value_size": True}]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                batch_mooncake.new_adapter(config)
        self.assertEqual(len(FakeStore.instances), count)

    def test_missing_native_batch_method_is_not_implemented_not_looped(self):
        with patch.object(FakeStore, "put_batch", None), self.assertRaises(NotImplementedError):
            batch_mooncake.new_adapter({})

    def test_close_is_idempotent_and_future_operations_fail(self):
        self.adapter.close()
        self.adapter.close()
        self.assertEqual([call[0] for call in self.native.calls], ["setup", "close"])
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.adapter.exists_batch(["key"])

    def test_close_error_is_not_masked_as_success(self):
        self.native.close_status = -1
        with self.assertRaises(batch_mooncake.BatchOperationError):
            self.adapter.close()
        self.assertIsNone(self.adapter.store)


if __name__ == "__main__":
    unittest.main()
