#!/usr/bin/env python3
"""Explicit reproducible actions; server processes and raw artifacts stay off the git repository."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time

HERE = Path(__file__).resolve().parent
TIMING_LOG = None


def run(argv):
    command = [str(a) for a in argv]
    start_wall, start_mono = time.time_ns(), time.monotonic_ns()
    status = None
    try:
        status = subprocess.run(command).returncode
        if status:
            raise subprocess.CalledProcessError(status, command)
    finally:
        if TIMING_LOG is not None:
            TIMING_LOG.parent.mkdir(parents=True, exist_ok=True)
            with TIMING_LOG.open("a") as stream:
                stream.write(json.dumps({"step":command[0]+" "+" ".join(command[1:3]),"argv":command,
                    "start_wall_ns":start_wall,"end_wall_ns":time.time_ns(),"duration_s":(time.monotonic_ns()-start_mono)/1e9,"returncode":status})+"\n")


def main():
    global TIMING_LOG
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["stage", "build", "prepare", "smoke", "run", "collect", "report", "publish", "restore", "profile"])
    parser.add_argument("--target", default="tiantiyun-80c128g")
    parser.add_argument("--publication-host", default="xqyun-32c32g")
    parser.add_argument("--remote-root", default="/home/worktrees/kvc-fair-20261020")
    parser.add_argument("--local-output", type=Path, default=Path.home()/"workspace/scratch/2026-10-20-kvc-compare")
    parser.add_argument("--suite", default="formal-001")
    parser.add_argument("--environment-root", default="/home/cache/kvc-fair-20261020/environment-reproduction")
    parser.add_argument("--smoke-suite", default="orchestration-reproduction-smoke")
    parser.add_argument("--profile-suite", default="put-resource-profile-001")
    parser.add_argument("--transport-profile", choices=["native", "tcp"], default="native")
    parser.add_argument("--report-path", default="perf/kvc-fair-20261020")
    parser.add_argument("--rfc", type=Path, default=HERE/"docs" if (HERE/"docs").is_dir() else HERE.parents[2]/"rfc/2026-10-20-kvc-fair-comparison")
    args = parser.parse_args()
    TIMING_LOG = args.local_output / "workflow-timings.jsonl"
    root = args.remote_root
    def ssh(command):
        run(["ssh",args.target,shlex.join(command)])
    if args.action == "stage":
        ssh(["mkdir","-p",root,root+"/scripts/datasystem",root+"/mooncake",root+"/lmcache",root+"/configs"])
        files = [HERE/name for name in ["benchmark.py","cgroup.py","monitor.py","run_suite.py","prepare_env.py","build_metadata.py","timed.py","capture_evidence.py","prepare_resume.py","profile_put.py","light_monitor.py","finish_environment.py"]]
        run(["scp",*files,args.target+":"+root+"/"])
        for name in ["mooncake","lmcache"]:
            run(["rsync","-a",str(HERE/name)+"/",args.target+":"+root+"/"+name+"/"])
        run(["rsync","-a",str(HERE/"datasystem")+"/",args.target+":"+root+"/scripts/datasystem/"])
        text=(HERE/"products.json").read_text().replace("/home/worktrees/kvc-fair-20261020",root)
        args.local_output.mkdir(parents=True,exist_ok=True)
        manifest=args.local_output/"products-staged.json";manifest.write_text(text)
        run(["scp",manifest,args.target+":"+root+"/products.json"])
        empty=args.local_output/"empty-config.json";empty.write_text("{}\n")
        for name in ["datasystem","mooncake","lmcache"]:
            run(["scp",empty,args.target+":"+root+"/configs/"+name+".json"])
    elif args.action == "build":
        ssh(["env","TASK_ROOT="+root,"MOONCAKE_ROOT="+root+"/mooncake","bash",root+"/mooncake/localdeps-install.sh"])
        for product,script in [("mooncake","mooncake/build.sh"),("datasystem","scripts/datasystem/build.sh"),("lmcache","lmcache/build_lmcache.sh")]:
            started = time.time_ns()
            exit_code = 0
            try:
                ssh(["env","TASK_ROOT="+root,"MOONCAKE_ROOT="+root+"/mooncake","LMCACHE_ROOT="+root+"/lmcache","bash",root+"/"+script])
            except subprocess.CalledProcessError as exc:
                exit_code = exc.returncode
                raise
            finally:
                ssh(["python3",root+"/build_metadata.py","record","--root",root,"--product",product,"--started-wall-ns",str(started),"--finished-wall-ns",str(time.time_ns()),"--build-exit",str(exit_code)])
    elif args.action == "prepare":
        ssh(["python3",root+"/prepare_env.py","--output",args.environment_root,"--apply"])
        ssh(["python3",root+"/cgroup.py","setup"])
    elif args.action in ["smoke","run"]:
        suite = root+"/"+(args.smoke_suite if args.action=="smoke" else args.suite)
        if args.action == "run":
            ssh(["python3",root+"/build_metadata.py","copy","--root",root,"--suite",suite])
        command=["python3",root+"/run_suite.py","--root",root,"--manifest",root+"/products.json","--output",suite]
        if args.action=="smoke":
            command += ["--smoke","--repeats","1","--sizes","1024","1048576","--concurrencies","2","--duration","1","--warmup","0.2"]
        ssh(command)
        if args.action == "smoke":
            ssh(["python3",root+"/build_metadata.py","smoke","--root",root,"--suite",suite])
    elif args.action == "profile":
        ssh(["python3",root+"/profile_put.py","--root",root,"--manifest",root+"/products.json","--baseline-suite",root+"/"+args.suite,"--out",root+"/"+args.profile_suite])
    elif args.action == "collect":
        ssh(["python3",root+"/capture_evidence.py","--root",root,"--suite",root+"/"+args.suite])
        destination=args.local_output/args.suite;destination.mkdir(parents=True,exist_ok=True)
        run(["rsync","-az",args.target+":"+root+"/"+args.suite+"/",str(destination)+"/"])
        profile=args.local_output/args.profile_suite;profile.mkdir(parents=True,exist_ok=True)
        run(["rsync","-az",args.target+":"+root+"/"+args.profile_suite+"/",str(profile)+"/"])
        evidence=args.local_output/"timing-inputs"
        (evidence/"environment").mkdir(parents=True,exist_ok=True)
        (evidence/args.smoke_suite).mkdir(parents=True,exist_ok=True)
        for name in ["environment-before.json","environment-cleanup.json"]:
            run(["scp",args.target+":"+args.environment_root+"/"+name,evidence/"environment"/name])
        run(["scp",args.target+":"+root+"/"+args.suite+"/environment-restored.json",evidence/"environment/environment-restored.json"])
        for name in ["summary.json","events.jsonl","plan.json"]:
            run(["scp",args.target+":"+root+"/"+args.smoke_suite+"/"+name,evidence/args.smoke_suite/name])
    elif args.action == "report":
        verification=["python3",HERE/"verify_results.py","--suite",args.local_output/args.suite,"--formal","--put-profile",args.local_output/args.profile_suite,"--out",args.local_output/args.suite/"verification-final.json"]
        interference=args.local_output/args.suite/"interference-audit.json"
        if interference.exists():
            verification += ["--interference-json",interference]
        run(verification)
        operations=[p for p in [args.local_output/"operation-timings.jsonl",TIMING_LOG] if p.exists()]
        timings=args.local_output/"timings"
        run(["python3",HERE/"collect_timings.py","--suite",args.local_output/args.suite,
             "--environment",args.local_output/"timing-inputs/environment",
             "--orchestration",args.local_output/"timing-inputs"/args.smoke_suite,"--put-profile",args.local_output/args.profile_suite,
             "--operations",*operations,"--out",timings])
        report_command=["python3",HERE/"report.py","--suite",args.local_output/args.suite,"--out",args.local_output/"report","--rfc",args.rfc,"--timings",timings/"timings.json","--raw-evidence","--put-profile",args.local_output/args.profile_suite,"--transport-profile",args.transport_profile]
        for operation_log in operations:
            report_command += ["--operation-log",operation_log]
        run(report_command)
    elif args.action == "publish":
        relative=Path(args.report_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Report must remain within the publication site")
        target="/var/www/html/"+relative.as_posix()
        run(["ssh",args.publication_host,shlex.join(["mkdir","-p",target])])
        run(["rsync","-a",str(args.local_output/"report")+"/",args.publication_host+":"+target+"/"])
        run(["scp",HERE/"publish.py",args.publication_host+":/tmp/kvc-fair-publish.py"])
        publish_command=["python3","/tmp/kvc-fair-publish.py","--report",relative.as_posix()]
        if args.transport_profile=="tcp":
            publish_command += ["--title","DataSystem × Mooncake × LMCache：同节点统一 TCP 实验"]
        run(["ssh",args.publication_host,shlex.join(publish_command)])
    elif args.action == "restore":
        ssh(["python3",root+"/finish_environment.py","--root",root,"--environment-root",args.environment_root,
             "--suite",root+"/"+args.suite,"--profile-suite",root+"/"+args.profile_suite,
             "--out",root+"/"+args.suite+"/environment-restored.json","--restore"])


if __name__ == "__main__":
    main()
