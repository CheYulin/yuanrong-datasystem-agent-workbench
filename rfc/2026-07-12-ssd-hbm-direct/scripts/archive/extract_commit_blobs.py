#!/usr/bin/env python3
"""Extract all blobs from a git commit into worktree (no git CLI)."""
from __future__ import annotations

import hashlib
import struct
import sys
import zlib
from pathlib import Path

GIT_DIR = Path(r"\\wsl$\Ubuntu\home\t14s\workspace\git-repos\yuanrong-datasystem\.git")
WORKTREE = Path(
    r"\\wsl$\Ubuntu\home\t14s\workspace\git-repos\yuanrong-datasystem\.worktrees\ssd-hbm-direct"
)
COMMIT = sys.argv[1] if len(sys.argv) > 1 else "ce54cb3acd7f76331e0dbe29b4765764bd642d34"
PREFIXES = (
    "src/datasystem/common/device/nds/",
    "src/datasystem/common/device/hbm_ipc/",
    "src/datasystem/worker/object_cache/hbm_mapping_table.",
    "tests/ut/common/device/nds/",
    "tests/ut/common/device/hbm_ipc/",
    ".repo_context/modules/infra/common-infra.md",
)


def read_obj(oid: str) -> tuple[str, bytes]:
    path = GIT_DIR / "objects" / oid[:2] / oid[2:]
    if not path.exists():
        raise FileNotFoundError(oid)
    raw = zlib.decompress(path.read_bytes())
    nul = raw.index(b"\x00")
    hdr, body = raw[:nul], raw[nul + 1 :]
    typ = hdr.split(b" ", 1)[0].decode()
    return typ, body


def parse_tree(body: bytes) -> list[tuple[str, str, str]]:
    items = []
    i = 0
    while i < len(body):
        sp = body.index(b" ", i)
        nul = body.index(b"\x00", sp)
        mode = body[i:sp].decode()
        name = body[sp + 1 : nul].decode()
        oid = body[nul + 1 : nul + 21].hex()
        items.append((mode, name, oid))
        i = nul + 21
    return items


def walk_tree(oid: str, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    _, body = read_obj(oid)
    for mode, name, child in parse_tree(body):
        path = f"{prefix}{name}"
        if mode.startswith("40"):
            out.update(walk_tree(child, path + "/"))
        else:
            out[path] = child
    return out


def main() -> int:
    _, commit_body = read_obj(COMMIT)
    tree_line = commit_body.split(b"\n", 1)[0]
    tree_oid = tree_line.split(b" ", 3)[2].decode()
    all_paths = walk_tree(tree_oid)
    n = 0
    for path, oid in sorted(all_paths.items()):
        if not any(path.startswith(p) for p in PREFIXES) and path not in (
            "src/datasystem/common/device/BUILD.bazel",
            "src/datasystem/common/device/nds/CMakeLists.txt",
            "src/datasystem/worker/object_cache/CMakeLists.txt",
            "tests/ut/CMakeLists.txt",
        ):
            continue
        typ, body = read_obj(oid)
        if typ != "blob":
            continue
        dest = WORKTREE / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        print(f"extracted {path} ({len(body)} bytes)")
        n += 1
    print(f"TOTAL {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
