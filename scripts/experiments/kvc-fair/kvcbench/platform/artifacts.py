"""Local artifact checksums, deterministic compression and command-ledger snapshots."""
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


def version_zip_href(source, checksum):
    if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
        raise ValueError("ZIP download version requires a lowercase SHA256")
    anchors = re.findall(r"<a\b[^>]*>", source, flags=re.IGNORECASE)
    links = [tag for tag in anchors if re.search(r'''\bhref\s*=\s*["'][^"']*reproduce\.zip(?:[?&#][^"']*)?["']''', tag, flags=re.IGNORECASE)]
    if len(links) != 1 or re.fullmatch(r'<a href="reproduce\.zip(?:\?sha256=[0-9a-f]{64})?" download>', links[0]) is None:
        raise ValueError("Expected exactly one reviewed reproduce.zip download anchor")
    return source.replace(links[0], '<a href="reproduce.zip?sha256=' + checksum + '" download>', 1)



def finalize_artifacts(out, gzip_paths=None, chmod_paths=None):
    if gzip_paths is None:
        gzip_paths = [path for path in sorted(out.iterdir()) if path.is_file() and path.suffix in {".html", ".js", ".css", ".json", ".jsonl", ".csv", ".md"}]
    for path in gzip_paths:
        target = Path(str(path) + ".gz")
        temporary = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(gzip.compress(path.read_bytes(), mtime=0))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    chmod_paths = None if chmod_paths is None else set(chmod_paths)
    hashes = {}
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            if chmod_paths is None or path in chmod_paths:
                path.chmod(0o644)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(8 << 20), b""):
                    digest.update(block)
            hashes[str(path.relative_to(out))] = digest.hexdigest()
    manifest = out / "SHA256SUMS"
    manifest.write_text("".join(digest + "  " + name + "\n" for name, digest in hashes.items()))
    manifest.chmod(0o644)
    return hashes



def operation_ledger(logs, cutoff_wall_ns, action):
    records, sources, seen, duplicates, excluded = [], [], set(), 0, 0
    for path in dict.fromkeys(Path(value).resolve(strict=True) for value in logs):
        content = path.read_bytes()
        sources.append({"path": str(path), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        for line_number, line in enumerate(content.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict) or "origin_log" in raw or raw.get("record_type") == "delivery_ledger_snapshot":
                raise ValueError("Use original command logs, not a delivery ledger: " + str(path))
            if (raw.get("end_wall_ns") or raw.get("start_wall_ns") or 0) > cutoff_wall_ns:
                excluded += 1
                continue
            key = (str(path), json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            records.append({**raw, "origin_log": str(path), "origin_line": line_number})
    whole, nanos = divmod(cutoff_wall_ns, 1_000_000_000)
    cutoff_utc = datetime.datetime.fromtimestamp(whole, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}Z"
    metadata = {"record_type": "delivery_ledger_snapshot", "snapshot_action": action, "cutoff_wall_ns": cutoff_wall_ns,
                "cutoff_utc": cutoff_utc, "sources": sources, "included_records": len(records),
                "duplicate_records_omitted": duplicates, "after_cutoff_records_omitted": excluded,
                "scope": "Original input records observed before this delivery action; origin_log identifies each source. This action and subsequent operations are not assigned fabricated records or durations. The first JSONL row is snapshot metadata, not a timed command."}
    return ("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in [metadata, *records])).encode("utf-8")
