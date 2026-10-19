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
    parser.add_argument("action", choices=["stage", "build", "prepare", "smoke", "run", "collect", "report", "publish"])
    parser.add_argument("--target", default="tiantiyun-80c128g")
    parser.add_argument("--publication-host", default="xqyun-32c32g")
    parser.add_argument("--remote-root", default="/home/worktrees/kvc-fair-20261020")
    parser.add_argument("--local-output", type=Path, default=Path.home()/"workspace/scratch/2026-10-20-kvc-compare")
    parser.add_argument("--suite", default="formal-001")
    parser.add_argument("--rfc", type=Path, default=HERE/"docs" if (HERE/"docs").is_dir() else HERE.parents[2]/"rfc/2026-10-20-kvc-fair-comparison")
    args = parser.parse_args()
    TIMING_LOG = args.local_output / "workflow-timings.jsonl"
    root = args.remote_root
    def ssh(command):
        run(["ssh",args.target,shlex.join(command)])
    if args.action == "stage":
        ssh(["mkdir","-p",root,root+"/scripts/datasystem",root+"/mooncake",root+"/lmcache",root+"/configs"])
        files = [HERE/name for name in ["benchmark.py","cgroup.py","monitor.py","run_suite.py","prepare_env.py"]]
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
        # Mooncake bootstraps the identical Python used by the following products.
        for script in ["mooncake/localdeps-install.sh","mooncake/build.sh","scripts/datasystem/build.sh","lmcache/build_lmcache.sh"]:
            ssh(["env","TASK_ROOT="+root,"MOONCAKE_ROOT="+root+"/mooncake","LMCACHE_ROOT="+root+"/lmcache","bash",root+"/"+script])
    elif args.action == "prepare":
        ssh(["python3",root+"/prepare_env.py","--output","/home/cache/kvc-fair-20261020/environment-reproduction","--apply"])
        ssh(["python3",root+"/cgroup.py","setup"])
    elif args.action in ["smoke","run"]:
        command=["python3",root+"/run_suite.py","--root",root,"--manifest",root+"/products.json","--output",root+"/"+("orchestration-reproduction-smoke" if args.action=="smoke" else args.suite)]
        if args.action=="smoke":
            command += ["--smoke","--repeats","1","--sizes","1024","1048576","--concurrencies","2","--duration","1","--warmup","0.2"]
        ssh(command)
    elif args.action == "collect":
        destination=args.local_output/args.suite;destination.mkdir(parents=True,exist_ok=True)
        run(["rsync","-a",args.target+":"+root+"/"+args.suite+"/",str(destination)+"/"])
    elif args.action == "report":
        run(["python3",HERE/"report.py","--suite",args.local_output/args.suite,"--out",args.local_output/"report","--rfc",args.rfc])
    elif args.action == "publish":
        target="/var/www/html/benchmarks/kvc-fair-20261020"
        run(["ssh",args.publication_host,shlex.join(["mkdir","-p",target])])
        run(["rsync","-a",str(args.local_output/"report")+"/",args.publication_host+":"+target+"/"])
        run(["scp",HERE/"publish.py",args.publication_host+":/tmp/kvc-fair-publish.py"])
        run(["ssh",args.publication_host,"python3 /tmp/kvc-fair-publish.py"])


if __name__ == "__main__":
    main()
