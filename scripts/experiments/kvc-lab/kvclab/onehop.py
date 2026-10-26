"""Independent, fail-closed native MSet RPC qualification; never a benchmark."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

from .remote_support import atomic, digest, member, read, ticks


SOURCE_REVISION = "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6"
CLIENT_MULTI = "_ZN10datasystem24WorkerOCService_BrpcStub12MultiPublishERKNS_10RpcOptionsERKNS_17MultiPublishReqPbERNS_17MultiPublishRspPbERKSt6vectorINS_7MemViewESaISA_EE"
CHANNEL = "_ZN4brpc7Channel10CallMethodEPKN6google8protobuf16MethodDescriptorEPNS2_13RpcControllerEPKNS2_7MessageEPS8_PNS2_7ClosureE"
ISSUE = "_ZN4brpc10Controller8IssueRPCEl"
WORKER_MULTI = "_ZN10datasystem12object_cache19WorkerOCServiceImpl12MultiPublishERKNS_17MultiPublishReqPbERNS_17MultiPublishRspPbESt6vectorINS_10RpcMessageESaIS8_EE"
META_SYMBOLS = {
    "local_create": "_ZN10datasystem6worker22WorkerLocalMasterOCApi10CreateMetaERNS_6master15CreateMetaReqPbERNS2_15CreateMetaRspPbE",
    "local_update": "_ZN10datasystem6worker22WorkerLocalMasterOCApi10UpdateMetaERNS_6master15UpdateMetaReqPbERNS2_15UpdateMetaRspPbE",
    "local_multi": "_ZN10datasystem6worker22WorkerLocalMasterOCApi15CreateMultiMetaERNS_6master20CreateMultiMetaReqPbERNS2_20CreateMultiMetaRspPbEb",
    "remote_create": "_ZN10datasystem6worker23WorkerRemoteMasterOCApi10CreateMetaERNS_6master15CreateMetaReqPbERNS2_15CreateMetaRspPbE",
    "remote_update": "_ZN10datasystem6worker23WorkerRemoteMasterOCApi10UpdateMetaERNS_6master15UpdateMetaReqPbERNS2_15UpdateMetaRspPbE",
    "remote_multi": "_ZN10datasystem6worker23WorkerRemoteMasterOCApi15CreateMultiMetaERNS_6master20CreateMultiMetaReqPbERNS2_20CreateMultiMetaRspPbEb",
}
CLIENT_GUARDS = {
    "client_create": "_ZN10datasystem24WorkerOCService_BrpcStub6CreateERKNS_10RpcOptionsERKNS_11CreateReqPbERNS_11CreateRspPbE",
    "client_multicreate": "_ZN10datasystem24WorkerOCService_BrpcStub11MultiCreateERKNS_10RpcOptionsERKNS_16MultiCreateReqPbERNS_16MultiCreateRspPbE",
    "client_publish": "_ZN10datasystem24WorkerOCService_BrpcStub7PublishERKNS_10RpcOptionsERKNS_12PublishReqPbERNS_12PublishRspPbERKSt6vectorINS_7MemViewESaISA_EE",
}
EXPECTED = dict(
    client_multi=1, channel=1, issue=1, worker_multi=1, local_multi=1,
    local_create=0, local_update=0, remote_create=0, remote_update=0, remote_multi=0,
    client_create=0, client_multicreate=0, client_publish=0,
)


def parse_counts(text):
    values = {}
    for name, value in re.findall(r"^@counts\[([a-z_]+)\]:\s*(\d+)\s*$", text, re.MULTILINE):
        if name in values:
            raise ValueError("Duplicate probe counter: " + name)
        values[name] = int(value)
    if set(values) != set(EXPECTED):
        raise ValueError("Incomplete or unexpected probe counter set")
    return values


def assess(counts, client, probe_returncode, ready, ended):
    checks = {
        "probe_ready": ready is True,
        "probe_ended_cleanly": ended is True and probe_returncode == 0,
        "all_counters_present": set(counts) == set(EXPECTED)
                                and all(type(value) is int and value >= 0 for value in counts.values()),
        "exact_rpc_and_metadata_counts": counts == EXPECTED,
        "native_mset_once": client.get("put_calls") == 1 and client.get("native_api") == "mset"
                            and client.get("batch_size") == 1,
        "exact_value_readback": client.get("valid") is True and client.get("read_matches") is True,
    }
    return {"verified": all(checks.values()), "checks": checks}


def mapped_library(pid, basename):
    found = set()
    for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or not fields[5].startswith("/"):
            continue
        path = Path(fields[5])
        if path.name != basename:
            continue
        stat = path.stat()
        major, minor = (int(value, 16) for value in fields[3].split(":"))
        if stat.st_ino != int(fields[4]) or (os.major(stat.st_dev), os.minor(stat.st_dev)) != (major, minor):
            raise RuntimeError("Mapped ELF identity changed")
        found.add(str(path.resolve()))
    if len(found) != 1:
        raise RuntimeError("Expected one mapped ELF: " + basename)
    path = found.pop()
    if re.fullmatch(r"/[A-Za-z0-9_./+\-]+", path) is None:
        raise ValueError("ELF path cannot be represented safely in a probe")
    return path


def require_symbols(path, names):
    process = subprocess.run(["nm", "-D", "--defined-only", path], capture_output=True, text=True, timeout=15, check=True)
    available = {line.split()[-1] for line in process.stdout.splitlines() if line.split()}
    missing = sorted(set(names) - available)
    if missing:
        raise RuntimeError("Required dynamic symbols are missing: " + ", ".join(missing))
    return {"path": path, "sha256": digest(path), "symbols": list(names)}


def probe_program(client_pid, client_lib, brpc_lib, workers, nonce):
    if type(client_pid) is not int or client_pid <= 0 or not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise ValueError("Invalid probe identity")
    for path in [client_lib, brpc_lib, *[entry["library"] for entry in workers]]:
        if re.fullmatch(r"/[A-Za-z0-9_./+\-]+", path) is None:
            raise ValueError("Unsafe probe path")
    lines = ["BEGIN {", *[f' @counts["{name}"] = sum(0);' for name in EXPECTED],
             f' printf("KVC_ONEHOP_ATTACHED {nonce}\\n");', "}"]
    lines += [
        f"uprobe:{client_lib}:{CLIENT_MULTI} /pid == {client_pid}/ {{",
        ' @counts["client_multi"] = sum(1); @active = 1; @pending[pid,tid] = 1;', "}",
        f"uretprobe:{client_lib}:{CLIENT_MULTI} /pid == {client_pid}/ {{ @active = 0; delete(@pending[pid,tid]); }}",
        f"uprobe:{brpc_lib}:{CHANNEL} /pid == {client_pid} && @active && @pending[pid,tid]/ {{",
        ' @counts["channel"] = sum(1); @controllers[pid,arg2] = 1; delete(@pending[pid,tid]);', "}",
        f"uprobe:{brpc_lib}:{ISSUE} /pid == {client_pid} && @active && @controllers[pid,arg0]/ {{",
        ' @counts["issue"] = sum(1);', "}",
    ]
    for name, symbol in CLIENT_GUARDS.items():
        lines.append(f'uprobe:{client_lib}:{symbol} /pid == {client_pid}/ {{ @counts["{name}"] = sum(1); }}')
    for worker in workers:
        pid, library = worker["pid"], worker["library"]
        if type(pid) is not int or pid <= 0:
            raise ValueError("Invalid worker PID")
        entries = {"worker_multi": WORKER_MULTI, **META_SYMBOLS}
        for name, symbol in entries.items():
            lines.append(f'uprobe:{library}:{symbol} /pid == {pid} && @active/ {{ @counts["{name}"] = sum(1); }}')
    lines += ["END {", " print(@counts); clear(@counts); clear(@active); clear(@pending); clear(@controllers);",
              f' printf("KVC_ONEHOP_ENDED {nonce}\\n");', "}"]
    return "\n".join(lines) + "\n"


def _wait_marker(process, path, marker, deadline):
    while time.monotonic() < deadline:
        if marker in path.read_text(errors="replace"):
            return
        if process.poll() is not None:
            raise RuntimeError("Qualification child exited before " + marker.split()[0])
        time.sleep(.05)
    raise TimeoutError("Timed out waiting for " + marker.split()[0])


def _stop_child(process, identity, group, interrupt=False):
    if process.poll() is None:
        if ticks(process.pid) != identity["start_ticks"] or not member(process.pid, group):
            raise RuntimeError("Qualification child ownership changed")
        process.send_signal(signal.SIGINT if interrupt else signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return {"pid": process.pid, "returncode": process.returncode, "terminal": process.poll() is not None}


def verify_one_hop(settings, deployment, adapter_config_path, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = {"schema_version": 1, "verified": False, "status": "not_verified",
              "scope": "one fresh-key native B1 MSet, exact readback, no performance samples",
              "expected_counts": EXPECTED, "commands": [], "cleanup": [], "raw_counts": {}}
    children = []
    streams = []
    try:
        if deployment.get("product") != "datasystem" or deployment.get("active") is not True:
            raise ValueError("An active owned DataSystem deployment is required")
        for executable in ("bpftrace", "nm", "git"):
            if shutil.which(executable) is None:
                raise RuntimeError("Missing qualification tool: " + executable)
        native = Path(settings["native_root"]) / "datasystem"
        revision = subprocess.check_output(["git", "-C", str(native), "rev-parse", "HEAD"], text=True, timeout=10).strip()
        result["source_revision"] = revision
        if revision != SOURCE_REVISION:
            raise ValueError("Qualification symbols are pinned to v0.9.2.rc44")
        group = Path(deployment["cgroup"])
        allowed = Path(settings.get("cgroup_root", "/sys/fs/cgroup/kvc-lab"))
        if allowed not in group.parents or any(path.is_symlink() for path in [group, *group.parents]):
            raise ValueError("Deployment cgroup is outside its owned prefix")
        workers = []
        binaries = []
        for identity in deployment.get("processes", []):
            if not identity["name"].startswith("worker-"):
                continue
            pid = identity["pid"]
            if ticks(pid) != identity["start_ticks"] or not member(pid, group / "server/workers"):
                raise RuntimeError("Owned worker is not live in its recorded domain")
            library = mapped_library(pid, "libdatasystem_worker.so")
            binaries.append(require_symbols(library, [WORKER_MULTI, *META_SYMBOLS.values()]))
            workers.append({"pid": pid, "start_ticks": identity["start_ticks"], "library": library})
        if not workers or len(workers) != deployment.get("workers") or len({worker["pid"] for worker in workers}) != len(workers):
            raise ValueError("Every deployed worker must have a unique owned live PID")
        result["workers"] = workers
        config = read(adapter_config_path)
        if not isinstance(config, dict) or not config.get("endpoints"):
            raise ValueError("A complete adapter configuration is required")
        config["log_dir"] = str(out / "sdk-logs")
        Path(config["log_dir"]).mkdir()
        config_path = out / "adapter-config.json"
        atomic(config_path, config)
        result["adapter_sha256"] = digest(Path(__file__).parent / "adapters/datasystem.py")
        nonce = uuid.uuid4().hex
        python = deployment["python"]
        lab = Path(__file__).resolve().parent.parent
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(lab) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        env.update(DATASYSTEM_MIN_LOG_LEVEL="2", DATASYSTEM_LOG_MONITOR_ENABLE="false")
        deadline = time.monotonic() + min(180, max(30, int(settings.get("one_hop_timeout_s", 120))))

        def spawn(name, command, domain, pipe=False):
            log = out / (name + ".log")
            stream = log.open("xb", buffering=0)
            streams.append(stream)
            argv = [python, "-B", "-m", "kvclab.remote_exec", "--group", str(domain), "--", *command]
            process = subprocess.Popen(argv, cwd=lab, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                       stdin=subprocess.PIPE if pipe else subprocess.DEVNULL, start_new_session=True)
            identity = {"name": name, "pid": process.pid, "start_ticks": ticks(process.pid),
                        "group": str(domain), "argv": argv, "log": str(log)}
            result["commands"].append(identity)
            children.append((process, identity, domain))
            return process, identity, log

        client_result = out / "client-result.json"
        client, client_identity, client_log = spawn(
            "client", [python, "-B", "-m", "kvclab.onehop", "client", "--config", str(config_path),
                       "--result", str(client_result), "--nonce", nonce], group / "client", True)
        _wait_marker(client, client_log, "KVC_ONEHOP_CLIENT_READY " + nonce, deadline)
        client_lib = mapped_library(client.pid, "libdatasystem.so")
        brpc_lib = mapped_library(client.pid, "libbrpc.so")
        binaries.append(require_symbols(client_lib, [CLIENT_MULTI, *CLIENT_GUARDS.values()]))
        binaries.append(require_symbols(brpc_lib, [CHANNEL, ISSUE]))
        result["binaries"] = binaries
        program = out / "probe.bt"
        program.write_text(probe_program(client.pid, client_lib, brpc_lib, workers, nonce))
        result["probe_sha256"] = digest(program)
        probe, probe_identity, probe_log = spawn("probe", [shutil.which("bpftrace"), "-q", "-B", "line", str(program)], group / "monitor")
        _wait_marker(probe, probe_log, "KVC_ONEHOP_ATTACHED " + nonce, deadline)
        if ticks(client.pid) != client_identity["start_ticks"] or not member(client.pid, group / "client"):
            raise RuntimeError("Qualification client ownership changed before release")
        client.stdin.write(b"put\n")
        client.stdin.flush()
        _wait_marker(client, client_log, "KVC_ONEHOP_PUT_DONE " + nonce, deadline)
        result["cleanup"].append(_stop_child(probe, probe_identity, group / "monitor", True))
        raw = probe_log.read_text(errors="replace")
        result["raw_counts"] = parse_counts(raw)
        client.stdin.write(b"verify\n")
        client.stdin.flush()
        client.stdin.close()
        client.wait(timeout=max(.1, deadline - time.monotonic()))
        result["client_result"] = read(client_result, {})
        result.update(assess(result["raw_counts"], result["client_result"], probe.returncode,
                             "KVC_ONEHOP_ATTACHED " + nonce in raw, "KVC_ONEHOP_ENDED " + nonce in raw))
        if client.returncode != 0:
            result["verified"] = False
            result["reason"] = "Qualification client did not exit successfully"
        if any(ticks(worker["pid"]) != worker["start_ticks"] for worker in workers):
            result["verified"] = False
            result["reason"] = "A deployed worker exited or changed during qualification"
        result["status"] = "verified" if result["verified"] else "not_verified"
        if not result["verified"] and "reason" not in result:
            result["reason"] = "Runtime counter or value-readback requirements were not met"
    except Exception as exc:
        result["verified"] = False
        result["status"] = "not_verified"
        result["reason"] = type(exc).__name__ + ": " + str(exc)
    finally:
        for process, identity, domain in reversed(children):
            try:
                result["cleanup"].append(_stop_child(process, identity, domain, domain.name == "monitor"))
            except Exception as exc:
                result["verified"] = False
                result["status"] = "not_verified"
                result.setdefault("cleanup_errors", []).append(type(exc).__name__ + ": " + str(exc))
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
        for stream in streams:
            stream.close()
        result["duration_s"] = time.monotonic() - started
        atomic(out / "one-hop.json", result)
    return result


def client_main(config_path, result_path, nonce):
    from .adapters.datasystem import new_adapter

    config = read(config_path)
    size = config.get("value_size", 1024)
    if type(size) is not int or not 1 <= size <= 1 << 20:
        raise ValueError("Qualification value_size must be between 1 byte and 1 MiB")
    adapter = None
    result = {"valid": False, "native_api": "mset", "batch_size": 1, "put_calls": 0,
              "read_matches": False, "value_size": size, "scope": "qualification only; no performance samples"}
    try:
        adapter = new_adapter(config)
        key = "lab-onehop-" + uuid.uuid4().hex
        value = (bytes(range(256)) * ((size + 255) // 256))[:size]
        result.update(key=key, value_sha256=hashlib.sha256(value).hexdigest(), adapter_metadata=adapter.metadata)
        print("KVC_ONEHOP_CLIENT_READY " + nonce, flush=True)
        if sys.stdin.readline() != "put\n":
            raise RuntimeError("Qualification release was not received")
        result["put_calls"] += 1
        result["put_start_mono_ns"] = time.monotonic_ns()
        adapter.put_batch([key], [value])
        result["put_end_mono_ns"] = time.monotonic_ns()
        print("KVC_ONEHOP_PUT_DONE " + nonce, flush=True)
        if sys.stdin.readline() != "verify\n":
            raise RuntimeError("Readback release was not received")
        result["read_matches"] = adapter.get_batch([key]) == [value]
        result["valid"] = result["read_matches"]
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        if adapter is not None:
            adapter.close()
        atomic(result_path, result)
    return 0 if result["valid"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    client = commands.add_parser("client")
    client.add_argument("--config", required=True)
    client.add_argument("--result", required=True)
    client.add_argument("--nonce", required=True)
    verify = commands.add_parser("verify")
    for argument in ("settings", "deployment", "adapter-config", "out-dir"):
        verify.add_argument("--" + argument, required=True)
    args = parser.parse_args()
    if args.command == "client":
        return client_main(args.config, args.result, args.nonce)
    result = verify_one_hop(read(args.settings), read(args.deployment), args.adapter_config, args.out_dir)
    print(json.dumps({"verified": result["verified"], "status": result["status"], "reason": result.get("reason")}))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
