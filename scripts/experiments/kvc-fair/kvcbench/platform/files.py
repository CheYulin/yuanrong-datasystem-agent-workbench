"""File identities and atomic JSON publication without following artifact links."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import shutil
import tempfile


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def regular(root, relative):
    root = Path(root).resolve(strict=True)
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("Artifact path must be a nonempty safe relative path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Artifact symlink is forbidden: " + str(current))
    if not stat.S_ISREG(current.stat().st_mode):
        raise ValueError("Artifact must be a regular file: " + str(current))
    return current


def real_directory(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in [path, *path.parents]) or not path.is_dir():
        raise ValueError("Expected a real directory without symlink ancestors")
    return path.resolve(strict=True)


def identity(path):
    path = Path(path)
    before = path.stat()
    checksum = digest(path)
    after = path.stat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")
    if any(getattr(before, name) != getattr(after, name) for name in fields):
        raise ValueError("File changed while hashing: " + str(path))
    return {"sha256": checksum, "bytes": after.st_size, "mode": stat.S_IMODE(after.st_mode)}


def reference(root, relative):
    root = Path(root).resolve(strict=True)
    path = regular(root, relative)
    return {"root": str(root), "path": path.relative_to(root).as_posix(), **identity(path)}


def relocated_root(root, roots=None):
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("Artifact roots must be absolute")
    matches = []
    for original, destination in (roots or {}).items():
        original, destination = Path(original), Path(destination)
        if not original.is_absolute() or not destination.is_absolute():
            raise ValueError("Relocation roots must be explicit absolute paths")
        if root == original or original in root.parents:
            matches.append((len(original.parts), destination / root.relative_to(original)))
    return max(matches, key=lambda item: item[0])[1] if matches else root


def validate_reference(record, roots=None):
    if (not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))
            or type(record.get("bytes")) is not int or record["bytes"] < 0
            or type(record.get("mode")) is not int or not 0 <= record["mode"] <= 0o7777):
        raise ValueError("Invalid artifact identity record")
    path = regular(relocated_root(record["root"], roots), record["path"])
    actual = identity(path)
    if any(record.get(name) != actual[name] for name in actual):
        raise ValueError("Artifact identity mismatch: " + str(path))
    return path


def read_json(path):
    def invalid(value):
        raise ValueError("Non-finite JSON number: " + value)
    return json.loads(Path(path).read_text(), parse_constant=invalid)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix="." + path.name + "-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
            temporary.unlink()
        else:
            if path.is_symlink():
                raise ValueError("Refuse to replace a symlink: " + str(path))
            temporary.replace(path)
        sync_directory(path.parent)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_immutable(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    descriptor, pending = tempfile.mkstemp(prefix=path.name + ".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(pending, path)
    finally:
        os.unlink(pending)


def copy_file(source, out, relative):
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Expected regular evidence file: " + str(source))
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": relative, "sha256": digest(target), "bytes": target.stat().st_size}
