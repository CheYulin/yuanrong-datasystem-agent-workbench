#!/usr/bin/env python3
"""Append documentation to the two existing reports; preserve measurements and ZIPs."""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


def digest(value):
    return hashlib.sha256(value).hexdigest()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.appendix-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site', type=Path, default=Path('/var/www/html'))
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    started = time.monotonic()
    payload = args.payload.resolve()
    pins = json.loads((payload / 'payload-sha256.json').read_text())
    assets = {}
    for name, sha in pins.items():
        source = payload / name
        if source.resolve() != source or payload not in source.parents:
            raise ValueError('Unsafe payload path')
        value = source.read_bytes()
        if digest(value) != sha:
            raise ValueError('Payload changed: ' + name)
        assets[name] = value
    fragment = assets.pop('appendix.html').decode()
    lock = Path('/tmp/kvc-yche-publish.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX)
    snapshots, updates, reports = {}, {}, []
    for key, scope in [
        ('kvc-tcp-20261020', '本页为旧单 key TCP 实验。下面的 tag 与编译记录对应其复用的构建物；A.3 左列是本页配置，右列仅用于说明后续 R7 的改进，不用于解释本页原始性能。'),
        ('kvc-batch-tcp-20261020', '本页为 R7 原生批量 TCP / pure-kv 实验。A.3 右列是本页实际配置；左列保留旧单 key TCP 的差异，避免将后续关闭项倒写进历史。')]:
        root = args.site.resolve() / 'perf' / key
        paths = [root / name for name in ['index.html', 'index.html.gz', 'SHA256SUMS']]
        if any(p.is_symlink() or not p.is_file() for p in paths):
            raise ValueError('Unsafe original report')
        old = {p: p.read_bytes() for p in paths}
        snapshots.update(old)
        entries = {}
        for line in old[root / 'SHA256SUMS'].decode().splitlines():
            sha, name = line.split('  ', 1)
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or name in entries:
                raise ValueError('Unsafe/duplicate manifest path')
            entries[name] = sha
        for name in ['index.html', 'index.html.gz']:
            if digest(old[root / name]) != entries[name]:
                raise ValueError('Original manifest mismatch')
        if gzip.decompress(old[root / 'index.html.gz']) != old[root / 'index.html']:
            raise ValueError('Original gzip mismatch')
        text = old[root / 'index.html'].decode()
        if 'id="build-appendix"' in text or text.count('<footer>') != 1:
            raise ValueError('Already appended or ambiguous footer')
        text = text.replace('<footer>', fragment.replace('{{SCOPE}}', scope) + '\n<footer>', 1)
        if key == 'kvc-batch-tcp-20261020':
            begin = text.index('<nav id="chapters"')
            end = text.index('</nav>', begin)
            text = text[:end] + '<a href="#build-appendix">附录 A　构建、版本与配置</a>' + text[end:]
        # Visible shortcut also works with the shared reader's generated mobile navigation.
        end_hero = text.index('</h1>') + len('</h1>')
        text = text[:end_hero] + '<p><a href="#build-appendix">附录 A：构建步骤 / tag / 关键配置 ↓</a></p>' + text[end_hero:]
        additions = {'index.html': text.encode(), 'index.html.gz': gzip.compress(text.encode(), mtime=0)}
        history = 'build-appendix/history/' + digest(old[root / 'index.html'])
        for p, value in old.items():
            additions[history + '/' + p.name] = value
        for name, value in assets.items():
            additions['build-appendix/' + name] = value
        receipt = {'kind': 'documentation_appendix_update', 'report': key,
                   'prior_html_sha256': digest(old[root / 'index.html']),
                   'prior_manifest_sha256': digest(old[root / 'SHA256SUMS']),
                   'unchanged_zip_sha256': entries['reproduce.zip'],
                   'scope': scope, 'native_benchmark_rerun': False,
                   'previous_acceptance_scope': 'Prior page; receipt bytes retained unchanged',
                   'script_sha256': digest(Path(__file__).read_bytes()),
                   'payload_sha256': pins, 'created_unix_ns': time.time_ns()}
        additions['build-appendix/update.json'] = (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode()
        for name, value in additions.items():
            target = root / name
            if name not in ['index.html', 'index.html.gz'] and target.exists():
                raise ValueError('Refusing existing addition: ' + str(target))
            if target.resolve() != target:
                raise ValueError('Symlink path')
            updates[target] = value
            entries[name] = digest(value)
        updates[root / 'SHA256SUMS'] = ''.join(sha + '  ' + name + '\n' for name, sha in sorted(entries.items())).encode()
        reports.append({'report': key, 'old_html_sha256': receipt['prior_html_sha256'],
                        'new_html_sha256': digest(additions['index.html']),
                        'manifest_entries': len(entries), 'unchanged_zip_sha256': entries['reproduce.zip']})
    if args.apply:
        for path, value in snapshots.items():
            if path.read_bytes() != value:
                raise RuntimeError('Concurrent report modification')
        written = []
        try:
            for path, value in updates.items():
                if path in snapshots and path.read_bytes() != snapshots[path]:
                    raise RuntimeError('Concurrent modification before replace')
                atomic(path, value)
                written.append(path)
            for path, value in updates.items():
                if path.read_bytes() != value:
                    raise RuntimeError('Published bytes differ')
        except BaseException:
            for path in reversed(written):
                if path in snapshots:
                    atomic(path, snapshots[path])
                else:
                    path.unlink()
            raise
    print(json.dumps({'applied': args.apply, 'reports': reports,
                      'written_files': len(updates), 'duration_s': time.monotonic() - started}, ensure_ascii=False))


if __name__ == '__main__':
    main()
