#!/usr/bin/env python3
"""Run on the publication host; register exactly this report and preserve all other pages."""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def atomic(path, content, expected=None):
    if expected is not None and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Concurrent modification, retry against fresh baseline: " + str(path))
    handle, tmp = tempfile.mkstemp(prefix=".kvc-publish-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.chmod(tmp, 0o644)
        if expected is not None and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Concurrent modification before replace: " + str(path))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def register_home(text, entry):
    match = re.search(r"var P\s*=\s*", text)
    if not match:
        raise RuntimeError("Homepage P array not found")
    pages, consumed = json.JSONDecoder().raw_decode(text[match.end():])
    same = [p for p in pages if p.get("p") == entry["p"]]
    if len(same) > 1:
        raise RuntimeError("Existing duplicated report registration")
    if same:
        return text, len(pages), len(pages)
    start = match.end() + 1
    updated = text[:start] + "\n" + json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "," + text[start:]
    checked, _ = json.JSONDecoder().raw_decode(updated[match.end():])
    if checked[1:] != pages or checked[0] != entry:
        raise AssertionError("Homepage unrelated entries changed")
    return updated, len(pages), len(checked)


def register_metadata(text, key, value):
    data = json.loads(text)
    if key not in data:
        start = text.index("{") + 1
        return text[:start] + "\n" + json.dumps(key) + ":" + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "," + text[start:]
    if data[key] == value:
        return text
    found = re.search(re.escape(json.dumps(key)) + r"\s*:\s*", text)
    old, length = json.JSONDecoder().raw_decode(text[found.end():])
    result = text[:found.end()] + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + text[found.end()+length:]
    check = json.loads(result)
    del check[key]; del data[key]
    if check != data:
        raise AssertionError("Document metadata unrelated entries changed")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=Path("/var/www/html"))
    parser.add_argument("--report", default="perf/kvc-fair-20261020")
    parser.add_argument("--title", default="DataSystem × Mooncake × LMCache：同节点 CPU 实验")
    parser.add_argument('--date', default='2026-10-20')
    parser.add_argument('--record', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', args.date):
        raise ValueError('Publication date must use YYYY-MM-DD')
    site = args.site.resolve()
    report = (site / args.report).resolve()
    if site not in report.parents:
        raise ValueError("Report path escapes site")
    if not (report / "index.html").is_file() or not (report / "SHA256SUMS").is_file():
        raise RuntimeError("Upload full report and manifest before registration")
    for line in (report / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        path = (report / name).resolve()
        if report not in path.parents or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Report dependency hash mismatch: " + name)
    entry = {"t":args.title, "p":args.report+"/index.html", "d":args.date, "u":args.date, "c":"perf", "g":"bench", "e":"发布源码构建、双服务 exist/put/get、cgroup CPU/内存/I/O 与可复现脚本", "tg":["DataSystem","Mooncake","LMCache","公平压测"]}
    metadata = {"c":"perf","g":"bench","group":"压测与性能基线","d":args.date,"u":args.date,"title":entry["t"],"parent":None}
    lock = Path("/tmp/kvc-yche-publish.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    homepage = site / "index.html"
    original = homepage.read_bytes()
    text, before, after = register_home(original.decode(), entry)
    index = site / "assets/js/document-index-20261011.json"
    index_original = index.read_bytes()
    index_text = register_metadata(index_original.decode(), entry["p"], metadata)
    audit = args.record or report / "publication.json"
    previous = {"homepage_before_sha256":hashlib.sha256(original).hexdigest(),"metadata_before_sha256":hashlib.sha256(index_original).hexdigest(),"pages_before":before,"pages_after":after}
    atomic(homepage, text.encode(), previous["homepage_before_sha256"])
    atomic(index, index_text.encode(), previous["metadata_before_sha256"])
    for path in [homepage,index,report/"index.html"]:
        companion = Path(str(path)+".gz")
        current = path.read_bytes()
        try:
            matches = companion.exists() and gzip.decompress(companion.read_bytes()) == current
        except (OSError, EOFError):
            matches = False
        if not matches:
            atomic(companion,gzip.compress(current,mtime=0))
    previous.update(url="https://yche.me/"+entry["p"],homepage_after_sha256=hashlib.sha256(homepage.read_bytes()).hexdigest(),metadata_after_sha256=hashlib.sha256(index.read_bytes()).hexdigest())
    atomic(audit,json.dumps(previous,ensure_ascii=False,indent=2).encode())
    print(json.dumps(previous,ensure_ascii=False))


if __name__ == "__main__":
    main()
