"""Verify actual public bytes and navigation against the accepted local report."""
import hashlib
from pathlib import Path
from urllib.parse import quote, urlsplit, unquote
from urllib.request import Request, urlopen

from kvcbench.delivery.registration import home_array
from kvcbench.delivery.acceptance import Links
from kvcbench.platform.files import read_json
from kvcbench.platform.files import digest


def fetch(url, *, keep=False):
    checksum = hashlib.sha256()
    content = []
    size = 0
    with urlopen(Request(url, headers={'Accept-Encoding': 'identity', 'User-Agent': 'Mozilla/5.0'}), timeout=120) as response:
        if response.status != 200:
            raise ValueError('Public dependency returned a non-success status')
        for block in iter(lambda: response.read(1 << 20), b''):
            checksum.update(block)
            size += len(block)
            if keep:
                content.append(block)
        record = {'url': response.url, 'status': response.status, 'bytes': size, 'sha256': checksum.hexdigest(),
                  'content_encoding': response.headers.get('Content-Encoding')}
    return record, b''.join(content)


def dependencies(report, expected):
    report = Path(report).resolve(strict=True)
    selected = {name for name in expected if name in {'index.html', 'index.html.gz', 'data.js', 'data.json', 'reproduce.zip'}
                or name.startswith(('assets/', 'docs/', 'provenance/')) and name != 'provenance/source-bundle.zip'}
    def include(source, link):
        uri = urlsplit(link)
        if uri.scheme or uri.netloc:
            return
        target = (source.parent / unquote(uri.path)).resolve() if uri.path else source
        if report not in target.parents:
            raise ValueError('Public report link escapes its accepted tree')
        name = target.relative_to(report).as_posix()
        if name == 'SHA256SUMS':
            return
        if name not in expected:
            raise ValueError('Public report link is not in the accepted manifest: ' + name)
        selected.add(name)
    for name in sorted(expected):
        if name.endswith('.html') and (name == 'index.html' or name.startswith('docs/')):
            page = report / name
            links = Links()
            links.feed(page.read_text())
            for link in links.links:
                include(page, link)
    data = read_json(report / 'data.json')
    for step in data.get('workflow', {}).get('steps', []):
        for link in step.get('evidence', {}).values():
            include(report / 'index.html', link)
    return sorted(selected)


def verify(base, report, relative):
    report = Path(report)
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or parsed.netloc != 'yche.me' or parsed.path.rstrip('/') != '/' + relative:
        raise ValueError('Public URL must match this report path on https://yche.me')
    base = base.rstrip('/') + '/'
    records = []
    manifest, raw = fetch(base + 'SHA256SUMS?sha256=' + digest(report / 'SHA256SUMS'), keep=True)
    if raw != (report / 'SHA256SUMS').read_bytes():
        raise ValueError('Public manifest differs from the accepted local report')
    records.append(manifest)
    expected = dict((line.split('  ', 1)[1], line.split('  ', 1)[0]) for line in raw.decode().splitlines())
    selected = dependencies(report, expected)
    for name in selected:
        record, _ = fetch(base + quote(name) + '?sha256=' + expected[name])
        if record['sha256'] != expected[name]:
            raise ValueError('Public dependency bytes differ: ' + name)
        records.append(record)
    home, content = fetch('https://yche.me/', keep=True)
    _, _, pages = home_array(content)
    if len([row for row in pages if row.get('p') == relative + '/index.html']) != 1:
        raise ValueError('Public homepage must register this report exactly once')
    records.append(home)
    return {'passed': True, 'url': base + 'index.html', 'downloads': records,
            'scope': 'Actual public manifest, front-end/doc/provenance dependencies, all linked build/step intermediate files and full reproduction ZIP SHA; exactly one public homepage registration. Raw payload files are additionally hashed on the publication host during W17.'}
