"""Install a fresh immutable report tree, then register against a fresh site baseline."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def checksum(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            value.update(block)
    return value.hexdigest()


def install(source, site, relative, *, title, date, record, publisher):
    source, site = Path(source).resolve(strict=True), Path(site).absolute()
    part = Path(relative)
    if (part.is_absolute() or '..' in part.parts or len(part.parts) < 2 or part.parts[0] != 'perf'
            or any(p.is_symlink() for p in (site, *site.parents))):
        raise ValueError('Publication requires an explicit fresh perf path in the real site')
    hashes = {}
    if any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('Uploaded trees cannot contain symlinks')
    for line in (source / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or name in hashes:
            raise ValueError('Unsafe or duplicate upload manifest entry')
        target = source / path
        if target.is_symlink() or checksum(target) != expected:
            raise ValueError('Uploaded dependency differs: ' + name)
        hashes[name] = expected
    actual = {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file() and p.name != 'SHA256SUMS'}
    if actual != set(hashes) or any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('Uploaded tree membership differs')
    destination = site / part
    with Path('/tmp/kvc-yche-upload.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists() or destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
            raise FileExistsError('Published report version must be fresh and cannot be overwritten')
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, destination)
        subprocess.run([sys.executable, str(publisher), '--site', str(site), '--report', part.as_posix(),
                        '--title', title, '--date', date, '--record', str(record)], check=True)
    value = json.loads(Path(record).read_text())
    value.update(installed_files=len(hashes), report_manifest_sha256=hashlib.sha256((destination / 'SHA256SUMS').read_bytes()).hexdigest(),
                 installation_scope='Fresh report directory; original upload fully hashed. Homepage/metadata use publication lock and fresh CAS baseline; existing report versions never overwritten.')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'site', 'record', 'publisher'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('relative', 'title', 'date'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.source, args.site, args.relative, title=args.title, date=args.date,
                             record=args.record, publisher=args.publisher), ensure_ascii=False))


if __name__ == '__main__':
    main()
