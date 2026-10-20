"""Read-only local delivery inventory and SHA manifest checks."""
import hashlib
from pathlib import Path
import stat
import re


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            result.update(block)
    return result.hexdigest()



def inventory(out):
    result = {}
    for path in sorted(out.rglob("*")):
        info = path.lstat()
        if path.is_symlink() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise RuntimeError("Only regular files/directories are allowed in delivery: " + str(path))
        if path.is_file():
            result[path.relative_to(out).as_posix()] = {"sha256": digest(path), "mode": stat.S_IMODE(info.st_mode)}
    return result



def verify_manifest(out):
    manifest = out / "SHA256SUMS"
    if not manifest.is_file() or manifest.is_symlink():
        raise RuntimeError("An existing regular SHA256SUMS is required")
    expected = {}
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        checksum, separator, name = line.partition("  ")
        relative = Path(name)
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", checksum) or relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or name in expected or name == "SHA256SUMS":
            raise RuntimeError("Invalid or duplicate SHA256SUMS entry: " + line)
        expected[name] = checksum
    observed = inventory(out)
    if set(observed) != set(expected) | {"SHA256SUMS"}:
        raise RuntimeError("Delivery file set differs from SHA256SUMS; untracked/missing files must be reviewed first")
    for name, checksum in expected.items():
        if observed[name]["sha256"] != checksum:
            raise RuntimeError("Original artifact checksum mismatch: " + name)
    return observed
