#!/usr/bin/env python3
"""Append actual final acceptance receipts without changing the tested ZIP or page."""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urljoin, urlsplit


def digest(content):
    return hashlib.sha256(content).hexdigest()


def atomic(path, content):
    fd, name = tempfile.mkstemp(prefix='.acceptance-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--public-url', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    started = time.monotonic_ns()
    out = args.out.resolve(strict=True)
    lock = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from kvcbench.platform.delivery_files import inventory, verify_manifest
        before = verify_manifest(out)
        names = ['delivery-acceptance.json', 'delivery-acceptance.json.gz']
        if any((out / name).exists() for name in names):
            raise ValueError('Use a report without an existing final acceptance receipt')
        records, values = {}, {}
        for role, source in [('browser', args.browser), ('package', args.package)]:
            if source.is_symlink() or not source.is_file():
                raise ValueError('Acceptance inputs must be explicit regular files')
            source = source.resolve(strict=True)
            if out == source or out in source.parents:
                raise ValueError('Acceptance inputs must remain outside the report')
            content = source.read_bytes()
            value = json.loads(content)
            if value.get('passed') is not True:
                raise ValueError(role + ' did not pass')
            records[role] = {'sha256': digest(content), 'bytes': len(content), 'original_utf8': content.decode('utf-8')}
            values[role] = value
        browser, package = values['browser'], values['package']
        expected_url = urlsplit(args.public_url)
        actual_url = urlsplit(browser.get('url', ''))
        if (expected_url.scheme != 'https' or expected_url.hostname != 'yche.me'
                or expected_url.query or expected_url.fragment or actual_url.query or actual_url.fragment
                or browser.get('url') != args.public_url):
            raise ValueError('Browser QA did not exercise the expected public page')
        checks = browser.get('checks', [])
        if not checks or browser.get('errors') != [] or not all(check.get('pass') is True for check in checks):
            raise ValueError('Browser checks are not all successful')
        zip_checks = [check for check in checks if check.get('name') == 'versioned ZIP body SHA equals URL and unique manifest entry']
        checksum, size = before['reproduce.zip']['sha256'], (out / 'reproduce.zip').stat().st_size
        if len(zip_checks) != 1 or any(zip_checks[0].get(key) != value for key, value in [('expected', checksum), ('observed', checksum), ('bytes', size)]):
            raise ValueError('The actually downloaded ZIP is a different version')
        manifest = (out / 'SHA256SUMS').read_bytes()
        zip_url = urljoin(args.public_url, 'reproduce.zip') + '?sha256=' + checksum
        if (zip_checks[0].get('requested_url') != zip_url or zip_checks[0].get('response_url') != zip_url
                or zip_checks[0].get('manifest_sha256') != digest(manifest)):
            raise ValueError('Actual public ZIP URLs or whole public manifest differ from the local version')
        if (package.get('zip_sha256') != checksum or package.get('zip_bytes') != size
                or package.get('manifest_sha256') != digest(manifest)
                or package.get('source_unchanged') is not True
                or package.get('crc_passed') is not True
                or package.get('safe_inventory_passed') is not True):
            raise ValueError('Package QA is stale or incomplete')
        source = Path(__file__).read_bytes()
        analysis = json.loads((out / 'analysis.json').read_bytes())
        receipt = {'schema_version': 1, 'kind': 'final_delivery_acceptance', 'delivery_passed': True,
                   'native_matrix_passed': analysis.get('passed'),
                   'native_evidence_verified': analysis.get('evidence_verified'),
                   'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'public_url': args.public_url, 'tested_zip_sha256': checksum, 'tested_zip_bytes': size,
                   'pre_receipt_manifest_sha256': digest(manifest), 'browser_checks': len(checks),
                   'actual_append_source': {'sha256': digest(source), 'bytes': len(source)},
                   'receipts': records,
                   'scope': 'Actual browser and package receipts refer to the identical final ZIP. This append does not rerender the page, change the ZIP, rerun native benchmarks, or claim every native API passed. The pre-receipt manifest excludes this receipt itself.'}
        content = (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if args.apply:
            try:
                atomic(out / names[0], content)
                atomic(out / names[1], gzip.compress(content, mtime=0))
                complete = inventory(out)
                atomic(out / 'SHA256SUMS', ''.join(value['sha256'] + '  ' + name + '\n'
                                                 for name, value in sorted(complete.items()) if name != 'SHA256SUMS').encode('utf-8'))
                after = verify_manifest(out)
                if set(after) != set(before) | set(names) or any(after[name] != value for name, value in before.items() if name != 'SHA256SUMS'):
                    raise RuntimeError('An original report byte or mode changed')
                if gzip.decompress((out / names[1]).read_bytes()) != content:
                    raise RuntimeError('Receipt gzip verification failed')
            except BaseException:
                for name in names:
                    (out / name).unlink(missing_ok=True)
                atomic(out / 'SHA256SUMS', manifest)
                raise
        print(json.dumps({'apply': args.apply, 'passed': True, 'tested_zip_sha256': checksum,
                          'tested_zip_bytes': size, 'browser_checks': len(checks),
                          'original_files_preserved': len(before) - 1, 'receipt_sha256': digest(content),
                          'duration_s': (time.monotonic_ns() - started) / 1e9}, indent=2))
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
