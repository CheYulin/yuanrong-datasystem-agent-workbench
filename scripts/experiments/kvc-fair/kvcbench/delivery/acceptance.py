"""Check a sealed local report, links and clean reproduction extraction."""
import gzip
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import stat
from urllib.parse import unquote, urlsplit
import zipfile

from kvcbench.platform.files import digest, real_directory, regular


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if values.get('id'):
            self.ids.add(values['id'])
        for attribute in ('href', 'src'):
            if values.get(attribute):
                self.links.append(values[attribute])


def manifest(root):
    root = real_directory(root)
    rows = {}
    for line in regular(root, 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split('  ', 1)
        if not re.fullmatch('[0-9a-f]{64}', checksum) or name in rows:
            raise ValueError('Invalid or duplicate report checksum')
        if digest(regular(root, name)) != checksum:
            raise ValueError('Report dependency differs from its manifest: ' + name)
        rows[name] = checksum
    observed = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name != 'SHA256SUMS'}
    if observed != set(rows) or any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Report manifest membership differs from the complete local tree')
    for name in rows:
        if name.endswith('.gz') and (root / name[:-3]).is_file():
            source = regular(root, name[:-3])
            if gzip.decompress(regular(root, name).read_bytes()) != source.read_bytes():
                raise ValueError('Report gzip companion differs: ' + name)
    return {'valid': True, 'files': len(rows), 'manifest_sha256': digest(root / 'SHA256SUMS')}


def links(root):
    root = real_directory(root)
    parsed = {}
    for path in sorted(root.rglob('*.html')):
        if 'raw' in path.relative_to(root).parts:
            continue
        document = Links()
        document.feed(path.read_text())
        parsed[path] = document
    count = 0
    for path, document in parsed.items():
        for link in document.links:
            target = urlsplit(link)
            if target.scheme or target.netloc:
                if target.scheme not in {'https', 'http', 'mailto', 'data'}:
                    raise ValueError('Unsupported document link scheme: ' + link)
                continue
            destination = (path.parent / unquote(target.path)).resolve() if target.path else path
            if root not in destination.parents or not destination.is_file():
                raise ValueError('Missing or escaping report link: ' + str(path) + ' -> ' + link)
            if target.fragment and destination in parsed and unquote(target.fragment) not in parsed[destination].ids:
                raise ValueError('Missing document fragment: ' + link)
            count += 1
    return {'valid': True, 'documents': len(parsed), 'local_links': count}


def extract(archive, destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
        raise FileExistsError('Use a fresh clean extraction directory')
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate reproduction ZIP member')
        for item in source.infolist():
            path = Path(item.filename)
            mode = item.external_attr >> 16
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename
                    or not path.parts or path.parts[0] != 'kvc-fair' and item.filename != 'bundle-manifest.json'
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError('Unsafe reproduction ZIP member: ' + item.filename)
        destination.mkdir(parents=True)
        for item in source.infolist():
            target = destination / item.filename
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as original, target.open('xb') as copied:
                    shutil.copyfileobj(original, copied)
                target.chmod((item.external_attr >> 16) & 0o777)
        for item in source.infolist():
            if item.is_dir():
                (destination / item.filename).chmod((item.external_attr >> 16) & 0o777)
    return destination / 'kvc-fair'
