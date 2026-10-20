#!/usr/bin/env python3
"""Exercise both service endpoints under strace before any TCP performance run."""

import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in {None, ""}:
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[3]))

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from profile_put import drain, stop_process

DATA_CALLS = {"read", "readv", "recvfrom", "recvmsg", "write", "writev", "sendto", "sendmsg"}


def trace_bytes(paths):
    total, calls, endpoints = 0, 0, set()
    for path in paths:
        pending = None
        for line in path.read_text(errors="replace").splitlines():
            call = re.match(r"(\w+)\(", line)
            resumed = re.match(r"<\.\.\. (\w+) resumed>", line)
            if call and "<unfinished ...>" in line:
                pending = (call[1], "<TCP:" in line, re.findall(r"<TCP:\[([^]]+)\]>", line))
                continue
            if resumed:
                tcp = pending is not None and pending[:2] == (resumed[1], True)
                call_endpoints = pending[2] if tcp else []
                name = resumed[1]
                pending = None
            elif call:
                name, tcp = call[1], "<TCP:" in line
                call_endpoints = re.findall(r"<TCP:\[([^]]+)\]>", line)
            else:
                continue
            result = re.search(r"\)\s+=\s+(\d+)(?:\s|$)", line)
            if name in DATA_CALLS and tcp and result:
                total += int(result[1]); calls += 1
                endpoints.update(call_endpoints)
    return {"successful_tcp_syscall_bytes": total, "successful_tcp_data_calls": calls,
            "tcp_endpoints": sorted(endpoints), "trace_files": len(paths),
            "scope": "Client-side successful TCP read/write/send/recv return bytes, including protocol overhead. Unmatched/incomplete syscalls are excluded; this is not application throughput."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root, out = args.root.resolve(), args.out.absolute()
    if out.exists():
        raise FileExistsError("Preserve protocol evidence; use a new output directory")
    with (root / "suite.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run_locked(root, out)


def run_locked(root, out):
    cg = Path("/sys/fs/cgroup/kvcfair20261020")
    drain(cg)
    out.mkdir(parents=True)
    summary = {"purpose": "tcp_protocol_correctness_only", "start_wall_ns": time.time_ns(),
               "start_mono_ns": time.monotonic_ns(), "products": [], "valid": False,
               "strace_version": subprocess.check_output(["strace", "-V"], text=True).splitlines()[0]}
    def execute(argv, log, env, client=False):
        started_wall, started_mono = time.time_ns(), time.monotonic_ns()
        result, error = None, None
        try:
            with log.open("a") as stream:
                result = subprocess.Popen(argv, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    result.wait(240)
                except BaseException:
                    stop_process(result)
                    raise
        except BaseException as exc:
            error = repr(exc)
            raise
        finally:
            cleanup, cleanup_error = None, None
            if client and result is not None and (error is not None or result.returncode != 0):
                try:
                    kill = cg / "client/cgroup.kill"
                    if kill.exists():
                        kill.write_text("1")
                        cleanup = drain(cg, roles=["client"])
                except Exception as exc:
                    cleanup_error = repr(exc)
            record = {"argv": argv, "start_wall_ns": started_wall, "end_wall_ns": time.time_ns(),
                      "duration_s": (time.monotonic_ns() - started_mono) / 1e9,
                      "returncode": result.returncode if result else None, "error": error,
                      "failed_client_cgroup_cleanup": cleanup, "client_cleanup_error": cleanup_error}
            with (out / "operations.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            if cleanup_error and error is None:
                raise RuntimeError("Failed protocol client cleanup: " + cleanup_error)
        if result.returncode:
            raise RuntimeError("Protocol gate command failed: " + str(log))
    try:
        products = json.loads((root / "products.json").read_text())["products"]
        if len(products) != 3 or {p["name"] for p in products} != {"datasystem", "mooncake", "lmcache"}:
            raise ValueError("Protocol gate requires the three unique products")
        for product in products:
            name = product["name"]
            directory = out / name
            directory.mkdir()
            variables = {"run_dir": str(directory / "run"), "server_logs": str(directory / "server-logs")}
            env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                       PYTHONHASHSEED="0", **{k:v.format(**variables) for k,v in product.get("env", {}).items()})
            env.update(DATASYSTEM_LOG_V="1", DATASYSTEM_MIN_LOG_LEVEL="0", DATASYSTEM_PERF_TRACE_LOG="true")
            start = [v.format(**variables) for v in product["start"]]
            stop = [v.format(**variables) for v in product["stop"]]
            record = {"product": name, "clients": [], "valid": False}
            summary["products"].append(record)
            try:
                execute(stop, directory / "lifecycle.log", env)
                execute(start, directory / "lifecycle.log", env)
                for index in [0, 1]:
                    trace = directory / ("strace-c" + str(index))
                    result_path = directory / ("probe-c" + str(index) + ".json")
                    argv = ["python3", str(root / "cgroup.py"), "--group", "client", "exec", "--",
                            "strace", "-ff", "-yy", "-s", "0", "-e", "trace=network,read,readv,write,writev",
                            "-o", str(trace), product["python"], str(root / "tcp_wire_probe.py"),
                            "--product", name, "--adapter", product["adapter"], "--config", product["config"],
                            "--client-index", str(index), "--out", str(result_path)]
                    execute(argv, directory / ("probe-c" + str(index) + ".log"), env, client=True)
                    raw = json.loads(result_path.read_text())
                    if not raw.get("valid"):
                        raise RuntimeError("Protocol correctness probe invalid: " + str(result_path))
                    paths = sorted(directory.glob(trace.name + ".*"))
                    observed = trace_bytes(paths)
                    required = 2 * (1 << 20) * 4
                    if observed["successful_tcp_syscall_bytes"] < required:
                        raise RuntimeError("Insufficient directly observed TCP data bytes: " + json.dumps(observed))
                    observed.update(client_index=index, probe_file=result_path.name,
                                    minimum_payload_bytes=required, valid=True,
                                    probe_sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),
                                    trace_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
                    record["clients"].append(observed)
                if name == "datasystem":
                    medium = out / "datasystem-medium.json"
                    execute(["python3", str(root / "cgroup.py"), "--group", "client", "exec", "--",
                             product["python"], str(root / "tcp_probe_ds_sizes.py"), "--root", str(root),
                             "--config", product["config"], "--out", str(medium),
                             "--log-dir", str(out / "datasystem-medium-logs")],
                            directory / "medium-probe.log", env, client=True)
                    details = json.loads(medium.read_text())
                    if details.get("passed") is not True or len(details.get("cases", [])) != 6:
                        raise RuntimeError("DataSystem three-size actual-medium evidence is incomplete")
                    record["actual_medium_evidence"] = {"path": medium.name, "sha256": hashlib.sha256(medium.read_bytes()).hexdigest()}
                record["valid"] = True
            finally:
                execute(stop, directory / "lifecycle.log", env)
                drain(cg)
        summary["valid"] = all(p["valid"] and len(p["clients"]) == 2 for p in summary["products"]) and len(summary["products"]) == 3
    except BaseException as exc:
        summary["fatal"] = repr(exc)
        raise
    finally:
        drain_error = None
        try:
            summary["cgroups_after"] = drain(cg)
        except Exception as exc:
            drain_error = repr(exc)
            summary["cgroup_drain_error"] = drain_error
            summary["valid"] = False
        summary.update(end_wall_ns=time.time_ns(), end_mono_ns=time.monotonic_ns())
        summary["duration_s"] = (summary["end_mono_ns"] - summary["start_mono_ns"]) / 1e9
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        if drain_error:
            raise RuntimeError("Protocol gate ended with nonempty cgroups: " + drain_error)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
