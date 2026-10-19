#!/usr/bin/env python3
import argparse
import json
import signal
import threading

from mooncake.store import MooncakeDistributedStore

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True, type=int)
parser.add_argument("--master", default="127.0.0.1:18551")
parser.add_argument("--capacity", type=int, default=4 * 1024**3)
args = parser.parse_args()
stop = threading.Event()
for signum in (signal.SIGTERM, signal.SIGINT):
    signal.signal(signum, lambda *_: stop.set())
store = MooncakeDistributedStore()
rc = store.setup(f"127.0.0.1:{args.port}", "P2PHANDSHAKE", args.capacity,
                 128 * 1024**2, "tcp", "", args.master)
if rc != 0:
    raise RuntimeError(f"Store peer setup failed: {rc}")
print(json.dumps({"state": "ready", "port": args.port,
                  "capacity_bytes": args.capacity, "hostname": store.get_hostname()}), flush=True)
stop.wait()
rc = store.close()
if rc != 0:
    raise RuntimeError(f"Store peer close failed: {rc}")
