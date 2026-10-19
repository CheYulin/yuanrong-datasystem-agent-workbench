"""Synchronous bytes API for the pinned, source-built Mooncake Store."""

import os


class MooncakeAdapter:
    def __init__(self, config):
        from mooncake.store import MooncakeDistributedStore, ReplicateConfig

        self.store = MooncakeDistributedStore()
        self.replication = ReplicateConfig()
        self.replication.replica_num = 1
        self.replication.nof_replica_num = 0
        self.replication.dfs_replica_num = 0
        self.metadata = {
            "product": "Mooncake Store",
            "release": "v0.3.13.post1",
            "source_sha": "719735896c86b56fabec6cf3e825fb2ea640597a",
            "transport": "tcp",
            "replicas": 1,
            "client_store_capacity": 0,
            "store_checksum": False,
            "boundary": "Python bytes -> registered staging -> TCP -> dedicated store; get returns Python-owned bytes",
        }
        rc = self.store.setup(
            config.get("local_hostname", "127.0.0.1"),
            "P2PHANDSHAKE",
            0,
            int(config.get("local_buffer_size", 128 * 1024 * 1024)),
            "tcp",
            "",
            config.get("master_server_addr", os.getenv("MOONCAKE_MASTER", "127.0.0.1:18551")),
        )
        if rc != 0:
            raise RuntimeError(f"Mooncake setup failed: {rc}")

    def put(self, key, value):
        if not isinstance(value, bytes) or not value:
            raise ValueError("This comparison requires nonempty Python bytes")
        rc = self.store.put(key, value, self.replication)
        if rc != 0:
            raise RuntimeError(f"Mooncake put failed: {rc}")

    def get(self, key):
        value = self.store.get(key)
        if value:
            return value
        if not self.exists(key):
            raise KeyError(key)
        raise RuntimeError("Mooncake get returned empty payload for an existing nonempty key")

    def exists(self, key):
        rc = self.store.is_exist(key)
        if rc not in (0, 1):
            raise RuntimeError(f"Mooncake is_exist failed: {rc}")
        return rc == 1

    def delete(self, key):
        rc = self.store.remove(key, True)
        if rc != 0:
            raise RuntimeError(f"Mooncake remove failed: {rc}")

    def close(self):
        if self.store is not None:
            rc = self.store.close()
            self.store = None
            if rc != 0:
                raise RuntimeError(f"Mooncake close failed: {rc}")


def new_adapter(config):
    return MooncakeAdapter(config)
