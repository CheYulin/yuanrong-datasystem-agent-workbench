"""Download every archive byte through strict ranges and verify one whole-file SHA."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from kvcbench.core.timing import started, finished
from kvcbench.platform.files import sync_directory


def download(url, size, sha256, *, segment_bytes=64 << 20, sink=None, progress=None):
    if (type(size) is not int or size <= 0 or type(segment_bytes) is not int or segment_bytes <= 0
            or not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256)
            or not isinstance(url, str)
            or urlsplit(url).scheme != 'https' or not urlsplit(url).netloc or urlsplit(url).fragment):
        raise ValueError('An HTTPS URL, positive sizes and explicit SHA256 are required')
    begin = started()
    checksum, parts, total, etag = hashlib.sha256(), [], 0, None
    for start in range(0, size, segment_bytes):
        end = min(size, start + segment_bytes) - 1
        count, part_hash = 0, hashlib.sha256()
        part_begin = started()
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Encoding': 'identity',
                   'Cache-Control': 'no-cache', 'Range': f'bytes={start}-{end}'}
        if etag and etag.startswith('"'):
            headers['If-Range'] = etag
        with urlopen(Request(url, headers=headers), timeout=120) as response:
            content_range = response.headers.get('Content-Range')
            length = response.headers.get('Content-Length')
            observed_etag = response.headers.get('ETag')
            if (response.status != 206 or content_range != f'bytes {start}-{end}/{size}'
                    or length is not None and int(length) != end - start + 1
                    or response.headers.get('Content-Encoding') not in (None, 'identity')
                    or etag and observed_etag and etag != observed_etag):
                raise ValueError(f'Invalid public range {start}-{end}: status={response.status}, '
                                 f'Content-Range={content_range!r}, Content-Length={length!r}')
            if etag is None:
                etag = observed_etag
            remaining = end - start + 1
            while remaining:
                block = response.read(min(1 << 20, remaining))
                if not block:
                    raise ValueError(f'Truncated public range {start}-{end}: received={count}')
                checksum.update(block)
                part_hash.update(block)
                if sink is not None and sink.write(block) != len(block):
                    raise OSError('Short write to archive download sink')
                remaining -= len(block)
                count += len(block)
                total += len(block)
            if response.read(1):
                raise ValueError(f'Overlong public range {start}-{end}')
            part = {'start': start, 'end': end, 'status': response.status, 'bytes': count,
                    'content_range': content_range, 'content_length': length, 'etag': observed_etag,
                    'url': response.url, 'sha256': part_hash.hexdigest(), **finished(part_begin)}
        parts.append(part)
        if progress is not None:
            progress(dict(part))
    observed = checksum.hexdigest()
    if total != size or observed != sha256:
        raise ValueError(f'Public archive mismatch: bytes={total}/{size}, '
                         f'observed_sha256={observed}, expected_sha256={sha256}')
    return {'kind': 'complete_segmented_public_download', 'status': 'complete', 'url': url,
            'bytes': total, 'sha256': observed, 'parts': parts,
            'scope': 'Every byte in contiguous offset order; exact 206 ranges/lengths and one whole-file SHA. No retries or source-byte substitution.',
            **finished(begin)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    target = args.out.absolute()
    partial = target.with_name(target.name + '.partial')
    try:
        if target.exists() or target.is_symlink() or any(p.is_symlink() for p in target.parents):
            raise FileExistsError('Use a fresh regular output path')
        with partial.open('xb') as stream:
            result = download(args.url, args.size, args.sha256, sink=stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(partial, target)
        partial.unlink()
        sync_directory(target.parent)
        print(json.dumps({'valid': True, 'output': str(target), **result}))
        return 0
    except Exception as error:
        print(json.dumps({'valid': False, 'error': str(error), 'partial': str(partial)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
