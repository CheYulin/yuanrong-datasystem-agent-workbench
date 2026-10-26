"""Create and verify the bounded common read pool once before timed cases."""
import argparse
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import time
import traceback


def pool_values(index, size):
    return [hashlib.shake_256(f"batch-v1-c{index}-v{i}".encode()).digest(size) for i in range(4)]


def preload(index, args, connection):
    adapter = None
    try:
        from kvcbench.run.workers.scalar import load_adapter
        config = json.loads(Path(args["config"]).read_text())
        config.update(client_index=index, concurrency=args["clients"], value_size=args["size"], write_mode="insert")
        adapter = load_adapter(args["adapter"], config)
        count = args["pool_keys"] // args["clients"]
        prefix = f"batch-{args['run_id']}-c{index}-pool-"
        values = pool_values(index, args["size"])
        for start in range(0, count, 64):
            end = min(count, start + 64)
            keys = [prefix + str(i) for i in range(start, end)]
            adapter.put_batch(keys, [values[i % 4] for i in range(start, end)])
        verified = 0
        for start in range(0, count, 64):
            end = min(count, start + 64)
            keys = [prefix + str(i) for i in range(start, end)]
            if adapter.get_batch(keys) != [values[i % 4] for i in range(start, end)]:
                raise AssertionError("Common pool exact bytes mismatch")
            verified += len(keys)
        connection.send({"client": index, "valid": True, "inserted_keys": count,
                         "verified_keys": verified, "prefix": prefix, "native_metadata": adapter.metadata})
    except BaseException:
        connection.send({"client": index, "valid": False, "error": traceback.format_exc()})
    finally:
        if adapter is not None:
            adapter.close()
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    for field in ["adapter", "config", "run-id", "out"]:
        parser.add_argument("--" + field, required=True)
    for field in ["clients", "size", "pool-keys"]:
        parser.add_argument("--" + field, required=True, type=int)
    args = vars(parser.parse_args())
    if any(args[name] <= 0 for name in ["clients", "size", "pool_keys"]) or args["pool_keys"] % args["clients"]:
        parser.error("A positive aligned common pool is required")
    context = mp.get_context("spawn")
    children, connections, results = [], [], []
    result = {"valid": False, "scope": "unmeasured common read pool; full exact bytes verification",
              "parameters": args, "started_wall_ns": time.time_ns()}
    try:
        for index in range(args["clients"]):
            parent, child = context.Pipe(duplex=False)
            process = context.Process(target=preload, args=(index, args, child))
            process.start()
            child.close()
            children.append(process)
            connections.append(parent)
        deadline = time.monotonic() + 600
        for connection in connections:
            if not connection.poll(max(0, deadline - time.monotonic())):
                raise TimeoutError("Common preload did not complete")
            results.append(connection.recv())
        result["valid"] = all(row.get("valid") for row in results) and sum(row.get("verified_keys", 0) for row in results) == args["pool_keys"]
    except BaseException:
        result["error"] = traceback.format_exc()
    finally:
        for process in children:
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
        result.update(clients=results, ended_wall_ns=time.time_ns(), process_exit_codes=[p.exitcode for p in children])
        if any(p.exitcode != 0 for p in children):
            result["valid"] = False
        Path(args["out"]).write_text(json.dumps(result, indent=2) + "\n")
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
