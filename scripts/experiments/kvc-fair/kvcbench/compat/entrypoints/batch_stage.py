#!/usr/bin/env python3
"""Temporary stage entry; runtime logic and build providers have separate owners."""
import shutil
from kvcbench.platform.cgroup import drain
from kvcbench.run.stage import copy_datasystem_runtime
from kvcbench.compat.commands import run_stage, stage_main


def stage(args):
    return run_stage(args, drain_cgroups=drain)


def main(argv=None):
    return stage_main(argv, executor=stage)


if __name__ == "__main__":
    main()
