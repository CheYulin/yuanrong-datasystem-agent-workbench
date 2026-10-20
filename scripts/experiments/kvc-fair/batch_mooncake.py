"""One native Mooncake bytes batch call per adapter operation, without retries."""
import os


class BatchOperationError(RuntimeError):
    def __init__(self, operation, message, **details):
        self.operation = operation
        self.details = {"operation": operation, **details}
        super().__init__(f"Mooncake {operation}: {message}; {self.details}")


class MooncakeBatchAdapter:
    def __init__(self, config):
        mode = config.get("write_mode", "insert")
        if mode not in {"insert", "upsert"}:
            raise ValueError("write_mode must explicitly be insert or upsert")
        self.value_size = config.get("value_size")
        if self.value_size is not None and (type(self.value_size) is not int or self.value_size <= 0):
            raise ValueError("value_size must be a positive integer")
        for name in ["MC_STORE_MEMCPY", "MC_STORE_LOCAL_HOT_CACHE_SIZE"]:
            if os.environ.get(name) != "0":
                raise ValueError(name + "=0 is required for the unified TCP batch experiment")
        from mooncake.store import MooncakeDistributedStore, ReplicateConfig

        self.native_write = "put_batch" if mode == "insert" else "upsert_batch"
        self.store = MooncakeDistributedStore()
        for name in [self.native_write, "get_batch", "batch_is_exist", "close"]:
            if not callable(getattr(self.store, name, None)):
                raise NotImplementedError("This Mooncake artifact does not expose native " + name)
        self.replication = ReplicateConfig()
        self.replication.replica_num = 1
        self.replication.nof_replica_num = 0
        self.replication.dfs_replica_num = 0
        local_buffer_size = int(config.get("local_buffer_size", 128 << 20))
        self.metadata = {
            "product": "Mooncake Store", "release": "v0.3.13.post1",
            "source_sha": "719735896c86b56fabec6cf3e825fb2ea640597a",
            "transport": "tcp", "replicas": 1, "client_store_capacity": 0,
            "local_buffer_size": local_buffer_size, "value_size": self.value_size,
            "tcp_queue_env": {name: os.environ.get(name) for name in [
                "MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER", "MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER",
                "MC_TCP_ADMISSION_TIMEOUT_MS"]},
            "write_mode": mode, "validation_in_api_timing": True,
            "capabilities": {"put_batch": True, "get_batch": True, "exists_batch": True},
            "native_api": {"put_batch": self.native_write, "get_batch": "get_batch", "exists_batch": "batch_is_exist"},
            "native_batch_calls_per_adapter_call": 1,
            "native_semantics": {
                "python_single_key_loop": False, "retry": False, "synchronous": True,
                "write_existing_key": "success_without_rewrite" if mode == "insert" else "insert_or_update",
                "write_failure_completed_keys": "unknown; native bytes batch collapses per-key statuses to one code",
                "get_empty_result": "missing or read failure indistinguishable; batch raises without follow-up queries",
                "exists_result": "per-key 0/1; every other native integer is an error",
                "native_max_batch_keys": None,
                "batch_limit_scope": "No explicit key-count cap found in the examined native path; aggregate staging and RPC/server resources still limit requests",
                "wire_scope": "One native API call can issue metadata RPCs and multiple object transfers",
                "boundary": "Python bytes list -> registered staging -> TCP; get returns Python-owned bytes list",
            },
        }
        rc = self.store.setup(config.get("local_hostname", "127.0.0.1"), "P2PHANDSHAKE", 0,
                              local_buffer_size, "tcp", "",
                              config.get("master_server_addr", os.environ.get("MOONCAKE_MASTER", "127.0.0.1:18551")))
        if type(rc) is not int or rc != 0:
            raise BatchOperationError("setup", "native setup failed", native_status=rc)

    def _keys(self, keys):
        if not isinstance(keys, (list, tuple)) or not keys:
            raise ValueError("keys must be a nonempty list or tuple")
        if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("keys must be nonempty distinct strings")
        if self.store is None:
            raise RuntimeError("Mooncake batch adapter is closed")
        return keys if isinstance(keys, list) else list(keys)

    def put_batch(self, keys, values):
        keys = self._keys(keys)
        if not isinstance(values, (list, tuple)) or len(values) != len(keys):
            raise ValueError("values must be a list or tuple matching keys")
        if any(not isinstance(value, bytes) or not value or self.value_size is not None and len(value) != self.value_size for value in values):
            raise ValueError("values must be nonempty bytes matching configured value_size")
        rc = getattr(self.store, self.native_write)(keys, values, self.replication)
        if type(rc) is not int or rc != 0:
            raise BatchOperationError("put_batch", "native write failed; partial success is unknown",
                                      native_api=self.native_write, native_status=rc, requested_keys=len(keys), completed_keys=None)

    def get_batch(self, keys):
        keys = self._keys(keys)
        values = self.store.get_batch(keys)
        if not isinstance(values, list) or len(values) != len(keys):
            raise BatchOperationError("get_batch", "native result length/type mismatch", requested_keys=len(keys),
                                      returned_keys=len(values) if isinstance(values, list) else None)
        invalid = [i for i, value in enumerate(values) if not isinstance(value, bytes) or not value or self.value_size is not None and len(value) != self.value_size]
        if invalid:
            raise BatchOperationError("get_batch", "empty/invalid bytes; missing and native read errors cannot be distinguished",
                                      requested_keys=len(keys), invalid_count=len(invalid), invalid_indices=invalid[:32])
        return values

    def exists_batch(self, keys):
        keys = self._keys(keys)
        statuses = self.store.batch_is_exist(keys)
        if not isinstance(statuses, list) or len(statuses) != len(keys):
            raise BatchOperationError("exists_batch", "native result length/type mismatch", requested_keys=len(keys),
                                      returned_keys=len(statuses) if isinstance(statuses, list) else None)
        errors = [(i, code) for i, code in enumerate(statuses) if type(code) is not int or code not in (0, 1)]
        if errors:
            raise BatchOperationError("exists_batch", "native per-key error", requested_keys=len(keys),
                                      error_count=len(errors), errors=errors[:32])
        return [code == 1 for code in statuses]

    def close(self):
        if self.store is not None:
            store, self.store = self.store, None
            rc = store.close()
            if type(rc) is not int or rc != 0:
                raise BatchOperationError("close", "native close failed", native_status=rc)


def new_adapter(config):
    return MooncakeBatchAdapter(config)
