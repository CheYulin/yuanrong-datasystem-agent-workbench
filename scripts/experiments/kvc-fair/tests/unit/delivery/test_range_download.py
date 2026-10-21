"""Archive transfer protocol, whole-file integrity and output ownership contracts."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from kvcbench.delivery import public, range_download


class RangeDownloadTests(unittest.TestCase):
    url = 'https://example.invalid/artifact.zip'
    data = bytes(range(19))

    def opener(self, fault=None):
        self.requests = []
        def open_response(request, timeout):
            headers = {name.lower(): value for name, value in request.header_items()}
            self.requests.append(headers)
            start, end = map(int, re.fullmatch(r'bytes=(\d+)-(\d+)', headers['range']).groups())
            body = self.data[start:end + 1]
            response_headers = {'Content-Range': f'bytes {start}-{end}/{len(self.data)}',
                                'Content-Length': str(len(body)), 'ETag': '"frozen"'}
            status = 206
            if fault == 'ignored':
                status = 200
            elif fault == 'range':
                response_headers['Content-Range'] = 'bytes 0-0/999'
            elif fault == 'length':
                response_headers['Content-Length'] = '999'
            elif fault == 'short':
                body = body[:-1]
            elif fault == 'extra':
                del response_headers['Content-Length']
                body += b'x'
            elif fault == 'encoding':
                response_headers['Content-Encoding'] = 'gzip'
            elif fault == 'etag' and start:
                response_headers['ETag'] = '"changed"'
            stream = io.BytesIO(body)
            stream.status, stream.url, stream.headers = status, self.url, response_headers
            return stream
        return open_response

    def test_all_offsets_whole_digest_sink_and_detached_progress(self):
        sink = io.BytesIO()
        progress = []
        def observe(part):
            progress.append(dict(part))
            part['bytes'] = 0
        with patch.object(range_download, 'urlopen', side_effect=self.opener()):
            result = range_download.download(self.url, len(self.data), hashlib.sha256(self.data).hexdigest(),
                                             segment_bytes=5, sink=sink, progress=observe)
        self.assertEqual(sink.getvalue(), self.data)
        self.assertEqual(result['bytes'], len(self.data))
        self.assertEqual([(p['start'], p['end']) for p in result['parts']], [(0, 4), (5, 9), (10, 14), (15, 18)])
        self.assertEqual(sum(p['bytes'] for p in result['parts']), len(self.data))
        self.assertEqual(len(progress), 4)
        self.assertTrue(all(r['cache-control'] == 'no-cache' for r in self.requests))
        self.assertTrue(all(r['if-range'] == '"frozen"' for r in self.requests[1:]))

    def test_ignored_wrong_truncated_overlong_encoded_or_changed_ranges_fail(self):
        for fault in ('ignored', 'range', 'length', 'short', 'extra', 'encoding', 'etag'):
            with self.subTest(fault=fault), patch.object(range_download, 'urlopen', side_effect=self.opener(fault)):
                with self.assertRaises(ValueError):
                    range_download.download(self.url, len(self.data), hashlib.sha256(self.data).hexdigest(), segment_bytes=5)

    def test_wrong_whole_hash_fails_after_actual_complete_download(self):
        with patch.object(range_download, 'urlopen', side_effect=self.opener()):
            with self.assertRaisesRegex(ValueError, 'observed_sha256'):
                range_download.download(self.url, len(self.data), '0' * 64, segment_bytes=5)
        self.assertEqual(len(self.requests), 4)

    def test_invalid_inputs_never_request(self):
        for size, checksum, segment in [(0, '0' * 64, 5), (True, '0' * 64, 5),
                                        (19, 'bad', 5), (19, None, 5), (19, '0' * 64, 0)]:
            with self.subTest(size=size, checksum=checksum), patch.object(range_download, 'urlopen') as request:
                with self.assertRaises(ValueError):
                    range_download.download(self.url, size, checksum, segment_bytes=segment)
                request.assert_not_called()

    def test_short_sink_write_fails(self):
        class ShortSink:
            def write(self, block):
                return len(block) - 1
        with patch.object(range_download, 'urlopen', side_effect=self.opener()):
            with self.assertRaises(OSError):
                range_download.download(self.url, len(self.data), hashlib.sha256(self.data).hexdigest(),
                                         segment_bytes=5, sink=ShortSink())

    def args(self, target):
        return ['--url', self.url, '--size', str(len(self.data)), '--sha256', hashlib.sha256(self.data).hexdigest(),
                '--out', str(target)]

    def test_cli_never_overwrites_and_preserves_failed_partial(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            target = Path(directory) / 'artifact.zip'
            target.write_bytes(b'user data')
            with patch.object(range_download, 'download') as request:
                self.assertEqual(range_download.main(self.args(target)), 1)
                request.assert_not_called()
            self.assertEqual(target.read_bytes(), b'user data')
            fresh = Path(directory) / 'fresh.zip'
            def interrupted(*args, sink, **kwargs):
                sink.write(b'partial')
                raise ValueError('controlled interruption')
            with patch.object(range_download, 'download', side_effect=interrupted):
                self.assertEqual(range_download.main(self.args(fresh)), 1)
            self.assertFalse(fresh.exists())
            self.assertEqual(fresh.with_name('fresh.zip.partial').read_bytes(), b'partial')

    def test_cli_promotes_only_a_verified_complete_file(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as output:
            target = Path(directory) / 'artifact.zip'
            with patch.object(range_download, 'urlopen', side_effect=self.opener()):
                self.assertEqual(range_download.main(self.args(target)), 0)
            self.assertEqual(target.read_bytes(), self.data)
            self.assertFalse(target.with_name('artifact.zip.partial').exists())
            self.assertTrue(json.loads(output.getvalue())['valid'])

    def test_normal_get_records_length_and_rejects_incomplete_body(self):
        for advertised in (len(self.data), len(self.data) + 1):
            stream = io.BytesIO(self.data)
            stream.status, stream.url = 200, self.url
            stream.headers = {'Content-Length': str(advertised)}
            with patch.object(public, 'urlopen', return_value=stream):
                if advertised != len(self.data):
                    with self.assertRaisesRegex(ValueError, 'truncated'):
                        public.fetch(self.url)
                else:
                    record, body = public.fetch(self.url, keep=True)
                    self.assertEqual(body, self.data)
                    self.assertEqual(record['content_length'], str(advertised))
