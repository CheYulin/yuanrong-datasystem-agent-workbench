"""Build and verify the explicit tool inventory, including nested modules/assets."""
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from kvcbench.core.timing import finished, started
from kvcbench.platform.files import atomic_json, digest, identity, read_json, regular, sync_directory


from kvcbench.platform.package import INVENTORY, inventory, verify_zip


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
    verify_zip(root / 'bundle.zip', expected_manifest=manifest_path.read_bytes())
    for record in manifest['files']:
        if identity(regular(payload, record['path'])) != {key: record[key] for key in ('sha256', 'bytes', 'mode')}:
            raise ValueError('Bundle payload changed during archive validation')
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
