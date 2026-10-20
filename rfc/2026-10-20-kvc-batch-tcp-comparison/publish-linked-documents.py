#!/usr/bin/env python3
"""Publish only the rendered reading pages, preserving frozen Markdown and results."""
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


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.kvc-reader-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
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
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--rendered', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--refresh-stylesheet', action='store_true')
    args = parser.parse_args()
    started = time.monotonic()
    original = json.loads(args.snapshot.read_text())
    bundle = json.loads((args.rendered / 'reader-build.json').read_text())
    lock = Path('/tmp/kvc-yche-publish.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX)
    site = args.site.resolve() / 'perf'
    before, changes, reports = {}, {}, []
    for name, pin in original.items():
        if args.refresh_stylesheet and not name.endswith('.md'):
            continue
        path = site / name
        if path.resolve() != path or site not in path.parents or digest(path.read_bytes()) != pin['sha256']:
            raise ValueError('Original input changed or unsafe: ' + name)
    for key in ['kvc-tcp-20261020', 'kvc-batch-tcp-20261020']:
        root = site / key
        files = [root / n for n in ['index.html', 'index.html.gz', 'SHA256SUMS']]
        prior = {p: p.read_bytes() for p in files}
        before.update(prior)
        entries = {}
        for line in prior[root / 'SHA256SUMS'].decode().splitlines():
            sha, name = line.split('  ', 1)
            if Path(name).is_absolute() or '..' in Path(name).parts or name in entries:
                raise ValueError('Unsafe/duplicate manifest path')
            entries[name] = sha
        additions = {}
        preserved = {}
        history = ('reader-history/refresh-' + digest(prior[root / 'SHA256SUMS']) if args.refresh_stylesheet
                   else 'reader-history/' + digest(prior[root / 'index.html']))
        css_names = [Path(n).name for n in bundle['files'] if n.startswith(key+'/_reader/reader-') and n.endswith('.css')]
        if len(css_names) != 1:
            raise ValueError('Expected exactly one content-addressed stylesheet')
        for name, sha in bundle['files'].items():
            relative = Path(name)
            if relative.parts[0] != key:
                continue
            source = args.rendered / relative
            if source.resolve() != source or args.rendered.resolve() not in source.parents:
                raise ValueError('Unsafe rendered source')
            existing = root / relative.relative_to(key)
            if not source.exists() and args.refresh_stylesheet:
                if existing.resolve() != existing or digest(existing.read_bytes()) != sha:
                    raise ValueError('Missing payload is not unchanged on site')
                continue
            value = source.read_bytes()
            if digest(value) != sha:
                raise ValueError('Rendered bytes changed')
            additions[relative.relative_to(key).as_posix()] = value
        for path, value in prior.items():
            additions[history + '/' + path.name] = value
        sources = [row for row in bundle['documents'] if row['source'].startswith(key + '/')]
        receipt = {'kind': 'frozen_markdown_html_delivery', 'source_documents': sources,
                   'prior_html_sha256': digest(prior[root / 'index.html']),
                   'prior_manifest_sha256': digest(prior[root / 'SHA256SUMS']),
                   'unchanged_zip_sha256': entries['reproduce.zip'],
                   'terminology': bundle['terminology'],
                   'mermaid_sha256': bundle['mermaid_sha256'],
                   'python_markdown_version': bundle['python_markdown_version'],
                   'publisher_sha256': digest(Path(__file__).read_bytes()),
                   'created_unix_ns': time.time_ns(), 'benchmark_rerun': False}
        if args.refresh_stylesheet:
            previous = root / '_reader/render-receipt.json'
            before[previous] = previous.read_bytes()
            preserved[history+'/_reader/render-receipt.json'] = before[previous]
            receipt['previous_receipt_sha256'] = digest(before[previous])
            receipt['stylesheet_refresh_only'] = True
        additions['_reader/render-receipt.json'] = (json.dumps(receipt, ensure_ascii=False, indent=2)+'\n').encode()
        for name, value in additions.items():
            target = root / name
            if target.resolve() != target or root not in target.parents:
                raise ValueError('Unsafe target')
            if target.exists() and target.read_bytes() == value:
                continue
            if target.exists() and name not in ['index.html', 'index.html.gz']:
                if not args.refresh_stylesheet:
                    raise ValueError('Refusing existing reading page: ' + name)
                old = target.read_bytes()
                if name != '_reader/render-receipt.json':
                    if not name.endswith(('.html', '.html.gz')):
                        raise ValueError('Stylesheet refresh cannot rewrite assets')
                    unpack = gzip.decompress if name.endswith('.gz') else lambda x: x
                    if unpack(old).replace(b'reader.css', css_names[0].encode()) != unpack(value):
                        raise ValueError('Stylesheet refresh attempted other document changes')
                    before[target] = old
                    preserved[history+'/'+name] = old
            changes[target] = value
            entries[name] = digest(value)
        for name, value in preserved.items():
            target = root / name
            if target.exists():
                raise ValueError('Refresh history already exists')
            changes[target] = value
            entries[name] = digest(value)
        changes[root / 'SHA256SUMS'] = ''.join(sha+'  '+name+'\n' for name, sha in sorted(entries.items())).encode()
        reports.append({'report': key, 'documents': len(sources), 'manifest_entries': len(entries),
                        'main_html_sha256': entries['index.html'],
                        'unchanged_zip_sha256': entries['reproduce.zip']})
    if args.apply:
        for path, value in before.items():
            if path.read_bytes() != value:
                raise RuntimeError('Concurrent report change')
        written = []
        try:
            for path, value in changes.items():
                if path in before and path.read_bytes() != before[path]:
                    raise RuntimeError('Concurrent change before replace')
                atomic(path, value)
                written.append(path)
            for path, value in changes.items():
                if path.read_bytes() != value:
                    raise RuntimeError('Published bytes mismatch')
        except BaseException:
            for path in reversed(written):
                if path in before:
                    atomic(path, before[path])
                else:
                    path.unlink()
            raise
    print(json.dumps({'applied': args.apply, 'reports': reports,
                      'changed_files': len(changes), 'duration_s': time.monotonic()-started}, ensure_ascii=False))


if __name__ == '__main__':
    main()
