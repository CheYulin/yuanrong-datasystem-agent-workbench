#!/usr/bin/env python3
"""Measure one native batch API; the parent synchronizes private client key pools."""
import argparse
from collections import Counter
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import time
import traceback

from benchmark import bucket, load_adapter, percentile, receive


def worker(index, args, connection):
    adapter = None
    try:
        config = json.loads(Path(args["config"]).read_text())
        config.update(client_index=index, concurrency=args["concurrency"],
                      value_size=args["size"], run_id=args["run_id"],
                      write_mode="upsert" if args["op"] == "upsert" else "insert")
        adapter = load_adapter(args["adapter"], config)
        batch = args["batch"]
        pool = args["pool_keys"] // args["concurrency"]
        data = [hashlib.shake_256(f"batch-v1-c{index}-v{i}".encode()).digest(args["size"]) for i in range(8)]
        prefix = f"batch-{args['run_id']}-c{index}-"
        def keys(offset, count=batch, phase="pool"):
            return [prefix + phase + "-" + str(i) for i in range(offset, offset + count)]
        def values(offset, count=batch, version=0):
            return [data[(i + version * 4) % 4 + version * 4] for i in range(offset, offset + count)]
        def exact(offset, count, phase="pool", version=0):
            answer = adapter.get_batch(keys(offset, count, phase))
            if answer != values(offset, count, version):
                raise AssertionError("full batch bytes/order mismatch")
        if not args["skip_preload"]:
            chunk = int(config.get("preload_batch", 256))
            for offset in range(0, pool, chunk):
                count = min(chunk, pool - offset)
                adapter.put_batch(keys(offset, count), values(offset, count))
        if args["op"] != "put":
            # The scalar fallback here is setup only, never the measured API.
            check_batch = min(batch, int(config.get("get_check_batch", batch)))
            exact(0, check_batch)
            if adapter.exists_batch(keys(0)) != [True] * batch:
                raise AssertionError("preloaded batch EXIST mismatch")
            if adapter.exists_batch(keys(0, phase="absent")) != [False] * batch:
                raise AssertionError("missing batch EXIST mismatch")
        metadata = getattr(adapter, "metadata", {})
        metadata = metadata() if callable(metadata) else metadata
        connection.send({"ready": True, "client": index, "pool_keys": pool,
                         "metadata": metadata,
                         "preloaded": not args["skip_preload"]})
        warm_start = connection.recv()["start_ns"]
        while time.monotonic_ns() < warm_start:
            time.sleep(0.001)
        def invoke(offset, version=0, phase="pool"):
            request_keys = keys(offset, phase=phase)
            if args["op"] in {"put", "upsert"}:
                adapter.put_batch(request_keys, values(offset, version=version))
                return None
            if args["op"] == "get":
                return adapter.get_batch(request_keys)
            return adapter.exists_batch(request_keys)
        if args["op"] == "put":
            warm_keys = args.get("put_warmup_keys", 32768) // args["concurrency"]
            for offset in range(0, warm_keys, batch):
                invoke(offset, phase="warm")
                exact(offset, batch, "warm")
        else:
            warm_end = time.monotonic() + args["warmup"]
            i = 0
            while time.monotonic() < warm_end:
                invoke(i * batch % pool, phase="absent" if args["op"] == "exist_miss" else "pool")
                i += 1
        connection.send({"warm": True, "client": index,
                         "put_inserted_keys": warm_keys if args["op"] == "put" else 0,
                         "put_verified_keys": warm_keys if args["op"] == "put" else 0})
        start = connection.recv()["start_ns"]
        stop = start + int(args["duration"] * 1e9)
        while time.monotonic_ns() < start:
            time.sleep(0.0005)
        count = failures = validations = total_latency = maximum = 0
        hist, seconds = Counter(), Counter()
        errors = []
        target = args["put_keys"] // args["concurrency"] // batch
        first = time.monotonic_ns()
        # Keep successful update identities for post-measure verification.
        latest_versions = {}
        while count + failures < target if args["op"] == "put" else time.monotonic_ns() < stop:
            i = count + failures
            offset = i * batch if args["op"] == "put" else i * batch % pool
            version = ((i * batch // pool + 1) % 2) if args["op"] == "upsert" else 0
            phase = "insert-b" + str(batch) if args["op"] == "put" else "absent" if args["op"] == "exist_miss" else "pool"
            request_keys = keys(offset, phase=phase)
            request_values = values(offset, version=version) if args["op"] in {"put", "upsert"} else None
            before = time.monotonic_ns()
            try:
                if args["op"] in {"put", "upsert"}:
                    adapter.put_batch(request_keys, request_values)
                    answer = None
                elif args["op"] == "get":
                    answer = adapter.get_batch(request_keys)
                else:
                    answer = adapter.exists_batch(request_keys)
                after = time.monotonic_ns()
                if args["op"] == "get":
                    if len(answer) != batch or any(type(v) is not bytes or len(v) != args["size"] for v in answer):
                        raise AssertionError("GET batch incomplete bytes")
                    if i % 128 == 0:
                        if answer != values(offset):
                            raise AssertionError("GET sampled full-batch content mismatch")
                        validations += batch
                elif args["op"].startswith("exist"):
                    if answer != [args["op"] == "exist_hit"] * batch:
                        raise AssertionError("EXIST vector mismatch")
                elif args["op"] == "upsert":
                    latest_versions[offset] = version
                elapsed = after - before
                hist[bucket(elapsed)] += 1
                seconds[int((after - start) / 1e9)] += 1
                total_latency += elapsed
                maximum = max(maximum, elapsed)
                count += 1
            except Exception as exc:
                failures += 1
                if len(errors) < 5:
                    errors.append(type(exc).__name__ + ": " + str(exc))
                # Partial commit is possible: fail closed, do not retry.
                break
        last = time.monotonic_ns()
        connection.send({"client": index, "success": count, "failures": failures,
                         "first_ns": first, "last_ns": last, "target": target,
                         "start_offset_us": (first - start) / 1000,
                         "latency_sum_ns": total_latency, "latency_max_ns": maximum,
                         "histogram": dict(hist), "per_second": dict(seconds),
                         "validated_keys": validations, "errors": errors})
        connection.recv()
        post = 0
        if count and args["op"] == "put":
            for call in sorted({0, count // 2, count - 1}):
                exact(call * batch, batch, "insert-b" + str(batch))
                post += batch
        if count and args["op"] == "upsert":
            for offset in sorted(latest_versions):
                exact(offset, batch, version=latest_versions[offset])
                post += batch
            # Restore known bytes outside measurement for the next operation/B.
            for offset in range(0, pool, batch):
                adapter.put_batch(keys(offset), values(offset))
            for offset in range(0, pool, batch):
                exact(offset, batch)
        connection.send({"client": index, "post_validated_keys": post,
                         "restored_verified_keys": pool if args["op"] == "upsert" and count else 0})
        connection.recv()
        adapter.close()
        adapter = None
        connection.send({"client": index, "closed": True})
    except BaseException:
        connection.send({"client": index, "fatal": traceback.format_exc()})
    finally:
        if adapter is not None:
            adapter.close()
        connection.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adapter", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--op", choices=["put", "upsert", "get", "exist_hit", "exist_miss"], required=True)
    p.add_argument("--batch", type=int, required=True)
    p.add_argument("--size", type=int, default=1024)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--pool-keys", type=int, default=65536)
    p.add_argument("--put-keys", type=int, default=262144)
    p.add_argument("--put-warmup-keys", type=int, default=32768)
    p.add_argument("--duration", type=float, default=20)
    p.add_argument("--warmup", type=float, default=3)
    p.add_argument("--skip-preload", action="store_true")
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", required=True)
    args = vars(p.parse_args())
    if any(args[k] <= 0 for k in ["batch", "size", "concurrency", "pool_keys", "put_keys", "put_warmup_keys", "duration"]):
        p.error("positive workload parameters required")
    if any(args[k] % (args["batch"] * args["concurrency"]) for k in ["pool_keys", "put_keys", "put_warmup_keys"]):
        p.error("key pools must be divisible by concurrency * batch")
    ctx = mp.get_context("spawn")
    processes, connections = [], []
    result = {"parameters": args, "wall_start_ns": time.time_ns(), "valid": False,
              "rate_unit": "completed native batch calls per second", "latency_unit": "whole batch completion microseconds"}
    try:
        for index in range(args["concurrency"]):
            parent, child = ctx.Pipe()
            proc = ctx.Process(target=worker, args=(index, args, child))
            proc.start()
            child.close()
            processes.append(proc)
            connections.append(parent)
        result["setup"] = [receive(c, 600) for c in connections]
        warm_start = time.monotonic_ns() + 300_000_000
        for c in connections:
            c.send({"start_ns": warm_start})
        result["warmup"] = [receive(c, args["warmup"] + 120) for c in connections]
        start = time.monotonic_ns() + 300_000_000
        result.update(measure_start_mono_ns=start, measure_start_wall_ns=time.time_ns() + start - time.monotonic_ns())
        for c in connections:
            c.send({"start_ns": start})
        clients = [receive(c, 1200) for c in connections]
        result["clients"] = clients
        for c in connections:
            c.send({"validate": True})
        result["post_validation"] = [receive(c, 300) for c in connections]
        for c in connections:
            c.send({"close": True})
        result["closed"] = [receive(c, 60) for c in connections]
        hist = Counter()
        for c in clients:
            hist.update({int(k): v for k, v in c["histogram"].items()})
        count = sum(c["success"] for c in clients)
        failures = sum(c["failures"] for c in clients)
        last = max(c["last_ns"] for c in clients)
        elapsed = (last - start) / 1e9
        valid = failures == 0 and count > 0
        if args["op"] == "put":
            valid = valid and all(c["success"] == c["target"] for c in clients)
        result.update(success=count, success_keys=count * args["batch"], failures=failures,
                      failed_keys_upper_bound=failures * args["batch"], partial_success_keys_unknown=bool(failures),
                      measure_end_mono_ns=last, duration_s=elapsed,
                      batch_qps=count / elapsed, keys_s=count * args["batch"] / elapsed,
                      throughput_mib_s=count * args["batch"] * args["size"] / elapsed / (1 << 20) if args["op"] in {"put", "upsert", "get"} else None,
                      mean_us=sum(c["latency_sum_ns"] for c in clients) / count / 1000 if count else None,
                      max_us=max(c["latency_max_ns"] for c in clients) / 1000,
                      p50=percentile(hist, .5), p95=percentile(hist, .95), p99=percentile(hist, .99),
                      histogram=dict(hist), valid=valid, short_window=elapsed < 10,
                      low_sample_p99=count < 1000)
    except BaseException:
        result["fatal"] = traceback.format_exc()
    finally:
        for proc in processes:
            proc.join(10)
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join(5)
        result["process_exit_codes"] = [proc.exitcode for proc in processes]
        if any(code != 0 for code in result["process_exit_codes"]):
            result["valid"] = False
            result["exit_error"] = "A client did not exit normally"
        result["wall_end_ns"] = time.time_ns()
        out = Path(args["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x") as stream:
            json.dump(result, stream, indent=2)
    print(json.dumps({k: result.get(k) for k in ["valid", "batch_qps", "keys_s", "duration_s", "success", "failures", "fatal"]}))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
