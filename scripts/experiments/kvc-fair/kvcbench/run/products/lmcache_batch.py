"""LMCache 0.5.6 native sparse batch metadata lookup; scalar TCP data I/O."""
from __future__ import annotations

import hashlib
import importlib.util
import math
from pathlib import Path
import sys
import time


class UnsupportedBatchOperation(NotImplementedError):
    def __init__(self, operation, batch_size, reason):
        self.operation = operation
        self.batch_size = batch_size
        super().__init__(f"LMCache {operation} B={batch_size} unsupported: {reason}")


def _load_scalar(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("scalar_adapter must be an absolute existing adapter path")
    parent = str(path.resolve().parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    name = "_batch_lmcache_scalar_" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load scalar adapter: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return module


def _native_types():
    from lmcache.v1.distributed.api import ipc_key_to_object_keys
    from lmcache.v1.multiprocess.token_hasher import TokenHasher

    return TokenHasher, ipc_key_to_object_keys


class LMCacheBatchAdapter:
    def __init__(self, config):
        self.size = int(config["value_size"])
        self.timeout = float(config.get("timeout", 20))
        self.put_mode = config.get("put_mode", "fresh")
        if self.size <= 0 or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("value_size and timeout must be positive")
        if self.put_mode not in ("fresh", "overwrite"):
            raise ValueError("put_mode must be fresh or overwrite")
        if config.get("hash_algorithm", "blake3") != "blake3":
            raise ValueError("This fixed-source mapping requires server hash_algorithm=blake3")
        self.scalar_path = Path(config["scalar_adapter"])
        module = _load_scalar(self.scalar_path)
        self.scalar = module.new_adapter(config)
        self.closed = False
        self.poisoned = False
        try:
            if not isinstance(self.scalar, module.LMCacheTCPAdapter):
                raise ValueError("scalar_adapter must construct the original LMCacheTCPAdapter")
            if not self.scalar.endpoint.startswith("tcp://"):
                raise ValueError("LMCache batch requires the scalar TCP transport")
            if self.scalar.client.get_chunk_size().result(timeout=self.timeout) != 1:
                raise ValueError("LMCache batch mapping requires server chunk_size=1")
            hasher_type, self._object_keys = _native_types()
            hashes = hasher_type(chunk_size=1, hash_algorithm="blake3").compute_chunk_hashes([1])
            if len(hashes) != 1 or not isinstance(hashes[0], bytes):
                raise RuntimeError("Unexpected LMCache single-token chunk hash")
            self._chunk_hash = hashes[0]
            self._layout = self.scalar.transfer.layout_desc
        except BaseException:
            self.scalar.close()
            self.closed = True
            raise
        self.capabilities = {
            "put": {"supported_batch_keys": [1] if self.put_mode == "fresh" else [], "max_batch_keys": 1,
                    "supported_batch_range": [1, 1] if self.put_mode == "fresh" else [],
                    "fresh_key_only": True, "overwrite": False},
            "get": {"supported_batch_keys": [1], "max_batch_keys": 1, "supported_batch_range": [1, 1]},
            "exists": {"supported_batch_keys": [1, 16, 256, 4096],
                       "supported_batch_range": [1, 4096],
                       "max_batch_keys": 4096, "native_arbitrary_key_list": True, "l1_only": True},
        }

    def _keys(self, keys):
        if self.closed or self.poisoned:
            raise RuntimeError("LMCache batch adapter is closed or failed")
        if not isinstance(keys, (list, tuple)) or not keys:
            raise ValueError("keys must be a nonempty list or tuple")
        if any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("Every key must be a nonempty string")
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate keys are outside the independent-key batch contract")
        return list(keys)

    def _mapped_key(self, key):
        ipc = self.scalar._key(key)
        if (ipc.world_size, ipc.worker_id, tuple(ipc.token_ids), ipc.start, ipc.end) != (1, 0, (1,), 0, 1):
            raise RuntimeError("Scalar adapter key layout differs from the fixed batch mapping")
        if ipc.model_name != f"bytes-{self.size}" or ipc.cache_salt != hashlib.sha256(key.encode()).hexdigest():
            raise RuntimeError("Scalar adapter key namespace differs from the fixed batch mapping")
        groups = self._object_keys(ipc, [self._chunk_hash], [0])
        if len(groups) != 1 or len(groups[0]) != 1:
            raise RuntimeError("Expected one native object per independent scalar key")
        return groups[0][0]

    def put_batch(self, keys, values):
        keys = self._keys(keys)
        if len(keys) != 1 or self.put_mode != "fresh":
            raise UnsupportedBatchOperation("put", len(keys),
                                            "MP accepts one sequence, and resident keys cannot be overwritten")
        if not isinstance(values, (list, tuple)) or len(values) != len(keys):
            raise ValueError("values must contain exactly one bytes value per key")
        if not isinstance(values[0], bytes) or len(values[0]) != self.size:
            raise ValueError(f"Expected {self.size} bytes per key")
        self.scalar.put(keys[0], values[0])

    def get_batch(self, keys):
        keys = self._keys(keys)
        if len(keys) != 1:
            raise UnsupportedBatchOperation("get", len(keys),
                                            "MP retrieves one prefix-hashed sequence, not independent keys")
        value = self.scalar.get(keys[0])
        if not isinstance(value, bytes) or len(value) != self.size:
            raise RuntimeError("Scalar TCP get returned an unexpected bytes payload")
        return [value]

    def exists_batch(self, keys):
        keys = self._keys(keys)
        if len(keys) > 4096:
            raise UnsupportedBatchOperation("exists", len(keys), "this experiment bounds native requests to 4096 keys")
        objects = [self._mapped_key(key) for key in keys]
        if len(set(objects)) != len(objects):
            raise RuntimeError("Distinct scalar keys mapped to duplicate native objects")
        deadline = time.monotonic() + self.timeout
        unlock = objects
        submitted = False
        failure = None
        try:
            submitted = True
            task = self.scalar.client.p2p_lookup_and_lock(objects, {0: self._layout}).result(
                timeout=self._remaining(deadline))
            if type(task) is not int or task < 0:
                raise RuntimeError("LMCache P2P lookup returned an invalid task id")
            while True:
                addresses = self.scalar.client.p2p_query_lookup_results(task).result(
                    timeout=self._remaining(deadline))
                if addresses is not None:
                    break
                time.sleep(min(0.001, self._remaining(deadline)))
            if not isinstance(addresses, list) or len(addresses) != len(objects):
                raise RuntimeError("LMCache P2P result count differs from the independent key count")
            if any(type(getattr(address, "offset", None)) is not int or
                   type(getattr(address, "size", None)) is not int for address in addresses):
                raise RuntimeError("LMCache P2P returned malformed addresses")
            for address in addresses:
                if (address.offset < 0 and address.size != 0) or (address.offset >= 0 and address.size != self.size):
                    raise RuntimeError("LMCache P2P returned an unexpected per-key object size")
            unlock = [obj for obj, address in zip(objects, addresses, strict=True) if address.offset >= 0]
            return [address.offset >= 0 for address in addresses]
        except BaseException as error:
            self.poisoned = True
            failure = error
            raise
        finally:
            if submitted and unlock:
                try:
                    # A failed result cannot identify its hits; disjoint key ownership
                    # permits conservative release of every submitted key before abort.
                    ack = self.scalar.client.p2p_unlock_objects(unlock).result(timeout=self.timeout)
                    if ack is not None:
                        raise RuntimeError("LMCache P2P unlock returned an unexpected acknowledgement")
                except BaseException as error:
                    self.poisoned = True
                    if failure is not None:
                        failure.add_note(f"LMCache P2P lock cleanup also failed: {error!r}")
                    else:
                        raise

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("LMCache native batch metadata lookup timed out; this is not a miss")
        return remaining

    def metadata(self):
        return {
            "product": "lmcache", "source_tag": "v0.5.6",
            "source_sha": "3e11e10812b448142c6cdb177a3058652bd826fa",
            "source_identity_kind": "required pinned source; external provenance gate verifies deployed hash",
            "scalar_adapter": str(self.scalar_path), "endpoint": self.scalar.endpoint,
            "endpoint_routing": "client_index modulo endpoint count; independent server namespaces",
            "key_mapping": "official ipc_key_to_object_keys(scalar._key(key), single-token blake3 hash, [0])",
            "exists_route": "native MP P2P metadata RPC: lookup_and_lock, query_lookup_results, unlock_objects",
            "exists_scope": "L1-resident sparse independent keys; differs from scalar LOOKUP prefix route",
            "payload_transport": "scalar PUT/GET use original CPU pickle/TCP adapter; EXISTS moves metadata only",
            "lock_ownership": "distinct keys per client; synchronous L1-only lookup; finally unlock ACK included",
            "failure_policy": "timeout/malformed/cleanup error is fatal, never a cache miss; reset server before reuse",
            "put_mode": self.put_mode, "put_ack_limit": "ACK can succeed for zero reservations; only fresh bounded keys eligible",
            "native_semantics": {"write_existing_key": "success_without_rewrite",
                                 "put_get_arbitrary_independent_batch": False,
                                 "exists_result_order": "same as requested independent ObjectKey list"},
            "capabilities": self.capabilities,
        }

    def close(self):
        if not self.closed:
            self.closed = True
            self.scalar.close()


def new_adapter(config):
    return LMCacheBatchAdapter(config)
