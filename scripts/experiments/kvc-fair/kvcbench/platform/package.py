"""Explicit source package inventory, independent of build or report operations."""
from pathlib import Path
import hashlib
import json
import stat
import zipfile
from kvcbench.platform.files import read_json, regular

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


def verify_zip(archive, *, expected_manifest=None):
    with zipfile.ZipFile(archive) as source:
        content = source.read('bundle-manifest.json')
        if expected_manifest is not None and content != expected_manifest:
            raise ValueError('Archive manifest differs')
        manifest = json.loads(content)
        files = manifest.get('files', [])
        names = [row['path'] for row in files]
        if manifest.get('schema_version') != 1 or not names or len(names) != len(set(names)):
            raise ValueError('Source ZIP requires a complete unique manifest')
        expected = {'kvc-fair/' + name for name in names} | {'bundle-manifest.json'}
        if len(source.namelist()) != len(expected) or set(source.namelist()) != expected:
            raise ValueError('Source ZIP membership differs from its manifest')
        for row in files:
            path = Path(row['path'])
            if path.is_absolute() or '..' in path.parts or '\\' in row['path'] or path.as_posix() != row['path']:
                raise ValueError('Unsafe source ZIP entry')
            info = source.getinfo('kvc-fair/' + row['path'])
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Only regular source ZIP members are allowed')
            data = source.read(info)
            if (type(row.get('bytes')) is not int or len(data) != row['bytes']
                    or hashlib.sha256(data).hexdigest() != row['sha256'] or type(row.get('mode')) is not int
                    or (info.external_attr >> 16) & 0o7777 != row['mode']):
                raise ValueError('Source ZIP bytes/mode differ from its manifest')
    return manifest
