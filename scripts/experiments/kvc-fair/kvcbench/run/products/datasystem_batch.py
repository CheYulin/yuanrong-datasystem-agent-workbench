"""Native rc44 multi-key bytes APIs; no scalar-call or batch-splitting fallback."""

import importlib.util
from pathlib import Path


SOURCE_TAG = "v0.9.2.rc44"
SOURCE_SHA = "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6"


def _load_client(config):
    path = Path(config["scalar_adapter"])
    if not path.is_absolute() or not path.is_file():
        raise ValueError("scalar_adapter must be an existing absolute file path")
    spec = importlib.util.spec_from_file_location("_kvc_batch_ds_scalar", path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the pinned DataSystem scalar adapter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.new_adapter(config), module.WriteMode.NONE_L2_CACHE


def _keys(keys, limit):
    if not isinstance(keys, (list, tuple)) or not 1 <= len(keys) <= limit:
        raise ValueError(f"keys must contain between 1 and {limit} entries")
    if any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("keys must be nonempty strings")
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate keys would change the requested batch cardinality")
    return keys if isinstance(keys, list) else list(keys)


class BatchWriteError(RuntimeError):
    def __init__(self, failed_keys, requested_count):
        self.failed_keys = tuple(failed_keys)
        self.requested_count = requested_count
        super().__init__(f"DataSystem MSet failed for {len(self.failed_keys)}/{requested_count} keys: "
                         + repr(self.failed_keys[:8]))


class BatchAdapter:
    def __init__(self, config):
        self.size = int(config["value_size"])
        if self.size <= 0:
            raise ValueError("value_size must be positive")
        self.owner, self.write_mode = _load_client(config)
        self.client = self.owner.client
        self.metadata = {
            "product": "DataSystem",
            "tag": SOURCE_TAG,
            "source_sha": SOURCE_SHA,
            "value_size": self.size,
            "write_mode": "upsert",
            "capabilities": {"put_batch": True, "get_batch": True, "exists_batch": True},
            "native_semantics": {"write_existing_key": "insert_or_update"},
            "native_batch": True,
            "sdk_calls_per_batch": 1,
            "public_apis": {"put": "KVClient.mset", "get": "KVClient.get", "exists": "KVClient.exist"},
            "max_batch_keys": {"put": 10000, "get": 10000, "exists": 100000},
            "put_semantics": "nontransactional upsert; NONE_L2_CACHE, TTL=0, ExistenceOpt.NONE",
            "partial_batch_policy": "raise without adapter retry; successful items may already be committed",
            "get_semantics": "ordered complete Python bytes; any missing or malformed item fails the batch",
            "exists_semantics": "ordered booleans; transport errors propagate",
            "get_native_chunks": {"max_keys_per_worker_chunk": 1024, "max_expected_bytes": 100 * 1024 * 1024},
            "transport_requirement": "TCP worker configuration and separate protocol gate required",
            "enable_local_cache": False,
            "validation_in_api_timing": True,
            "scalar_fallback": False,
            "adapter_batch_splitting": False,
        }

    def put_batch(self, keys, values):
        keys = _keys(keys, 10000)
        if not isinstance(values, (list, tuple)) or len(values) != len(keys):
            raise ValueError("values must contain one bytes value per key")
        if any(not isinstance(value, bytes) or len(value) != self.size for value in values):
            raise ValueError(f"Each value must contain exactly {self.size} bytes")
        values = values if isinstance(values, list) else list(values)
        failed = self.client.mset(keys, values, write_mode=self.write_mode, ttl_second=0)
        if not isinstance(failed, list):
            raise RuntimeError("DataSystem MSet returned an invalid failure list")
        if failed:
            raise BatchWriteError(failed, len(keys))

    def get_batch(self, keys):
        keys = _keys(keys, 10000)
        values = self.client.get(keys, convert_to_str=False, sub_timeout_ms=0)
        if not isinstance(values, list) or len(values) != len(keys):
            raise RuntimeError("DataSystem Get returned an incomplete batch")
        if any(not isinstance(value, bytes) or len(value) != self.size for value in values):
            raise RuntimeError("DataSystem Get returned missing or incomplete bytes")
        return values

    def exists_batch(self, keys):
        keys = _keys(keys, 100000)
        exists = self.client.exist(keys)
        if not isinstance(exists, list) or len(exists) != len(keys) or any(type(value) is not bool for value in exists):
            raise RuntimeError("DataSystem Exist returned an invalid boolean batch")
        return exists

    def close(self):
        try:
            self.owner.close()
        finally:
            self.client = None


def new_adapter(config):
    return BatchAdapter(config)
