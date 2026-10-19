#!/usr/bin/env python3
"""Record command wall time, status, and exact public argv for operation replay."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command required")
    start_wall, start_mono = time.time_ns(), time.monotonic_ns()
    status = None
    error = None
    try:
        status = subprocess.run(command).returncode
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        end_wall, end_mono = time.time_ns(), time.monotonic_ns()
        args.log.parent.mkdir(parents=True, exist_ok=True)
        row = {"step":args.step,"argv":command,"start_wall_ns":start_wall,"end_wall_ns":end_wall,
               "duration_s":(end_mono-start_mono)/1e9,"returncode":status,"error":error}
        with args.log.open("a") as stream:
            stream.write(json.dumps(row, ensure_ascii=False)+"\n")
    raise SystemExit(status or 0)


if __name__ == "__main__":
    main()
