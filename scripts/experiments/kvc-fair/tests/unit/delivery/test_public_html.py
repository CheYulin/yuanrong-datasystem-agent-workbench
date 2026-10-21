"""Recognize only the measured site reader/beacon additions; never discard unknown HTML."""
import unittest

from kvcbench.delivery.public import verify_html, html_core, SITE_PROFILE
from kvcbench.platform.files import read_json


class PublicHtmlTests(unittest.TestCase):
    def fixture(self):
        profile = read_json(SITE_PROFILE)
        origin = b'<html><head><title>native results</title></head><body>132 valid, 18 failed</body></html>\n'
        reader, beacon = profile['reader_block'].encode(), profile['beacon'].encode()
        served = origin.replace(b'</head>', reader + b'</head>').replace(b'</body>', beacon + b'</body>')
        served += reader + b'\n'
        for asset in profile['assets']:
            served = served.replace((asset + '?v=').encode(), (asset + '?' + profile['query_prefix']).encode())
        return origin, served, profile

    def test_exact_origin_and_only_known_site_projection_have_distinct_proof(self):
        origin, served, _ = self.fixture()
        self.assertEqual(verify_html(origin, origin)['comparison'], 'exact-origin')
        proof = verify_html(origin, served)
        self.assertEqual(proof['comparison'], 'exact-known-site-components-only')
        self.assertEqual(proof['served_components'], {'reader_blocks': 2, 'beacons': 1})
        self.assertEqual(proof['origin_components'], {'reader_blocks': 0, 'beacons': 0})

    def test_changed_data_unknown_script_or_excessive_known_injections_are_rejected(self):
        origin, served, profile = self.fixture()
        corruptions = [served.replace(b'132 valid', b'133 valid'),
                       served.replace(b'</head>', b'<script src="/unknown.js"></script></head>'),
                       served + profile['reader_block'].encode() + b'\n',
                       served.replace(b'99e478818cfd48d09ae240cc5b677c84', b'unknown-beacon-identifier')]
        for content in corruptions:
            with self.subTest(content=content[-80:]), self.assertRaises(ValueError):
                verify_html(origin, content)

    def test_existing_reader_blocks_do_not_hide_core_changes_or_whitespace(self):
        origin, _, profile = self.fixture()
        reader = profile['reader_block'].encode()
        origin = origin.replace(b'</head>', reader + b'</head>')
        served = origin.replace(b'</head>', reader + b'</head>') + reader + b'\n'
        verify_html(origin, served)
        with self.assertRaises(ValueError):
            verify_html(origin, served.replace(b'native results', b'different results'))
        with self.assertRaises(ValueError):
            verify_html(origin, served.replace(b'<body>', b'<body>unrecognized text'))
