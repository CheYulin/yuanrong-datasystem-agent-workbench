#!/usr/bin/env python3
"""Script every batch experiment phase, preserving failed commands and durations."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys

import workflow

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["stage", "prepare", "gate", "diagnose", "compose", "smoke", "collect-checks", "tcp-audit", "run", "restore", "collect", "report", "publish"])
    p.add_argument("--target", default="tiantiyun-80c128g")
    p.add_argument("--publication-host", default="xqyun-32c32g")
    p.add_argument("--root", default="/home/worktrees/kvc-batch-tcp-20261020-r3")
    p.add_argument("--base", default="/home/worktrees/kvc-fair-tcp-20261020")
    p.add_argument("--gate", default="batch-gate-001")
    p.add_argument("--mooncake-queue", type=int, default=8192)
    p.add_argument("--local-output", type=Path, default=Path.home() / "workspace/scratch/2026-10-20-kvc-batch-tcp-r3")
    p.add_argument("--environment-root", default="/home/cache/kvc-fair-20261020/environment-batch-tcp-r3")
    p.add_argument("--rfc", type=Path, default=HERE / "docs" if (HERE / "docs").is_dir() else HERE.parents[2] / "rfc/2026-10-20-kvc-batch-tcp-comparison")
    p.add_argument("--report-path", default="perf/kvc-batch-tcp-20261020")
    args = p.parse_args()
    report_path = Path(args.report_path)
    if report_path.is_absolute() or ".." in report_path.parts or report_path == Path("."):
        p.error("report path must be a nonempty relative site path")
    workflow.TIMING_LOG = args.local_output / "operation-timings.jsonl"
    run = workflow.run
    def ssh(argv):
        run(["ssh", args.target, shlex.join(str(v) for v in argv)])
    root = args.root
    if args.action == "stage":
        bundle = "/home/cache/kvc-fair-20261020/batch-script-bundle"
        ssh(["mkdir", "-p", bundle])
        run(["rsync", "-a", "--exclude", "__pycache__", str(HERE) + "/", args.target + ":" + bundle + "/"])
        ssh(["python3", bundle + "/batch_stage.py", "--base", args.base, "--root", root, "--bundle", bundle, "--mooncake-queue", args.mooncake_queue])
    elif args.action == "prepare":
        ssh(["python3", root + "/batch_environment.py", "--out", root + "/environment-before-full.json"])
        ssh(["python3", root + "/tcp_stage.py", "--root", root, "--prepare-environment", args.environment_root])
    elif args.action in {"diagnose", "compose"}:
        script = "batch_diagnostic.py" if args.action == "diagnose" else "batch_compose_gate.py"
        helper = "/home/cache/kvc-fair-20261020/batch-script-bundle/" + script
        run(["scp", HERE / script, args.target + ":" + helper])
        command = ["env", "PYTHONPATH=" + root, "python3", helper, "--root", root]
        if args.action == "diagnose":
            command += ["--out", root + "/untraced-diagnostic-001"]
        else:
            command += ["--source-gate", root + "/batch-gate-001", "--diagnostic", root + "/untraced-diagnostic-001", "--out", root + "/" + args.gate]
        ssh(command)
    elif args.action in {"gate", "smoke", "run"}:
        out = {"gate": "batch-gate-001", "smoke": "batch-smoke-001", "run": "formal-batch-001"}[args.action]
        if args.action == "run":
            ssh(["python3", "-c", "import json;from pathlib import Path;p=Path(" + repr(root) + ");assert json.loads((p/'batch-smoke-001/summary.json').read_text())['valid']"])
        ssh(["python3", root + "/batch_runner.py", args.action, "--root", root, "--out", root + "/" + out, "--gate", args.gate])
    elif args.action == "restore":
        # The generic restoration guard needs two terminal summaries; its second
        # input is our correctness gate, explicitly not a resource-profile run.
        ssh(["python3", root + "/finish_environment.py", "--root", root,
             "--suite", root + "/formal-batch-001", "--profile-suite", root + "/" + args.gate,
             "--environment-root", args.environment_root, "--out", root + "/formal-batch-001/environment-restored.json", "--restore"])
    elif args.action == "tcp-audit":
        run([sys.executable, HERE / "batch_tcp_verify.py", "--suite", args.local_output / args.gate,
             "--out", args.local_output / "datasystem-batch-tcp-verification.json"])
    elif args.action in {"collect", "collect-checks"}:
        names = ["batch-gate-001", args.gate, "batch-smoke-001"]
        if args.action == "collect":
            names += ["formal-batch-001", "builds", "run"]
        if args.gate != "batch-gate-001":
            names.append("untraced-diagnostic-001")
        for name in dict.fromkeys(names):
            destination = args.local_output / name
            destination.mkdir(parents=True, exist_ok=True)
            run(["rsync", "-az", args.target + ":" + root + "/" + name + "/", str(destination) + "/"])
        if args.action == "collect-checks":
            return
        evidence = args.local_output / "environment"
        evidence.mkdir(parents=True, exist_ok=True)
        run(["rsync", "-az", "--exclude", "*.tar.gz", args.target + ":" + args.environment_root + "/", str(evidence) + "/"])
        run(["scp", args.target + ":" + root + "/cgroup-settings.json", args.local_output / "cgroup-settings.json"])
        run(["scp", args.target + ":" + root + "/environment-before-full.json", args.local_output / "environment-before-full.json"])
        stage_path = args.local_output / "batch-stage.json"
        run(["scp", args.target + ":" + root + "/batch-stage.json", stage_path])
        stage = json.loads(stage_path.read_text())
        names = sorted(stage["inputs"])
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise ValueError("Frozen input path escaped runtime root")
        inputs = args.local_output / "runtime-inputs"
        inputs.mkdir(exist_ok=True)
        listing = args.local_output / "runtime-input-paths.txt"
        listing.write_text("\n".join(names) + "\n")
        run(["rsync", "-az", "--files-from", listing, args.target + ":" + root + "/", str(inputs) + "/"])
        for name, record in stage["inputs"].items():
            if hashlib.sha256((inputs / name).read_bytes()).hexdigest() != record["sha256"]:
                raise RuntimeError("Collected execution input changed: " + name)
    elif args.action == "report":
        command = [sys.executable, HERE / "batch_report.py", "--suite", args.local_output / "formal-batch-001",
             "--out", args.local_output / "report", "--rfc", args.rfc, "--builds", args.local_output / "builds",
             "--gate", args.local_output / args.gate, "--environment", args.local_output / "environment",
             "--operation-log", args.local_output / "operation-timings.jsonl", "--runtime-inputs", args.local_output / "runtime-inputs", "--raw-evidence"]
        history = args.local_output / "history/default-r2"
        if history.is_dir():
            command += ["--history", history]
        tcp = args.local_output / "datasystem-batch-tcp-verification.json"
        if tcp.is_file():
            command += ["--tcp-verification", tcp]
        run(command)
    elif args.action == "publish":
        run(["rsync", "-az", str(args.local_output / "report") + "/",
             args.publication_host + ":/var/www/html/" + args.report_path + "/"])
        run(["scp", HERE / "publish.py", args.publication_host + ":/tmp/kvc-batch-publish.py"])
        run(["ssh", args.publication_host, shlex.join(["python3", "/tmp/kvc-batch-publish.py", "--report", args.report_path,
                                                       "--title", "DataSystem × Mooncake × LMCache：每次调用 1–4096 keys 的 TCP 批量实验"])])


if __name__ == "__main__":
    main()
