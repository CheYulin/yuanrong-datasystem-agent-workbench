"""Synchronous bytes adapter to the unmodified LMCache 0.5.6 MP server."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import sys
import uuid

_SOURCE = Path(__file__).resolve().parent / "source"
if (_SOURCE / "lmcache" / "__init__.py").exists():
    _BENCH_ROOT = _SOURCE.parent.parent
    sys.path[:] = [entry for entry in sys.path if Path(entry or os.getcwd()).resolve() != _BENCH_ROOT]
    sys.path.insert(0, str(_SOURCE))

import torch
import zmq

from lmcache.v1.distributed.api import MemoryLayoutDesc
from lmcache.v1.multiprocess.custom_types import (
    IPCCacheServerKey,
    RegisterEngineDrivenContextPayload,
)
from lmcache.v1.multiprocess.transfer_context.base import (
    EngineDrivenContextMetadata,
    create_engine_driven_context,
)
from lmcache.v1.multiprocess.transfer_context.shm import EngineDrivenContextShm
from lmcache.v1.multiprocess.transport.factory import RequestClientFactory


class LMCacheAdapter:
    def __init__(self, config):
        self.size = int(config["value_size"])
        self.timeout = float(config.get("timeout", 20))
        self.index = int(config.get("client_index", 0))
        endpoints = config.get("endpoints", ["tcp://127.0.0.1:15550", "tcp://127.0.0.1:15551"])
        self.endpoint = endpoints[self.index % len(endpoints)]
        self.model = f"bytes-{self.size}"
        self.instance_id = uuid.uuid4().int & ((1 << 63) - 1)
        self.session_id = f"bytes-{os.getpid()}-{self.instance_id}"
        self.zctx = zmq.Context()
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
        )
        if not isinstance(self.transfer, EngineDrivenContextShm):
            raise RuntimeError("Required LMCache SHM transport unavailable; refusing a silent pickle fallback")
        self.closed = False

    def _key(self, key):
        return IPCCacheServerKey(
            model_name=self.model,
            world_size=1,
            worker_id=0,
            token_ids=(1,),
            start=0,
            end=1,
            request_id=self.session_id,
            cache_salt=hashlib.sha256(key.encode()).hexdigest(),
            num_kv_readers=1,
        )

    def put(self, key: str, value: bytes):
        if len(value) != self.size:
            raise ValueError(f"Expected {self.size} bytes, got {len(value)}")
        ipc_key = self._key(key)
        result = self.transfer.prepare_store(ipc_key, self.instance_id)
        if result is None:
            raise RuntimeError("LMCache prepare_store unexpectedly requested pickle transfer")
        tensors, indices = result
        if not tensors:
            raise FileExistsError(key)
        if indices != [0] or len(tensors) != 1 or tensors[0].numel() != self.size:
            raise RuntimeError("LMCache returned an unexpected SHM slot layout")
        ctypes.memmove(tensors[0].data_ptr(), value, self.size)
        del tensors
        if not self.transfer.commit_store(ipc_key, self.instance_id, []):
            raise RuntimeError("LMCache COMMIT_STORE returned false")

    def _lookup(self, ipc_key):
        self.client.lookup(ipc_key.no_worker_id_version(), 1).result(timeout=self.timeout)
        hits = self.client.wait_prefetch_status(self.session_id, self.timeout).result(timeout=self.timeout + 1)
        if hits is None:
            raise TimeoutError("LMCache LOOKUP prefetch did not finish within the timeout")
        if hits not in (0, 1):
            raise RuntimeError(f"Unexpected LMCache single-chunk hit count {hits}")
        return hits == 1

    def get(self, key: str) -> bytes:
        ipc_key = self._key(key)
        if not self._lookup(ipc_key):
            raise KeyError(key)
        tensors = None
        try:
            tensors = self.transfer.prepare_retrieve(ipc_key, self.instance_id)
            if tensors is None or len(tensors) != 1 or tensors[0].numel() != self.size:
                raise RuntimeError("LMCache PREPARE_RETRIEVE failed after a lookup hit")
            return ctypes.string_at(tensors[0].data_ptr(), self.size)
        finally:
            if tensors is None:
                self.client.free_lookup_locks(ipc_key.no_worker_id_version(), 1).result(timeout=self.timeout)
            else:
                del tensors
                if not self.transfer.commit_retrieve(ipc_key, self.instance_id):
                    raise RuntimeError("LMCache COMMIT_RETRIEVE returned false")

    def exists(self, key: str) -> bool:
        ipc_key = self._key(key)
        hit = self._lookup(ipc_key)
        if hit:
            self.client.free_lookup_locks(ipc_key.no_worker_id_version(), 1).result(timeout=self.timeout)
        return hit

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.client.end_session(self.session_id).result(timeout=self.timeout)
            self.client.unregister_kv_cache_engine_driven_context(self.instance_id).result(timeout=self.timeout)
        finally:
            self.transfer.close()
            self.client.close()
            self.zctx.term()


def new_adapter(config):
    return LMCacheAdapter(config)
