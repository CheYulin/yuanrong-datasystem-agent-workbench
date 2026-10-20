#!/usr/bin/env python3
"""Temporary builds-only argument adapter; no build or run implementation here."""
import shutil
from kvcbench.build.catalog import build_root, regular, identity, validate_builds, install_provenance
from kvcbench.platform.locks import open_lock
from kvcbench.run.stage import NATIVE_TEMPLATE_ROOT, tcp_manifest
from kvcbench.compat.commands import bootstrap_main


def main(argv=None):
    from batch_stage import stage
    return bootstrap_main(argv, executor=stage)


if __name__ == "__main__":
    raise SystemExit(main())
