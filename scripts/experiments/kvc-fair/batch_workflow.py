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
    p.add_argument("action", choices=["stage", "qualify", "prepare", "deadline-diagnostic", "gate", "diagnose", "compose", "smoke", "collect-checks", "collect-qualification-failure", "tcp-audit", "run", "restore", "collect", "resource-audit", "deployment-audit", "report", "publish"])
    p.add_argument("--target", default="tiantiyun-80c128g")
    p.add_argument("--publication-host", default="xqyun-32c32g")
    p.add_argument("--root", default="/home/worktrees/kvc-batch-tcp-20261020-r7")
    p.add_argument("--base", default="/home/worktrees/kvc-fair-tcp-20261020")
    p.add_argument("--builds-only-native-root")
    p.add_argument("--gate", default="batch-gate-001")
    p.add_argument("--mooncake-queue", type=int, default=8192)
    p.add_argument("--feature-profile", choices=["baseline", "pure-kv"], default="pure-kv")
    p.add_argument("--restore-phase", choices=["formal", "qualification"], default="formal")
    p.add_argument("--local-output", type=Path, default=Path.home() / "workspace/scratch/2026-10-20-kvc-batch-tcp-r7")
    p.add_argument("--environment-root", default="/home/cache/kvc-fair-20261020/environment-batch-tcp-r7")
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
    if args.action == "qualify":
        common = ["--root", root, "--base", args.base, "--target", args.target,
                  "--gate", args.gate, "--local-output", args.local_output,
                  "--environment-root", args.environment_root, "--feature-profile", args.feature_profile]
        actions = ["prepare", *(["deadline-diagnostic"] if args.feature_profile == "pure-kv" else []), "gate", "smoke", "collect-checks", "tcp-audit"]
        for action in actions:
            run([sys.executable, Path(__file__).resolve(), action, *common])
    elif args.action == "stage":
        bundle = "/home/cache/kvc-fair-20261020/batch-script-bundle"
        ssh(["mkdir", "-p", bundle])
        run(["rsync", "-a", "--exclude", "__pycache__", str(HERE) + "/", args.target + ":" + bundle + "/"])
        if args.builds_only_native_root:
            ssh(["python3", bundle + "/batch_build_stage.py", "--builds-only-native-root", args.builds_only_native_root,
                 "--root", root, "--bundle", bundle, "--mooncake-queue", args.mooncake_queue, "--feature-profile", args.feature_profile])
        else:
            ssh(["python3", bundle + "/batch_stage.py", "--base", args.base, "--root", root, "--bundle", bundle, "--mooncake-queue", args.mooncake_queue, "--feature-profile", args.feature_profile])
    elif args.action == "prepare":
        ssh(["python3", root + "/batch_environment.py", "--out", root + "/environment-before-full.json"])
        ssh(["python3", root + "/tcp_stage.py", "--root", root, "--prepare-environment", args.environment_root])
    elif args.action == "deadline-diagnostic":
        ssh(["python3", root + "/batch_ds_diagnostic_runner.py", "--root", root, "--out", root + "/deadline-diagnostic-001"])
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
        command = ["python3", root + "/batch_runner.py", args.action, "--root", root, "--out", root + "/" + out, "--gate", args.gate]
        if args.action == "run":
            command += ["--continue-readonly-errors"]
        ssh(command)
    elif args.action == "restore":
        # The generic restoration guard needs two terminal summaries; its second
        # input is our correctness gate, explicitly not a resource-profile run.
        suite = "formal-batch-001" if args.restore_phase == "formal" else args.gate
        ssh(["python3", root + "/finish_environment.py", "--root", root,
             "--suite", root + "/" + suite, "--profile-suite", root + "/" + args.gate,
             "--environment-root", args.environment_root, "--out", root + "/" + suite + "/environment-restored.json", "--restore"])
    elif args.action == "tcp-audit":
        run([sys.executable, HERE / "batch_tcp_verify.py", "--suite", args.local_output / args.gate,
             "--out", args.local_output / "datasystem-batch-tcp-verification.json"])
    elif args.action in {"collect", "collect-checks", "collect-qualification-failure"}:
        names = ["batch-gate-001", args.gate, "builds", "run"]
        if args.action != "collect-qualification-failure":
            names += ["batch-smoke-001"]
        if args.feature_profile == "pure-kv":
            names += ["deadline-diagnostic-001"]
        if args.action == "collect":
            names += ["formal-batch-001"]
        if args.gate != "batch-gate-001":
            names.append("untraced-diagnostic-001")
        for name in dict.fromkeys(names):
            destination = args.local_output / name
            destination.mkdir(parents=True, exist_ok=True)
            run(["rsync", "-az", args.target + ":" + root + "/" + name + "/", str(destination) + "/"])
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
            if "mode" in record and (inputs / name).stat().st_mode & 0o777 != record["mode"]:
                raise RuntimeError("Collected execution input permissions changed: " + name)
    elif args.action == "resource-audit":
        run([sys.executable, HERE / "batch_resource_audit.py", "--suite", args.local_output / "formal-batch-001",
             "--out", args.local_output / "resource-audit"])
    elif args.action == "deployment-audit":
        run([sys.executable, HERE / "batch_deployment_audit.py", "--root", args.local_output,
             "--out", args.local_output / "deployment-audit.json"])
    elif args.action == "report":
        command = [sys.executable, HERE / "batch_report.py", "--suite", args.local_output / "formal-batch-001",
             "--out", args.local_output / "report", "--rfc", args.rfc, "--builds", args.local_output / "builds",
             "--gate", args.local_output / args.gate, "--environment", args.local_output / "environment",
             "--operation-log", args.local_output / "operation-timings.jsonl", "--runtime-inputs", args.local_output / "runtime-inputs", "--raw-evidence"]
        history = args.local_output / "history/default-r2"
        if history.is_dir():
            command += ["--history", history]
        for path in sorted((args.local_output / "history").glob("protocol-preflight-*")):
            if path.is_dir():
                command += ["--history", path]
        for path in sorted((args.local_output / "history").glob("pure-kv-preflight-*")):
            if path.is_dir():
                command += ["--history", path]
        deadline = args.local_output / "deadline-diagnostic-001"
        if deadline.is_dir():
            command += ["--history", deadline]
        tcp_history = args.local_output / "history/tcp-proof-attempts"
        if tcp_history.is_dir():
            command += ["--history", tcp_history]
        tcp = args.local_output / "datasystem-batch-tcp-verification-v2.json"
        if not tcp.is_file():
            tcp = args.local_output / "datasystem-batch-tcp-verification.json"
        if tcp.is_file():
            command += ["--tcp-verification", tcp]
        for path in sorted((args.local_output / "history").glob("shared-rocksdb-*")):
            if path.is_dir():
                command += ["--failed-attempts", path]
        for path in sorted((args.local_output / "history").glob("native-exist-deadline-*")):
            if path.is_dir():
                command += ["--failed-attempts", path]
        resource_audit = args.local_output / "resource-audit"
        if resource_audit.is_dir():
            command += ["--resource-audit", resource_audit]
        deployment_audit = args.local_output / "deployment-audit.json"
        if deployment_audit.is_file():
            command += ["--deployment-audit", deployment_audit]
        run(command)
    elif args.action == "publish":
        run(["rsync", "-az", str(args.local_output / "report") + "/",
             args.publication_host + ":/var/www/html/" + args.report_path + "/"])
        run(["scp", HERE / "publish.py", args.publication_host + ":/tmp/kvc-batch-publish.py"])
        run(["ssh", args.publication_host, shlex.join(["python3", "/tmp/kvc-batch-publish.py", "--report", args.report_path,
                                                       "--title", "DataSystem × Mooncake × LMCache：每次调用 1–4096 keys 的 TCP 批量实验"])])


if __name__ == "__main__":
    main()
