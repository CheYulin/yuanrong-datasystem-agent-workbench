"""Offline adapter mechanics only; these fixtures are not LMCache measurements."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kvcbench.run.products import lmcache_batch as subject
class Future:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def result(self, timeout):
        if timeout <= 0:
            raise AssertionError("Nonpositive RPC timeout")
        if self.error:
            raise self.error
        return self.value


@dataclass(frozen=True)
class ObjectKey:
    chunk_hash: bytes
    model_name: str
    kv_rank: int
    object_group_id: int
    cache_salt: str


class FakeHasher:
    def __init__(self, chunk_size, hash_algorithm):
        if (chunk_size, hash_algorithm) != (1, "blake3"):
            raise AssertionError("Unexpected fixture hasher configuration")

    def compute_chunk_hashes(self, tokens):
        if tokens != [1]:
            raise AssertionError("Expected scalar single-token identity")
        return [b"fixture-single-token-hash"]


def object_keys(ipc, hashes, groups):
    return [[ObjectKey(h, ipc.model_name, 42, group, ipc.cache_salt) for h in hashes]
            for group in groups]


class FakeClient:
    def __init__(self):
        self.calls = []
        self.chunk_size = 1
        self.hit_salts = set()
        self.locked = set()
        self.query_result = "normal"
        self.unlock_error = None
        self.query_error = None
        self.task = 3
        self.last_objects = []

    def get_chunk_size(self):
        return Future(self.chunk_size)

    def p2p_lookup_and_lock(self, objects, layouts):
        self.calls.append(("lookup", objects[:], layouts))
        self.last_objects = objects[:]
        self.locked = {obj for obj in objects if obj.cache_salt in self.hit_salts}
        return Future(self.task)

    def p2p_query_lookup_results(self, task):
        self.calls.append(("query", task))
        if self.query_error:
            return Future(error=self.query_error)
        if self.query_result != "normal":
            return Future(self.query_result)
        return Future([SimpleNamespace(offset=i * 4096 if obj in self.locked else -1,
                                       size=1024 if obj in self.locked else 0)
                       for i, obj in enumerate(self.last_objects)])

    def p2p_unlock_objects(self, objects):
        self.calls.append(("unlock", objects[:]))
        if self.unlock_error:
            return Future(error=self.unlock_error)
        self.locked.difference_update(objects)
        return Future()


class FakeScalar:
    def __init__(self, config):
        self.size = config["value_size"]
        self.endpoint = config.get("endpoints", ["tcp://127.0.0.1:1", "tcp://127.0.0.1:2"])[
            config.get("client_index", 0) % 2]
        self.client = FakeClient()
        self.transfer = SimpleNamespace(layout_desc="fixture-layout")
        self.data = {}
        self.io_calls = []
        self.close_count = 0

    def _key(self, key):
        return SimpleNamespace(world_size=1, worker_id=0, token_ids=(1,), start=0, end=1,
                               model_name=f"bytes-{self.size}", cache_salt=hashlib.sha256(key.encode()).hexdigest())

    def put(self, key, value):
        self.io_calls.append(("put", key, value))
        self.data.setdefault(key, value)

    def get(self, key):
        self.io_calls.append(("get", key))
        return self.data[key]

    def close(self):
        self.close_count += 1


class BatchLMCacheTests(unittest.TestCase):
    def setUp(self):
        self.module = SimpleNamespace(new_adapter=FakeScalar, LMCacheTCPAdapter=FakeScalar)
        self.load = patch.object(subject, "_load_scalar", return_value=self.module)
        self.native = patch.object(subject, "_native_types", return_value=(FakeHasher, object_keys))
        self.load.start()
        self.native.start()
        self.addCleanup(self.load.stop)
        self.addCleanup(self.native.stop)

    def adapter(self, **kwargs):
        config = {"scalar_adapter": "/fixture/adapter_lmcache_tcp.py", "value_size": 1024, **kwargs}
        adapter = subject.new_adapter(config)
        self.addCleanup(adapter.close)
        return adapter

    def test_scalar_data_io_and_no_overwrite_claim(self):
        adapter = self.adapter()
        value = bytes(range(256)) * 4
        self.assertIsNone(adapter.put_batch(["a"], [value]))
        self.assertEqual(adapter.get_batch(["a"]), [value])
        self.assertEqual(len(adapter.scalar.io_calls), 2)
        self.assertEqual(adapter.metadata()["native_semantics"]["write_existing_key"], "success_without_rewrite")

    def test_multi_put_get_rejected_before_scalar_io(self):
        adapter = self.adapter()
        for batch in (16, 256, 4096):
            keys = [str(i) for i in range(batch)]
            for operation, args in ((adapter.put_batch, (keys, [b"x" * 1024] * batch)),
                                    (adapter.get_batch, (keys,))):
                with self.assertRaises(subject.UnsupportedBatchOperation):
                    operation(*args)
        self.assertEqual(adapter.scalar.io_calls, [])
        self.assertEqual(adapter.scalar.client.calls, [])

    def test_overwrite_mode_unsupported_even_single_key(self):
        adapter = self.adapter(put_mode="overwrite")
        with self.assertRaises(subject.UnsupportedBatchOperation):
            adapter.put_batch(["a"], [b"x" * 1024])
        self.assertEqual(adapter.capabilities["put"]["supported_batch_keys"], [])
        self.assertEqual(adapter.scalar.io_calls, [])

    def test_all_four_batch_sizes_one_native_list_lookup(self):
        for batch in (1, 16, 256, 4096):
            with self.subTest(batch=batch):
                adapter = self.adapter()
                keys = [f"key-{i}" for i in range(batch)]
                adapter.scalar.client.hit_salts = {hashlib.sha256(k.encode()).hexdigest() for k in keys[::2]}
                self.assertEqual(adapter.exists_batch(keys), [i % 2 == 0 for i in range(batch)])
                calls = adapter.scalar.client.calls
                self.assertEqual([c[0] for c in calls], ["lookup", "query", "unlock"])
                self.assertEqual(len(calls[0][1]), batch)
                self.assertEqual(len(calls[-1][1]), (batch + 1) // 2)
                self.assertEqual(adapter.scalar.client.locked, set())

    def test_all_miss_does_not_unlock_unowned_keys(self):
        adapter = self.adapter()
        self.assertEqual(adapter.exists_batch(["a", "b"]), [False, False])
        self.assertEqual([c[0] for c in adapter.scalar.client.calls], ["lookup", "query"])

    def test_order_subset_and_new_instance_keep_key_identity(self):
        first, second = self.adapter(), self.adapter()
        first.exists_batch(["a", "b", "c"])
        identities = {obj.cache_salt: obj for obj in first.scalar.client.last_objects}
        second.exists_batch(["c", "a"])
        for key, obj in zip(["c", "a"], second.scalar.client.last_objects, strict=True):
            self.assertEqual(obj, identities[hashlib.sha256(key.encode()).hexdigest()])
            self.assertEqual(obj.kv_rank, 42)

    def test_malformed_count_cleanup_then_fatal(self):
        adapter = self.adapter()
        adapter.scalar.client.hit_salts.add(hashlib.sha256(b"a").hexdigest())
        adapter.scalar.client.query_result = []
        with self.assertRaisesRegex(RuntimeError, "count"):
            adapter.exists_batch(["a"])
        self.assertEqual(adapter.scalar.client.locked, set())
        with self.assertRaisesRegex(RuntimeError, "closed or failed"):
            adapter.exists_batch(["a"])

    def test_bad_size_releases_recognized_hits(self):
        adapter = self.adapter()
        adapter.scalar.client.hit_salts.add(hashlib.sha256(b"a").hexdigest())
        adapter.scalar.client.query_result = [SimpleNamespace(offset=0, size=999)]
        with self.assertRaisesRegex(RuntimeError, "object size"):
            adapter.exists_batch(["a"])
        self.assertEqual(adapter.scalar.client.locked, set())

    def test_malformed_address_fallback_releases_all(self):
        adapter = self.adapter()
        adapter.scalar.client.hit_salts.add(hashlib.sha256(b"a").hexdigest())
        adapter.scalar.client.query_result = [SimpleNamespace(offset=True, size=1024)]
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            adapter.exists_batch(["a"])
        self.assertEqual(adapter.scalar.client.locked, set())

    def test_timeout_is_not_miss_and_cleanup_occurs(self):
        adapter = self.adapter()
        adapter.scalar.client.hit_salts.add(hashlib.sha256(b"a").hexdigest())
        adapter.scalar.client.query_error = TimeoutError("fixture transport failure")
        with self.assertRaises(TimeoutError):
            adapter.exists_batch(["a"])
        self.assertEqual(adapter.scalar.client.locked, set())

    def test_cleanup_failure_does_not_return_success(self):
        adapter = self.adapter()
        adapter.scalar.client.hit_salts.add(hashlib.sha256(b"a").hexdigest())
        adapter.scalar.client.unlock_error = RuntimeError("fixture unlock failure")
        with self.assertRaisesRegex(RuntimeError, "unlock failure"):
            adapter.exists_batch(["a"])
        self.assertTrue(adapter.poisoned)

    def test_primary_error_preserved_with_cleanup_error_note(self):
        adapter = self.adapter()
        adapter.scalar.client.query_error = TimeoutError("primary fixture")
        adapter.scalar.client.unlock_error = RuntimeError("cleanup fixture")
        with self.assertRaisesRegex(TimeoutError, "primary fixture") as caught:
            adapter.exists_batch(["a"])
        self.assertIn("cleanup fixture", caught.exception.__notes__[0])

    def test_invalid_and_duplicate_keys_no_rpc(self):
        adapter = self.adapter()
        for keys in ([], "a", [""], [1], ["a", "a"]):
            with self.assertRaises(ValueError):
                adapter.exists_batch(keys)
        self.assertEqual(adapter.scalar.client.calls, [])

    def test_invalid_bytes_no_scalar_put(self):
        adapter = self.adapter()
        for values in ([], [b"short"], [bytearray(1024)], b"x" * 1024):
            with self.assertRaises(ValueError):
                adapter.put_batch(["a"], values)
        self.assertEqual(adapter.scalar.io_calls, [])

    def test_wrong_scalar_mapping_no_native_rpc(self):
        adapter = self.adapter()
        with patch.object(adapter.scalar, "_key", return_value=SimpleNamespace(
                world_size=1, worker_id=0, token_ids=(2,), start=0, end=1)):
            with self.assertRaisesRegex(RuntimeError, "key layout"):
                adapter.exists_batch(["a"])
        self.assertEqual(adapter.scalar.client.calls, [])

    def test_close_idempotent(self):
        adapter = self.adapter()
        adapter.close()
        adapter.close()
        self.assertEqual(adapter.scalar.close_count, 1)
        with self.assertRaises(RuntimeError):
            adapter.get_batch(["a"])

    def test_exists_larger_than_experiment_limit_rejected_before_rpc(self):
        adapter = self.adapter()
        with self.assertRaises(subject.UnsupportedBatchOperation):
            adapter.exists_batch([str(i) for i in range(4097)])
        self.assertEqual(adapter.scalar.client.calls, [])

    def test_missing_scalar_get_remains_error(self):
        adapter = self.adapter()
        with self.assertRaises(KeyError):
            adapter.get_batch(["missing"])

    def test_invalid_task_identifier_still_releases_and_aborts(self):
        adapter = self.adapter()
        adapter.scalar.client.hit_salts.add(hashlib.sha256(b"a").hexdigest())
        adapter.scalar.client.task = True
        with self.assertRaisesRegex(RuntimeError, "task id"):
            adapter.exists_batch(["a"])
        self.assertEqual(adapter.scalar.client.locked, set())

    def test_client_affinity_and_metadata_route_are_explicit(self):
        first, second, third = self.adapter(client_index=0), self.adapter(client_index=1), self.adapter(client_index=2)
        self.assertNotEqual(first.scalar.endpoint, second.scalar.endpoint)
        self.assertEqual(first.scalar.endpoint, third.scalar.endpoint)
        metadata = first.metadata()
        self.assertIn("P2P metadata RPC", metadata["exists_route"])
        self.assertIn("differs from scalar LOOKUP", metadata["exists_scope"])
        self.assertEqual(metadata["capabilities"]["exists"]["supported_batch_range"], [1, 4096])

    def test_init_wrong_chunk_size_closes_scalar(self):
        scalar = FakeScalar({"value_size": 1024})
        scalar.client.chunk_size = 256
        self.module.new_adapter = lambda config: scalar
        with self.assertRaisesRegex(ValueError, "chunk_size"):
            self.adapter()
        self.assertEqual(scalar.close_count, 1)

    def test_loader_absolute_path_and_relative_dependency(self):
        self.load.stop()
        with self.assertRaises(ValueError):
            subject._load_scalar("relative.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture_relative_dependency.py").write_text("VALUE = 41\n")
            scalar = root / "adapter_lmcache_tcp.py"
            scalar.write_text("from fixture_relative_dependency import VALUE\nRESULT = VALUE + 1\n")
            module = subject._load_scalar(scalar)
            self.assertEqual(module.RESULT, 42)
            self.assertIs(subject._load_scalar(scalar), module)


if __name__ == "__main__":
    unittest.main()
