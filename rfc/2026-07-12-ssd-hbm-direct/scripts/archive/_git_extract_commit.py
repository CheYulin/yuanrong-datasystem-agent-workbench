#!/usr/bin/env python3
"""Extract file list and contents from a git commit (for manual recovery)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

COMMIT = sys.argv[1] if len(sys.argv) > 1 else "ce54cb3acd7f76331e0dbe29b4765764bd642d34"
WD = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
    "/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct"
)


def run(*args: str) -> str:
    r = subprocess.run(args, cwd=WD, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL {args}: {r.stderr}")
    return r.stdout


def main() -> int:
    print("== abort rebase ==")
    gm = WD / ".git"
    if (gm / "rebase-merge").exists():
        run("git", "rebase", "--abort")
    git_dir = Path(run("git", "rev-parse", "--git-dir").strip())
    if (git_dir / "rebase-merge").exists():
        run("git", "rebase", "--abort")

    print("== fetch ==")
    run("git", "fetch", "main", "master")

    print("== reset to main/master ==")
    run("git", "checkout", "-B", "feat/ssd-hbm-direct", "main/master")

    for sha in (
        "dd8ce71d44a3a3180c49806e5a184e325b341a9f",
        "ce54cb3acd7f76331e0dbe29b4765764bd642d34",
    ):
        print(f"== cherry-pick {sha} ==")
        r = subprocess.run(["git", "cherry-pick", sha], cwd=WD, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            conflicted = run("git", "diff", "--name-only", "--diff-filter=U").strip().splitlines()
            nds_prefixes = (
                "src/datasystem/common/device/nds/",
                "src/datasystem/common/device/hbm_ipc/",
                "src/datasystem/worker/object_cache/hbm_mapping_table.",
                "tests/ut/common/device/nds/",
                "tests/ut/common/device/hbm_ipc/",
                ".repo_context/",
            )
            for f in conflicted:
                if not f:
                    continue
                if any(f.startswith(p) for p in nds_prefixes) or f.endswith("CMakeLists.txt") or f.endswith(
                    "BUILD.bazel"
                ):
                    print(f"keep: {f}")
                else:
                    print(f"theirs main/master: {f}")
                    subprocess.run(["git", "checkout", "main/master", "--", f], cwd=WD, check=False)
            run("git", "add", "-A")
            run("git", "cherry-pick", "--continue")

    print("== push ==")
    run("git", "push", "origin", "feat/ssd-hbm-direct", "--force-with-lease")
    print("HEAD", run("git", "rev-parse", "HEAD").strip())
    print("COUNT", run("git", "rev-list", "--left-right", "--count", "main/master...HEAD").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
