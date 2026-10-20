#!/usr/bin/env python3
"""Synthetic delivery transactions; no report or remote operations."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import batch_repair_navigation as repair


def checksum(content):
    return hashlib.sha256(content).hexdigest()


class NavigationRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name) / "report"
        self.out.mkdir()
        html = b'<!doctype html>\r\n<html><head>' + repair.OLD_LINK + b'</head><body>\xe5\x9b\xbe</body></html>\r\n'
        files = {
            "index.html": html,
            "index.html.gz": gzip.compress(html, mtime=0),
            "batch-report.css": b".site-header{position:fixed}\n",
            "batch-report.js": b"window.fixture=true;\n",
            "reproduce.zip": b"synthetic historical ZIP bytes; must not rebuild",
            "data/data.json": b'{"fixture":true}\n',
            "raw-evidence.tar.gz": b"synthetic evidence frozen\x00\xff",
        }
        for name, content in files.items():
            p = self.out / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            p.chmod(0o644)
        self.manifest()

    def manifest(self):
        members = sorted(p for p in self.out.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
        p = self.out / "SHA256SUMS"
        p.write_text("".join(checksum(f.read_bytes()) + "  " + f.relative_to(self.out).as_posix() + "\n" for f in members))
        p.chmod(0o644)

    def args(self, apply=False):
        return argparse.Namespace(out=self.out, expected_manifest=checksum((self.out / "SHA256SUMS").read_bytes()), apply=apply)

    def snapshot(self):
        return {p.relative_to(self.out).as_posix(): (p.read_bytes(), p.stat().st_mode & 0o7777)
                for p in self.out.rglob("*") if p.is_file()}

    def assert_unchanged_after(self, error, args=None):
        before = self.snapshot()
        with self.assertRaises(error):
            repair.repair(args or self.args(True))
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_plans_exact_changes_and_leaves_all_bytes_modes(self):
        before = self.snapshot()
        result = repair.repair(self.args())
        self.assertTrue(result["original_output_unchanged"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({r["path"] for r in result["controlled_changes"]}, {"index.html", "index.html.gz", "SHA256SUMS"})
        self.assertEqual(len(result["new_files"]), 7)

    def test_apply_changes_only_exact_href_and_preserves_originals_and_history(self):
        before = self.snapshot()
        result = repair.repair(self.args(True))
        self.assertTrue(result["applied"])
        after = self.snapshot()
        for name in before.keys() - {"index.html", "index.html.gz", "SHA256SUMS"}:
            self.assertEqual(after[name], before[name], name)
        asset = result["asset"]
        expected_html = before["index.html"][0].replace(repair.OLD_LINK, b'<link rel="stylesheet" href="' + asset.encode() + b'">', 1)
        self.assertEqual(after["index.html"][0], expected_html)
        self.assertIn(b"\r\n", expected_html)
        self.assertIn("图".encode(), expected_html)
        self.assertEqual(gzip.decompress(after["index.html.gz"][0]), expected_html)
        candidate = before["batch-report.css"][0] + repair.CSS_RULE
        self.assertEqual(after[asset][0], candidate)
        self.assertEqual(gzip.decompress(after[asset + ".gz"][0]), candidate)
        self.assertIn(checksum(candidate), asset)
        for name in ("index.html", "index.html.gz", "SHA256SUMS"):
            self.assertEqual(after[result["history"] + "/" + name][0], before[name][0])
        self.assertEqual(after[result["history"] + "/executed-helper.py"][0], Path(repair.__file__).read_bytes())
        receipt = json.loads(after[result["history"] + "/repair-receipt.json"][0])
        self.assertEqual(receipt["original_manifest_sha256"], checksum(before["SHA256SUMS"][0]))
        self.assertEqual(repair.verify_manifest(self.out), repair.inventory(self.out))
        self.assertTrue(all(mode == 0o644 for _, mode in after.values()))

    def test_stale_cas_rejected_without_mutation(self):
        args = self.args(True)
        args.expected_manifest = "0" * 64
        self.assert_unchanged_after(RuntimeError, args)

    def test_duplicate_or_nonexact_stylesheet_link_rejected(self):
        original = (self.out / "index.html").read_bytes()
        for value in (original + repair.OLD_LINK, original.replace(b'href="batch-report.css"', b'href="batch-report.css?unknown=1"')):
            with self.subTest(value=value):
                (self.out / "index.html").write_bytes(value)
                (self.out / "index.html.gz").write_bytes(gzip.compress(value, mtime=0))
                self.manifest()
                self.assert_unchanged_after(ValueError)

    def test_previously_patched_original_css_is_rejected(self):
        p = self.out / "batch-report.css"
        p.write_bytes(p.read_bytes() + repair.CSS_RULE)
        self.manifest()
        self.assert_unchanged_after(ValueError)

    def test_html_gzip_mismatch_is_rejected(self):
        (self.out / "index.html.gz").write_bytes(gzip.compress(b"different", mtime=0))
        self.manifest()
        self.assert_unchanged_after(ValueError)

    def test_original_manifest_tamper_and_unlisted_file_rejected(self):
        (self.out / "data/data.json").write_bytes(b"tampered")
        self.assert_unchanged_after(RuntimeError)
        self.manifest()
        (self.out / "unlisted").write_bytes(b"unknown")
        self.assert_unchanged_after(RuntimeError)

    def test_original_nonpublic_mode_rejected(self):
        (self.out / "reproduce.zip").chmod(0o600)
        self.assert_unchanged_after(ValueError)

    def test_poststage_body_tamper_rejected_before_any_commit(self):
        validate = repair.validate_stage
        before = self.snapshot()
        def tamper(stage, expected):
            self.assertFalse((stage / "batch-report.css").exists())
            self.assertFalse((stage / "reproduce.zip").exists())
            (stage / "index.html").write_bytes((stage / "index.html").read_bytes().replace(b"</body>", b"injected</body>"))
            validate(stage, expected)
        with mock.patch.object(repair, "validate_stage", side_effect=tamper):
            with self.assertRaisesRegex(RuntimeError, "Staged repair"):
                repair.repair(self.args(True))
        self.assertEqual(self.snapshot(), before)

    def test_mid_commit_replace_error_rolls_back_bytes_modes_and_directories(self):
        before = self.snapshot()
        original_replace = repair.os.replace
        fired = False
        def failing_replace(source, destination):
            nonlocal fired
            if not fired and Path(source).parent.name == "stage" and Path(destination).name == "index.html.gz":
                fired = True
                raise OSError("synthetic commit failure")
            return original_replace(source, destination)
        with mock.patch.object(repair.os, "replace", side_effect=failing_replace):
            with self.assertRaisesRegex(OSError, "synthetic commit"):
                repair.repair(self.args(True))
        self.assertTrue(fired)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.out / "delivery-ui").exists())

    def test_post_commit_validation_error_rolls_back_manifest_too(self):
        before = self.snapshot()
        verify = repair.verify_manifest
        calls = 0
        def fail_final(path):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("synthetic final validation failure")
            return verify(path)
        with mock.patch.object(repair, "verify_manifest", side_effect=fail_final):
            with self.assertRaisesRegex(RuntimeError, "synthetic final"):
                repair.repair(self.args(True))
        self.assertEqual(calls, 3)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.out / "delivery-ui").exists())


if __name__ == "__main__":
    unittest.main()
