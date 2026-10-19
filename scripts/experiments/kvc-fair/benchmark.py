#!/usr/bin/env python3
"""Synchronized SDK-level bytes benchmark. Setup, warmup, measurement are distinct."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
import multiprocessing as mp
from pathlib import Path
import time
import traceback


def bucket(ns):
    ns = max(64, ns)
    exponent = ns.bit_length() - 1
    return exponent * 64 + (ns >> (exponent - 6)) - 64


def percentile(hist, fraction):
    threshold = math.ceil(sum(hist.values()) * fraction)
    accumulated = 0
    for idx, count in sorted(hist.items()):
        accumulated += count
        if accumulated >= threshold:
            exponent, sub = divmod(idx, 64)
            return {"lower_us": ((64 + sub) << (exponent - 6)) / 1000,
                    "upper_us": ((65 + sub) << (exponent - 6)) / 1000}
    return None


def load_adapter(path, config):
    spec = importlib.util.spec_from_file_location("product_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.new_adapter(config)


def worker(index, args, connection):
    adapter = None
    try:
        config = json.loads(Path(args["config"]).read_text())
        config.update(client_index=index, concurrency=args["concurrency"], value_size=args["size"], run_id=args["run_id"])
        adapter = load_adapter(args["adapter"], config)
        size = args["size"]
        data = [hashlib.shake_256(("kvc-fair-v1-" + str(index) + "-" + str(i)).encode()).digest(size) for i in range(4)]
        per_client = max(4, args["preload_bytes"] // size // args["concurrency"])
        prefix = "kvc-" + args["run_id"] + "-c" + str(index) + "-"
        def key(i, phase="data"):
            return prefix + phase + "-" + str(i)
        if args["op"] != "put":
            if not args["skip_preload"]:
                for i in range(per_client):
                    adapter.put(key(i), data[i % len(data)])
            for i in sorted(set([0, 1, 2, 3, per_client - 1])):
                if adapter.get(key(i)) != data[i % len(data)]:
                    raise AssertionError("preload exact bytes mismatch")
                if not adapter.exists(key(i)):
                    raise AssertionError("preload exists false")
            if adapter.exists(key(0, "absent")):
                raise AssertionError("missing exists true")
        connection.send({"ready": True, "client": index, "preload_count": per_client if args["op"] != "put" else 0})
        warm_start = connection.recv()["start_ns"]
        while time.monotonic_ns() < warm_start:
            time.sleep(0.001)
        if args["op"] == "put":
            for i in range(4):
                adapter.put(key(i, "warm"), data[i])
                if adapter.get(key(i, "warm")) != data[i]:
                    raise AssertionError("PUT warmup bytes mismatch")
        else:
            warm_end = time.monotonic() + args["warmup"]
            i = 0
            while time.monotonic() < warm_end:
                if args["op"] == "get":
                    adapter.get(key(i % per_client))
                else:
                    adapter.exists(key(i % per_client, "absent" if args["op"] == "exist_miss" else "data"))
                i += 1
        connection.send({"warm": True, "client": index})
        start_ns = connection.recv()["start_ns"]
        end_ns = start_ns + int(args["duration"] * 1e9)
        while time.monotonic_ns() < start_ns:
            time.sleep(0.0005)
        target = args["total_bytes"] // size // args["concurrency"]
        count = failures = validations = total_latency = max_latency = 0
        hist = Counter()
        seconds = Counter()
        first = time.monotonic_ns()
        errors = []
        while (count + failures < target if args["op"] == "put" else time.monotonic_ns() < end_ns):
            i = count + failures
            k = key(i if args["op"] == "put" else i % per_client,
                    "insert" if args["op"] == "put" else "absent" if args["op"] == "exist_miss" else "data")
            value = data[i % len(data)]
            before = time.monotonic_ns()
            try:
                if args["op"] == "put":
                    adapter.put(k, value)
                    answer = None
                elif args["op"] == "get":
                    answer = adapter.get(k)
                else:
                    answer = adapter.exists(k)
                after = time.monotonic_ns()
                if args["op"] == "get" and (not isinstance(answer, bytes) or len(answer) != size):
                    raise AssertionError("GET did not return complete bytes")
                if args["op"].startswith("exist") and answer != (args["op"] == "exist_hit"):
                    raise AssertionError("EXIST incorrect boolean")
                if args["op"] == "get" and i % 1024 == 0:
                    if answer != data[(i % per_client) % len(data)]:
                        raise AssertionError("GET sampled bytes mismatch")
                    validations += 1
                elapsed = after - before
                hist[bucket(elapsed)] += 1
                total_latency += elapsed
                max_latency = max(max_latency, elapsed)
                seconds[int((after - start_ns) / 1e9)] += 1
                count += 1
            except Exception as exc:
                failures += 1
                if len(errors) < 5:
                    errors.append(str(exc))
                if failures >= 10:
                    break
        last = time.monotonic_ns()
        connection.send({"client": index, "success": count, "failures": failures,
                         "first_ns": first, "last_ns": last, "start_offset_us": (first - start_ns) / 1000,
                         "latency_sum_ns": total_latency, "latency_max_ns": max_latency,
                         "histogram": dict(hist), "per_second": dict(seconds), "validations": validations,
                         "errors": errors, "target": target})
        connection.recv()
        post_validations = 0
        if args["op"] == "put" and count:
            for i in sorted(set([0, count // 2, count - 1])):
                if adapter.get(key(i, "insert")) != data[i % len(data)]:
                    raise AssertionError("PUT post-measure exact bytes mismatch")
                post_validations += 1
        connection.send({"client": index, "post_validations": post_validations})
        connection.recv()
    except BaseException:
        connection.send({"client": index, "fatal": traceback.format_exc()})
    finally:
        if adapter is not None:
            try:
                adapter.close()
            except Exception:
                pass
        connection.close()


def receive(connection, timeout):
    if not connection.poll(timeout):
        raise TimeoutError("client stage timeout")
    message = connection.recv()
    if "fatal" in message:
        raise RuntimeError(message["fatal"])
    return message


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--op", choices=["put", "get", "exist_hit", "exist_miss"], required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--warmup", type=float, default=3)
    parser.add_argument("--total-bytes", type=int, default=2 << 30)
    parser.add_argument("--preload-bytes", type=int, default=64 << 20)
    parser.add_argument("--skip-preload", action="store_true")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True)
    args = vars(parser.parse_args())
    ctx = mp.get_context("spawn")
    processes, connections = [], []
    result = {"parameters": args, "wall_start_ns": time.time_ns(), "valid": False}
    try:
        for index in range(args["concurrency"]):
            parent, child = ctx.Pipe()
            proc = ctx.Process(target=worker, args=(index, args, child))
            proc.start(); child.close()
            processes.append(proc); connections.append(parent)
        result["setup"] = [receive(c, 300) for c in connections]
        warm = time.monotonic_ns() + 300_000_000
        for conn in connections:
            conn.send({"start_ns": warm})
        result["warmup"] = [receive(c, args["warmup"] + 120) for c in connections]
        start = time.monotonic_ns() + 300_000_000
        result["measure_start_mono_ns"] = start
        result["measure_start_wall_ns"] = time.time_ns() + start - time.monotonic_ns()
        for conn in connections:
            conn.send({"start_ns": start})
        clients = [receive(c, max(300, args["duration"] + 120)) for c in connections]
        result["clients"] = clients
        for conn in connections:
            conn.send({"validate": True})
        result["post_validation"] = [receive(c, 120) for c in connections]
        for conn in connections:
            conn.send({"close": True})
        hist = Counter()
        for client in clients:
            hist.update({int(k): v for k, v in client["histogram"].items()})
        count = sum(c["success"] for c in clients)
        failures = sum(c["failures"] for c in clients)
        last = max(c["last_ns"] for c in clients)
        elapsed = (last - start) / 1e9
        result.update(success=count, failures=failures, duration_s=elapsed,
                      measure_end_mono_ns=last, qps=count / elapsed,
                      throughput_mib_s=count * args["size"] / elapsed / (1 << 20) if args["op"] in ["put", "get"] else None,
                      mean_us=sum(c["latency_sum_ns"] for c in clients) / count / 1000 if count else None,
                      max_us=max(c["latency_max_ns"] for c in clients) / 1000,
                      p50=percentile(hist, 0.5), p95=percentile(hist, 0.95), p99=percentile(hist, 0.99),
                      histogram=dict(hist), valid=failures == 0 and count > 0 and all(c["success"] == c["target"] for c in clients) if args["op"] == "put" else failures == 0 and count > 0,
                      short_window=elapsed < 10)
    except BaseException:
        result["fatal"] = traceback.format_exc()
    finally:
        for proc in processes:
            proc.join(10)
            if proc.is_alive():
                proc.terminate(); proc.join(5)
        result["wall_end_ns"] = time.time_ns()
        Path(args["out"]).parent.mkdir(parents=True, exist_ok=True)
        Path(args["out"]).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result.get(k) for k in ["valid", "qps", "duration_s", "success", "failures", "fatal"]}))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
