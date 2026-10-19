"""Fresh-key bytes transactions through LMCache's native MP TCP/pickle path."""
from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import sys
import uuid

_DIRECTORY = str(Path(__file__).resolve().parent)
if _DIRECTORY not in sys.path:
    sys.path.insert(0, _DIRECTORY)

from adapter_lmcache import LMCacheAdapter
import torch
import zmq

from lmcache.v1.distributed.api import MemoryLayoutDesc
from lmcache.v1.multiprocess.custom_types import RegisterEngineDrivenContextPayload
from lmcache.v1.multiprocess.transfer_context.base import (
    EngineDrivenContextMetadata,
    create_engine_driven_context,
)
from lmcache.v1.multiprocess.transfer_context.pickle import EngineDrivenContextPickle
from lmcache.v1.multiprocess.transport.factory import RequestClientFactory


class LMCacheTCPAdapter(LMCacheAdapter):
    def __init__(self, config):
        self.size = int(config["value_size"])
        self.timeout = float(config.get("timeout", 20))
        self.index = int(config.get("client_index", 0))
        if self.size <= 0 or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("value_size and timeout must be positive")
        endpoints = config.get("endpoints", ["tcp://127.0.0.1:15550", "tcp://127.0.0.1:15551"])
        if not isinstance(endpoints, (list, tuple)) or not endpoints or any(
            not isinstance(endpoint, str) or not endpoint.startswith("tcp://") for endpoint in endpoints
        ):
            raise ValueError("LMCache TCP adapter requires explicit tcp:// endpoints")
        self.endpoint = endpoints[self.index % len(endpoints)]
        self.model = f"bytes-{self.size}"
        self.instance_id = uuid.uuid4().int & ((1 << 63) - 1)
        self.session_id = f"bytes-{os.getpid()}-{self.instance_id}"
        self.zctx = zmq.Context()
        self.client = None
        try:
            self.client = RequestClientFactory.create(self.endpoint, context=self.zctx)
            response = self.client.register_kv_cache_engine_driven_context(
                RegisterEngineDrivenContextPayload(
                    instance_id=self.instance_id,
                    model_name=self.model,
                    world_size=1,
                    block_size=1,
                    num_layers=1,
                    hidden_dim_size=self.size,
                    dtype_str="uint8",
                    use_mla=True,
                    num_physical_slots=1,
                )
            ).result(timeout=self.timeout)
            if response.shm_name != "" or response.pool_size != 0:
                raise RuntimeError("LMCache TCP adapter requires server SHM transport to be disabled")
            self.transfer = create_engine_driven_context(
                EngineDrivenContextMetadata(
                    MemoryLayoutDesc(shapes=[torch.Size([1, 1, self.size])], dtypes=[torch.uint8]),
                    block_size=1,
                    use_mla=True,
                ),
                self.client,
                self.timeout,
                response.shm_name,
                response.pool_size,
                use_pickle=True,
            )
            if not isinstance(self.transfer, EngineDrivenContextPickle):
                raise RuntimeError("LMCache TCP adapter requires EngineDrivenContextPickle")
        except BaseException:
            try:
                if self.client is not None:
                    try:
                        self.client.unregister_kv_cache_engine_driven_context(self.instance_id).result(timeout=self.timeout)
                    except Exception:
                        pass
                    self.client.close()
            finally:
                self.zctx.term()
            raise
        self.closed = False

    def put(self, key: str, value: bytes):
        if not isinstance(value, bytes) or len(value) != self.size:
            raise ValueError(f"Expected {self.size} bytes")
        ipc_key = self._key(key)
        prepared = self.client.prepare_store(ipc_key, self.instance_id).result(timeout=self.timeout)
        if prepared.context != {}:
            raise RuntimeError("LMCache TCP prepare_store returned an unexpected transport context")
        tensor = torch.frombuffer(bytearray(value), dtype=torch.uint8).reshape(1, 1, self.size)
        if not self.transfer.commit_store(ipc_key, self.instance_id, [tensor]):
            raise RuntimeError("LMCache TCP COMMIT_STORE failed or timed out")

    def get(self, key: str) -> bytes:
        ipc_key = self._key(key)
        if not self._lookup(ipc_key):
            raise KeyError(key)
        tensors = None
        try:
            tensors = self.transfer.prepare_retrieve(ipc_key, self.instance_id)
            if tensors is None or len(tensors) != 1:
                raise RuntimeError("LMCache TCP PREPARE_RETRIEVE failed after a lookup hit")
            tensor = tensors[0]
            if tensor.numel() != self.size or tensor.dtype != torch.uint8 or tensor.device.type != "cpu":
                raise RuntimeError("LMCache TCP PREPARE_RETRIEVE returned an unexpected CPU tensor")
            return ctypes.string_at(tensor.data_ptr(), self.size)
        finally:
            if tensors is None:
                self.client.free_lookup_locks(ipc_key.no_worker_id_version(), 1).result(timeout=self.timeout)
            else:
                del tensors
                committed = self.client.commit_retrieve(ipc_key, self.instance_id).result(timeout=self.timeout)
                if committed is not True:
                    raise RuntimeError("LMCache TCP COMMIT_RETRIEVE returned false")


def new_adapter(config):
    return LMCacheTCPAdapter(config)
