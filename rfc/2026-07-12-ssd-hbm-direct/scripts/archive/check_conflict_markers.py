#!/usr/bin/env python3
"""Scan git tree at HEAD for unresolved merge conflict markers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WD = Path("/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct")
MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def main() -> int:
    out = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=WD, text=True)
    bad: list[str] = []
    for rel in out.splitlines():
        if not rel.endswith((".cmake", ".txt", ".md", ".h", ".cpp", ".bazel")):
            continue
        try:
            blob = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=WD)
        except subprocess.CalledProcessError:
            continue
        text = blob.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if any(line.startswith(m) for m in MARKERS):
                bad.append(rel)
                break
    if bad:
        print("CONFLICT_MARKERS_IN_HEAD:")
        for p in bad:
            print(p)
        return 1
    print("HEAD_CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
