"""One native Mooncake data peer at an explicit logical-node endpoint."""
import argparse
import json
import signal
import threading


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--master", required=True)
    parser.add_argument("--capacity", required=True, type=int)
    args = parser.parse_args()
    from mooncake.store import MooncakeDistributedStore
    stop = threading.Event()
    for signum in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(signum, lambda *_: stop.set())
    store = MooncakeDistributedStore()
    rc = store.setup(f"{args.host}:{args.port}", "P2PHANDSHAKE", args.capacity, 64 << 20, "tcp", "", args.master)
    if rc != 0:
        raise RuntimeError("Mooncake peer setup failed: " + str(rc))
    print(json.dumps({"state": "ready", "host": args.host, "port": args.port, "capacity_bytes": args.capacity}), flush=True)
    stop.wait()
    rc = store.close()
    if rc != 0:
        raise RuntimeError("Mooncake peer close failed: " + str(rc))


if __name__ == "__main__":
    main()
