"""Verify actual public bytes and navigation against the accepted local report."""
import hashlib
from pathlib import Path
from urllib.parse import quote, urlsplit, unquote
from urllib.request import Request, urlopen

from kvcbench.delivery.registration import home_array
from kvcbench.delivery.acceptance import Links
from kvcbench.platform.files import read_json
from kvcbench.platform.files import digest
from kvcbench.delivery import range_download


SITE_PROFILE = Path(__file__).resolve().parents[2] / 'configs/reports/yche-html-20261021.json'


def html_core(content, profile):
    for asset in profile['assets']:
        content = content.replace((asset + '?' + profile['query_prefix']).encode(), (asset + '?v=').encode())
    reader, beacon = profile['reader_block'].encode(), profile['beacon'].encode()
    counts = {'reader_blocks': content.count(reader), 'beacons': content.count(beacon)}
    while content.endswith(reader + b'\n'):
        content = content[:-len(reader) - 1]
    return content.replace(reader, b'').replace(beacon, b''), counts


def verify_html(origin, served):
    source_sha = hashlib.sha256(origin).hexdigest()
    if served == origin:
        return {'source_sha256': source_sha, 'comparison': 'exact-origin'}
    profile = read_json(SITE_PROFILE)
    if profile.get('schema_version') != 1 or profile.get('id') != 'yche-reader-20261012-cloudflare-20261021':
        raise ValueError('Unsupported known site HTML projection profile')
    expected, before = html_core(origin, profile)
    actual, after = html_core(served, profile)
    bounded = (0 <= after['reader_blocks'] - before['reader_blocks'] <= profile['maximum_added_reader_blocks']
               and 0 <= after['beacons'] - before['beacons'] <= profile['maximum_added_beacons'])
    if actual != expected or not bounded:
        raise ValueError('Public HTML differs beyond the exact known site components')
    return {'source_sha256': source_sha, 'core_sha256': hashlib.sha256(expected).hexdigest(),
            'comparison': 'exact-known-site-components-only', 'profile_sha256': digest(SITE_PROFILE),
            'origin_components': before, 'served_components': after}


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
                  'content_encoding': response.headers.get('Content-Encoding'),
                  'content_length': response.headers.get('Content-Length')}
        if record['content_length'] is not None and size != int(record['content_length']):
            raise ValueError(f'Public download truncated: {urlsplit(url).path}; '
                             f'bytes={size}/{record["content_length"]}, sha256={record["sha256"]}')
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


def verify(base, report, relative, *, on_segment=None):
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
        is_html = name.endswith('.html')
        url = base + quote(name) + '?sha256=' + expected[name]
        if name == 'reproduce.zip' and (report / name).stat().st_size >= 64 << 20:
            record = range_download.download(url, (report / name).stat().st_size,
                                             expected[name], progress=on_segment)
            content = b''
        else:
            record, content = fetch(url, keep=is_html)
        if is_html:
            origin = (report / name).read_bytes()
            if hashlib.sha256(origin).hexdigest() != expected[name]:
                raise ValueError('Local HTML differs from the accepted manifest: ' + name)
            record.update(verify_html(origin, content))
        elif record['sha256'] != expected[name]:
            raise ValueError(f'Public dependency bytes differ: {name}; bytes={record.get("bytes")}, '
                             f'observed_sha256={record["sha256"]}, expected_sha256={expected[name]}')
        records.append(record)
    home, content = fetch('https://yche.me/', keep=True)
    _, _, pages = home_array(content)
    if len([row for row in pages if row.get('p') == relative + '/index.html']) != 1:
        raise ValueError('Public homepage must register this report exactly once')
    records.append(home)
    return {'passed': True, 'url': base + 'index.html', 'downloads': records,
            'scope': 'Actual public manifest, front-end/doc/provenance dependencies, all linked build/step intermediate files and full reproduction ZIP SHA; exactly one public homepage registration. HTML retains its actual served SHA and must match the origin core after only the bounded exact known site reader/beacon additions. Other bytes remain exact. Raw payload files are additionally hashed on the publication host during W17.'}
