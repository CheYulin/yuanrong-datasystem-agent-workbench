"""Bind private runtime evidence to the exact owning job and its terminal receipt."""
from pathlib import Path
import gzip
import shutil
from .remote_support import digest


def manifest(job):
    root = Path(job)
    rows = []
    candidates = sorted(root.rglob('*'))
    for original in candidates:
        if original.is_symlink() or not original.is_file() or any(p.is_symlink() for p in original.parents):
            continue
        name = str(original.relative_to(root))
        if original.suffix not in {'.json', '.jsonl', '.log', '.bt', '.txt'} or name in {'complete.json', 'artifact-manifest.json', 'events.jsonl'} or name.startswith('_evidence/'):
            continue
        path = original
        if path.suffix == '.log':
            path = root / '_evidence' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, path)
        if path.suffix in {'.jsonl', '.log'} and path.stat().st_size > 8 << 20:
            destination = path.with_name(path.name + '.gz')
            with path.open('rb') as source, gzip.open(destination, 'wb') as target:
                shutil.copyfileobj(source, target)
            path = destination
        size = path.stat().st_size
        rows.append({'name': str(path.relative_to(root)), 'source': name, 'size': size,
                     'downloadable': size <= 16 << 20, 'sha256': digest(path) if size <= 16 << 20 else None})
    return rows
