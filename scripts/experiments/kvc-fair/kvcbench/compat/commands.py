"""Legacy staging arguments; all providers are explicit synchronous callables."""
import argparse
import json
from pathlib import Path

from kvcbench.build import catalog
from kvcbench.build.bundle import install_package
from kvcbench.platform.cgroup import drain
from kvcbench.run.stage import stage


def run_stage(args, *, drain_cgroups=drain):
    selected = getattr(args, "build_catalog", None)
    verifier = catalog.validate_builds if selected is None else lambda root: catalog.validate_saved_catalog(selected, root)
    return stage(args, verify_builds=verifier,
                 snapshot_builds=catalog.install_provenance,
                 install_inputs=install_package, drain_cgroups=drain_cgroups)


def stage_main(argv=None, executor=run_stage):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, default=Path("/home/worktrees/kvc-fair-tcp-20261020"))
    p.add_argument("--root", type=Path, default=Path("/home/worktrees/kvc-batch-tcp-20261020-r7"))
    p.add_argument("--mooncake-queue", type=int, default=8192)
    p.add_argument("--feature-profile", choices=["baseline", "pure-kv"], default="pure-kv")
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--cgroup", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    p.add_argument("--resource-lock", type=Path, help="Existing shared lock for this runtime resource domain")
    args = p.parse_args(argv)
    if not 0 <= args.mooncake_queue <= 65535:
        p.error("queue capacity must be 0 (native default) or at most 65535")
    record = executor(args)
    print(json.dumps({"root": record["root"], "inputs": len(record["inputs"]), "build_artifacts": len(record["build_artifacts"])}))


def bootstrap_main(argv=None, executor=run_stage):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--builds-only-native-root", "--native-root", dest="builds_only_native_root", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--build-catalog", type=Path, help="Explicit accepted catalog; rechecked before staging")
    parser.add_argument("--mooncake-queue", type=int, default=8192)
    parser.add_argument("--feature-profile", choices=["pure-kv"], default="pure-kv")
    parser.add_argument("--cgroup", type=Path, default=Path("/sys/fs/cgroup/kvcfair20261020"))
    parser.add_argument("--resource-lock", type=Path, help="Existing shared lock for this runtime resource domain")
    args = parser.parse_args(argv)
    if not 0 <= args.mooncake_queue <= 65535:
        parser.error("queue capacity must be 0 (native default) or at most 65535")
    args.base = args.builds_only_native_root
    record = executor(args)
    print(json.dumps({"root": record["root"], "mode": record["source_build_mode"], "inputs": len(record["inputs"]),
                      "native_artifacts": len(record["build_artifacts"]), "legacy_suites_required": False}))
    return 0
