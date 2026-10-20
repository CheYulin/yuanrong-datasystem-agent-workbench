#!/usr/bin/env python3
"""Script every batch experiment phase, preserving failed commands and durations."""
import argparse
from pathlib import Path
import shlex
import sys

import workflow

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["stage", "prepare", "gate", "smoke", "run", "restore", "collect", "report", "publish"])
    p.add_argument("--target", default="tiantiyun-80c128g")
    p.add_argument("--publication-host", default="xqyun-32c32g")
    p.add_argument("--root", default="/home/worktrees/kvc-batch-tcp-20261020")
    p.add_argument("--base", default="/home/worktrees/kvc-fair-tcp-20261020")
    p.add_argument("--local-output", type=Path, default=Path.home() / "workspace/scratch/2026-10-20-kvc-batch-tcp")
    p.add_argument("--environment-root", default="/home/cache/kvc-fair-20261020/environment-batch-tcp")
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
        ssh(["python3", bundle + "/batch_stage.py", "--base", args.base, "--root", root, "--bundle", bundle])
    elif args.action == "prepare":
        ssh(["python3", root + "/batch_environment.py", "--out", root + "/environment-before-full.json"])
        ssh(["python3", root + "/tcp_stage.py", "--root", root, "--prepare-environment", args.environment_root])
    elif args.action in {"gate", "smoke", "run"}:
        out = {"gate": "batch-gate-001", "smoke": "batch-smoke-001", "run": "formal-batch-001"}[args.action]
        if args.action == "run":
            ssh(["python3", "-c", "import json;from pathlib import Path;p=Path(" + repr(root) + ");assert json.loads((p/'batch-smoke-001/summary.json').read_text())['valid']"])
        ssh(["python3", root + "/batch_runner.py", args.action, "--root", root, "--out", root + "/" + out])
    elif args.action == "restore":
        # The generic restoration guard needs two terminal summaries; its second
        # input is our correctness gate, explicitly not a resource-profile run.
        ssh(["python3", root + "/finish_environment.py", "--root", root,
             "--suite", root + "/formal-batch-001", "--profile-suite", root + "/batch-gate-001",
             "--environment-root", args.environment_root, "--out", root + "/formal-batch-001/environment-restored.json", "--restore"])
    elif args.action == "collect":
        for name in ["batch-gate-001", "batch-smoke-001", "formal-batch-001", "builds"]:
            destination = args.local_output / name
            destination.mkdir(parents=True, exist_ok=True)
            run(["rsync", "-az", args.target + ":" + root + "/" + name + "/", str(destination) + "/"])
        evidence = args.local_output / "environment"
        evidence.mkdir(parents=True, exist_ok=True)
        run(["rsync", "-az", "--exclude", "*.tar.gz", args.target + ":" + args.environment_root + "/", str(evidence) + "/"])
        run(["scp", args.target + ":" + root + "/cgroup-settings.json", args.local_output / "cgroup-settings.json"])
        run(["scp", args.target + ":" + root + "/environment-before-full.json", args.local_output / "environment-before-full.json"])
    elif args.action == "report":
        run([sys.executable, HERE / "batch_report.py", "--suite", args.local_output / "formal-batch-001",
             "--output", args.local_output / "report", "--rfc", args.rfc])
    elif args.action == "publish":
        run(["rsync", "-az", str(args.local_output / "report") + "/",
             args.publication_host + ":/var/www/html/" + args.report_path + "/"])
        run(["scp", HERE / "publish.py", args.publication_host + ":/tmp/kvc-batch-publish.py"])
        run(["ssh", args.publication_host, shlex.join(["python3", "/tmp/kvc-batch-publish.py", "--report", args.report_path,
                                                       "--title", "DataSystem × Mooncake × LMCache：每次调用 1–4096 keys 的 TCP 批量实验"])])


if __name__ == "__main__":
    main()
