"""Build and verify the explicit tool inventory, including nested modules/assets."""
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from kvcbench.core.timing import finished, started
from kvcbench.platform.files import atomic_json, digest, identity, read_json, regular, sync_directory


INVENTORY = "bundle-inputs.json"


def inventory(root):
    root = Path(root).resolve(strict=True)
    record = read_json(regular(root, INVENTORY))
    paths = record.get("paths", [])
    if record.get("schema_version") != 1 or not paths or paths != sorted(set(paths)) or INVENTORY not in paths:
        raise ValueError("Bundle inventory must be a sorted, unique, versioned complete list")
    for name in paths:
        path = Path(name)
        if any(part.startswith(".") or part in {"__pycache__", "venv", "venv311"} for part in path.parts):
            raise ValueError("Runtime caches must not enter the source bundle")
        regular(root, name)
    package = {p.relative_to(root).as_posix() for p in (root / "kvcbench").rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    if package - set(paths):
        raise ValueError("Bundle inventory omits package files: " + repr(sorted(package - set(paths))))
    return paths


def install_package(source, destination):
    source, destination = Path(source), Path(destination)
    if not (source / INVENTORY).exists():
        if (source / "kvcbench").exists():
            raise ValueError("A modular source package requires its explicit inventory")
        return []
    installed = []
    for name in inventory(source):
        if Path(name).parts[0] not in {"kvcbench", "configs"}:
            continue
        original = regular(source, name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        if identity(original) != identity(target):
            raise ValueError("Installed package differs: " + name)
        installed.append(name)
    return installed


def verify(root):
    root = Path(root).resolve(strict=True)
    manifest_path = regular(root, "manifest.json")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 1 or not manifest.get("files"):
        raise ValueError("Unsupported/empty bundle manifest")
    names = [item["path"] for item in manifest["files"]]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate bundle paths")
    payload = root / "payload"
    for record in manifest["files"]:
        actual = identity(regular(payload, record["path"]))
        if any(record.get(key) != value for key, value in actual.items()):
            raise ValueError("Bundle payload mismatch: " + record["path"])
    listed = inventory(payload)
    actual_files = {p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_file()}
    if set(names) != set(listed) or actual_files != set(names):
        raise ValueError("Bundle inventory and payload disagree")
    archived = read_json(regular(root, "archive.json"))
    if identity(regular(root, "bundle.zip")) != archived["identity"] or archived["manifest_sha256"] != digest(manifest_path):
        raise ValueError("Archive provenance mismatch")
    with zipfile.ZipFile(root / "bundle.zip") as source:
        expected = {"kvc-fair/" + name for name in names} | {"bundle-manifest.json"}
        if len(source.namelist()) != len(expected) or set(source.namelist()) != expected:
            raise ValueError("Archive membership differs from the complete bundle")
        if source.read("bundle-manifest.json") != manifest_path.read_bytes():
            raise ValueError("Archive manifest differs")
        for record in manifest["files"]:
            name = "kvc-fair/" + record["path"]
            info = source.getinfo(name)
            if ((info.external_attr >> 16) & 0o7777) != record["mode"] or source.read(name) != (payload / record["path"]).read_bytes():
                raise ValueError("Archive bytes/mode mismatch: " + name)
    return {"valid": True, "files": len(names), "manifest_sha256": digest(manifest_path),
            "archive_sha256": digest(root / "bundle.zip")}


def create(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Bundle output must be fresh")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Bundle output must be outside the source tree")
    paths = inventory(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".partial-" + destination.name + "-", dir=destination.parent))
    begin = started()
    try:
        payload = temporary / "payload"
        payload.mkdir()
        records = []
        for name in paths:
            original = regular(source, name)
            expected = identity(original)
            target = payload / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            if identity(target) != expected or identity(original) != expected:
                raise ValueError("Bundle source changed during copy: " + name)
            records.append({"path": name, **expected})
        atomic_json(temporary / "manifest.json", {"schema_version": 1, "files": records, **finished(begin)}, exclusive=True)
        with zipfile.ZipFile(temporary / "bundle.zip", "x", zipfile.ZIP_DEFLATED) as archive:
            archive.write(temporary / "manifest.json", "bundle-manifest.json")
            for name in paths:
                archive.write(payload / name, "kvc-fair/" + name)
        atomic_json(temporary / "archive.json", {"schema_version": 1, "identity": identity(temporary / "bundle.zip"),
                    "manifest_sha256": digest(temporary / "manifest.json")}, exclusive=True)
        verify(temporary)
        temporary.rename(destination)
        sync_directory(destination.parent)
        return destination
    except BaseException as exc:
        atomic_json(temporary / "failure.json", {"error_type": type(exc).__name__, "error": str(exc), **finished(begin)}, exclusive=True)
        raise
