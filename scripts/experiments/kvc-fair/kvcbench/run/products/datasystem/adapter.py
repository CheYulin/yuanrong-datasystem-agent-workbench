"""Synchronous bytes adapter for the pinned DataSystem Python SDK."""

import os

from yr.datasystem import KVClient
from yr.datasystem.kv_client import DataPlacementPolicy
from yr.datasystem.object_client import WriteMode


class Adapter:
    def __init__(self, config):
        if config.get("log_dir"):
            os.environ["DATASYSTEM_CLIENT_LOG_DIR"] = str(config["log_dir"])
        os.environ.setdefault("DATASYSTEM_LOG_MONITOR_ENABLE", "false")
        os.environ.setdefault("DATASYSTEM_MIN_LOG_LEVEL", "2")
        endpoints = config.get("endpoints", ["127.0.0.1:31531", "127.0.0.1:31532"])
        index = int(config.get("worker_index", config.get("client_index", 0))) % len(endpoints)
        host, port = endpoints[index].rsplit(":", 1)
        self.client = KVClient(
            host=host,
            port=int(port),
            connect_timeout_ms=int(config.get("timeout_ms", 10000)),
            req_timeout_ms=int(config.get("timeout_ms", 10000)),
            enable_local_cache=False,
            enable_cross_node_connection=False,
            data_placement_policy=DataPlacementPolicy.REQUIRED_SAME_NODE,
        )
        self.client.init()

    def put(self, key: str, value: bytes):
        self.client.set(key, value, write_mode=WriteMode.NONE_L2_CACHE, ttl_second=0)

    def get(self, key: str) -> bytes:
        try:
            value = self.client.get([key], convert_to_str=False, sub_timeout_ms=0)[0]
        except RuntimeError as error:
            if str(error).startswith("code: [Key not found],"):
                raise KeyError(key) from error
            raise
        if value is None:
            raise KeyError(key)
        return value

    def exists(self, key: str) -> bool:
        try:
            return bool(self.client.exist([key])[0])
        except RuntimeError as error:
            if str(error).startswith("code: [Key not found],"):
                return False
            raise

    def delete(self, key: str):
        failed = self.client.delete([key])
        if failed:
            raise RuntimeError("DataSystem delete failed: " + repr(failed))

    def close(self):
        self.client = None


def new_adapter(config):
    return Adapter(config)
