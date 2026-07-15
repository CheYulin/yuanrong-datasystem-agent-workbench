#!/usr/bin/env python3
"""Read a blob from a commit via git object store (no git CLI)."""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

GIT_DIR = Path(r"\\wsl$\Ubuntu\home\t14s\workspace\git-repos\yuanrong-datasystem\.git")
COMMIT = sys.argv[1] if len(sys.argv) > 1 else "8578c00031e8102224ad5eb85140181f2bc0d8b6"
PATH = sys.argv[2] if len(sys.argv) > 2 else "src/datasystem/common/device/CMakeLists.txt"


def read_obj(oid: str) -> tuple[str, bytes]:
    path = GIT_DIR / "objects" / oid[:2] / oid[2:]
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
    tree_oid = commit_body.split(b"\n", 1)[0].split(b" ", 3)[2].decode()
    paths = walk_tree(tree_oid)
    if PATH not in paths:
        print(f"MISSING {PATH}")
        return 1
    typ, body = read_obj(paths[PATH])
    text = body.decode("utf-8", errors="replace")
    print(text)
    for m in ("<<<<<<<", "=======", ">>>>>>>"):
        if m in text:
            print(f"\n!!! FOUND {m} in {PATH} @ {COMMIT}")
            return 1
    print(f"\nOK: no conflict markers in {PATH} @ {COMMIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
