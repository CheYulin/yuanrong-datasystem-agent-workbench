#!/usr/bin/env python3
"""Reproduce the separate all-TCP matrix using the original pinned source builds."""
import argparse
import json
from pathlib import Path
import shlex
import sys

import workflow

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "prepare", "probe", "retry-probe", "smoke", "run", "profile", "restore", "collect", "report", "publish"])
    parser.add_argument("--target", default="tiantiyun-80c128g")
    parser.add_argument("--base", default="/home/worktrees/kvc-fair-20261020")
    parser.add_argument("--root", default="/home/worktrees/kvc-fair-tcp-20261020")
    parser.add_argument("--local-output", type=Path, default=Path.home()/"workspace/scratch/2026-10-20-kvc-tcp")
    args = parser.parse_args()
    workflow.TIMING_LOG = args.local_output / "operation-timings.jsonl"
    run = workflow.run
    def ssh(command):
        run(["ssh", args.target, shlex.join(str(v) for v in command)])
    suite = args.root + "/formal-tcp-001"
    if args.action == "stage":
        bundle = "/home/cache/kvc-fair-20261020/tcp-script-bundle"
        ssh(["mkdir", "-p", bundle])
        run(["rsync", "-a", "--exclude", "__pycache__", str(HERE)+"/", args.target+":"+bundle+"/"])
        ssh(["python3", bundle+"/tcp_stage.py", "--base", args.base, "--root", args.root, "--bundle", bundle])
    elif args.action in {"probe", "retry-probe"}:
        if args.action == "retry-probe":
            bundle = "/home/cache/kvc-fair-20261020/tcp-script-bundle"
            run(["scp", *(HERE / name for name in ["tcp_stage.py", "tcp_protocol_gate.py", "tcp_probe_ds_sizes.py", "prepare_tcp_gate_retry.py"]), args.target+":"+bundle+"/"])
            ssh(["python3", bundle+"/prepare_tcp_gate_retry.py", "--root", args.root, "--bundle", bundle])
        ssh(["python3", args.root+"/tcp_protocol_gate.py", "--root", args.root, "--out", args.root+"/protocol-gate-001"])
    elif args.action == "prepare":
        ssh(["python3", args.root+"/tcp_stage.py", "--root", args.root, "--prepare-environment", "/home/cache/kvc-fair-20261020/environment-tcp"])
    elif args.action in {"smoke", "run"}:
        if args.action == "run":
            # The gate and common smoke must be terminal before performance starts.
            ssh(["python3", "-c", "import json;from pathlib import Path;p=Path("+repr(args.root)+");assert json.loads((p/'protocol-gate-001/summary.json').read_text())['valid'];s=json.loads((p/'orchestration-tcp-smoke/summary.json').read_text());assert s.get('ended') and not s.get('failed') and not s.get('fatal')"])
        command = ["python3", args.root+"/run_suite.py", "--root", args.root, "--manifest", args.root+"/products.json",
                   "--output", args.root+"/orchestration-tcp-smoke" if args.action == "smoke" else suite]
        if args.action == "smoke":
            command += ["--smoke", "--repeats", "1", "--sizes", "1024", "1048576", "--concurrencies", "2", "--duration", "1", "--warmup", "0.2"]
        ssh(command)
    else:
        command = [sys.executable, str(HERE/"workflow.py"), args.action, "--target", args.target,
                   "--remote-root", args.root, "--local-output", str(args.local_output), "--suite", "formal-tcp-001",
                   "--smoke-suite", "orchestration-tcp-smoke", "--profile-suite", "put-resource-profile-tcp-001",
                   "--environment-root", "/home/cache/kvc-fair-20261020/environment-tcp",
                   "--transport-profile", "tcp", "--report-path", "perf/kvc-tcp-20261020"]
        run(command)
        if args.action == "collect":
            destination = args.local_output / "formal-tcp-001/protocol-evidence"
            destination.mkdir(parents=True, exist_ok=True)
            run(["rsync", "-az", args.target+":"+args.root+"/protocol-gate-001/", str(destination)+"/"])


if __name__ == "__main__":
    main()
