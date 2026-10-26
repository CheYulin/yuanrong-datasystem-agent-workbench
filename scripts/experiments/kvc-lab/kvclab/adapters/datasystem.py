"""Route native byte writes to their metadata owners, without a client cache."""
import os


class Adapter:
    def __init__(self, config):
        from yr.datasystem import KVClient
        from yr.datasystem.kv_client import DataPlacementPolicy
        from yr.datasystem.object_client import WriteMode

        endpoints = config["endpoints"]
        host, port = endpoints[int(config.get("client_index", 0)) % len(endpoints)].rsplit(":", 1)
        self.size = int(config["value_size"])
        self.mode = WriteMode.NONE_L2_CACHE
        os.environ["DATASYSTEM_CLIENT_LOG_DIR"] = config["log_dir"]
        self.client = KVClient(host=host, port=int(port), enable_local_cache=False,
                               enable_cross_node_connection=False,
                               data_placement_policy=DataPlacementPolicy.PREFERRED_META_OWNER,
                               connect_timeout_ms=10000, req_timeout_ms=10000,
                               fast_transport_mem_size=16 << 20)
        self.client.init()
        self.metadata = {"product": "datasystem", "native_batch": True,
                         "data_placement_policy": "PREFERRED_META_OWNER", "enable_local_cache": False,
                         "write_mode": "NONE_L2_CACHE", "sdk_calls_per_batch": 1,
                         "native_api": {"put": "mset", "get": "get", "exist": "exist"},
                         "one_hop_verified": False,
                         "one_hop_scope": "Routing policy alone does not prove runtime RPC hop count"}

    def put_batch(self, keys, values):
        self._keys(keys)
        if len(values) != len(keys) or any(type(v) is not bytes or len(v) != self.size for v in values):
            raise ValueError("One complete bytes value per key is required")
        failed = self.client.mset(keys, values, write_mode=self.mode, ttl_second=0)
        if not isinstance(failed, list) or failed:
            raise RuntimeError("Native MSet returned failed keys: " + repr(failed)[:1000])

    def get_batch(self, keys):
        self._keys(keys)
        values = self.client.get(keys, convert_to_str=False, sub_timeout_ms=0)
        if not isinstance(values, list) or len(values) != len(keys) or any(type(v) is not bytes or len(v) != self.size for v in values):
            raise RuntimeError("Native GET returned missing or incomplete bytes")
        return values

    def exists_batch(self, keys):
        self._keys(keys)
        values = self.client.exist(keys)
        if not isinstance(values, list) or len(values) != len(keys) or any(type(v) is not bool for v in values):
            raise RuntimeError("Native EXIST did not return one boolean per key")
        return values

    def _keys(self, keys):
        if not keys or len(keys) != len(set(keys)) or any(type(k) is not str or not k for k in keys):
            raise ValueError("Distinct nonempty keys are required")

    def close(self):
        self.client = None


def new_adapter(config):
    return Adapter(config)
