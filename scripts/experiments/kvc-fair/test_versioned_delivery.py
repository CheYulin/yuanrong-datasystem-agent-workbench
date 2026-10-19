#!/usr/bin/env python3
"""Tiny local delivery fixtures; no real report, service, browser or network."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import refresh_delivery
import report


class VersionHrefTests(unittest.TestCase):
    checksum = "a" * 64
    anchor = '<a href="reproduce.zip" download>'

    def test_only_reviewed_href_changes(self):
        html = '<html>原始内容 ' + self.anchor + 'ZIP</a><a href="data.json">data</a></html>'
        expected = html.replace(self.anchor, '<a href="reproduce.zip?sha256=' + self.checksum + '" download>')
        self.assertEqual(report.version_zip_href(html, self.checksum), expected)

    def test_existing_hash_replaced_and_idempotent(self):
        html = '<a href="reproduce.zip?sha256=' + "b" * 64 + '" download>ZIP</a>'
        result = report.version_zip_href(html, self.checksum)
        self.assertEqual(result, '<a href="reproduce.zip?sha256=' + self.checksum + '" download>ZIP</a>')
        self.assertEqual(report.version_zip_href(result, self.checksum), result)

    def test_invalid_checksum_rejected(self):
        for value in [None, 123, b"a" * 64, "A" * 64, "a" * 63, "a" * 65, "g" * 64, "../bad"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                report.version_zip_href(self.anchor, value)

    def test_absent_and_duplicate_anchors_rejected(self):
        for html in ["no anchor", self.anchor + self.anchor,
                     self.anchor + '<a href="other/reproduce.zip" download>other</a>']:
            with self.subTest(html=html), self.assertRaises(ValueError):
                report.version_zip_href(html, self.checksum)

    def test_unreviewed_queries_and_fragments_rejected(self):
        for suffix in ["?cache=1", "?sha256=", "?sha256=" + "A" * 64,
                       "?sha256=" + "b" * 64 + "&extra=1", "#download", "?sha256=" + "b" * 64 + "#x"]:
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                report.version_zip_href('<a href="reproduce.zip' + suffix + '" download>', self.checksum)

    def test_nonexact_attributes_quotes_paths_and_case_rejected(self):
        for tag in ["<a href='reproduce.zip' download>", '<a href="reproduce.zip" class="x" download>',
                    '<a download href="reproduce.zip">', '<A href="reproduce.zip" download>',
                    '<a href="./reproduce.zip" download>', '<a href="https://example.invalid/reproduce.zip" download>']:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                report.version_zip_href(tag, self.checksum)


class RefreshVersionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kvc-versioned-delivery-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.out, self.rfc, self.scripts = [self.root / name for name in ["delivery", "rfc", "scripts"]]
        for path in [self.out, self.rfc, self.scripts, self.out / "docs"]:
            path.mkdir(parents=True, exist_ok=True)
        (self.rfc / "build.md").write_text("# Offline fixture build document\n")
        (self.out / "docs/build.md").write_bytes((self.rfc / "build.md").read_bytes())
        (self.scripts / "fixture.py").write_text("# Packaged fixture, never executed\n")
        self.html = '<html>固定正文<a href="reproduce.zip" download>ZIP</a><p>unchanged</p></html>'.encode()
        (self.out / "index.html").write_bytes(self.html)
        (self.out / "data.json").write_text('{"fixture":true}\n')
        (self.out / "report.js").write_text("// immutable fixture asset\n")
        (self.out / "tcp-verification.json").write_text('{"fixture":true,"runtime_evidence":false}\n')
        (self.out / "raw-evidence.tar.gz").write_bytes(gzip.compress(b"offline fixture only", mtime=123))
        self.log = self.root / "external-operations.jsonl"
        self.log.write_text(json.dumps({"step": "offline_fixture", "argv": ["fixture"], "start_wall_ns": 1000000000,
            "end_wall_ns": 2000000000, "duration_s": 1.0, "returncode": 0}) + "\n")
        (self.out / "operations.jsonl").write_bytes(report.operation_ledger([self.log], 3000000000, "fixture"))
        report.reproduction_archive(self.scripts, self.rfc, self.out)
        report.finalize_artifacts(self.out, gzip_paths=[self.out / name for name in
            ["index.html", "data.json", "report.js", "tcp-verification.json", "operations.jsonl", "reproduce.zip", "docs/build.md"]])
        (self.rfc / "build.md").write_text("# Refreshed offline fixture document\n")
        self.args = argparse.Namespace(out=self.out, rfc=self.rfc, scripts=self.scripts,
            operation_log=[self.log], apply=True, version_download_link=True)

    def snapshot(self):
        return {str(path.relative_to(self.out)): {"bytes": path.read_bytes(), "mode": path.stat().st_mode & 0o777}
                for path in self.out.rglob("*") if path.is_file()}

    def test_opt_in_changes_exact_href_and_deterministic_gzip_only(self):
        before = refresh_delivery.verify_manifest(self.out)
        frozen = refresh_delivery.core_inventory(before)
        result = refresh_delivery.refresh(self.args)
        after = refresh_delivery.verify_manifest(self.out)
        checksum = hashlib.sha256((self.out / "reproduce.zip").read_bytes()).hexdigest()
        expected = report.version_zip_href(self.html.decode(), checksum).encode()
        self.assertEqual((self.out / "index.html").read_bytes(), expected)
        self.assertEqual((self.out / "index.html.gz").read_bytes(), gzip.compress(expected, mtime=0))
        changed = {name for name in frozen if frozen[name] != after[name]}
        self.assertEqual(changed, {"index.html", "index.html.gz"})
        self.assertTrue(result["frozen_core_unchanged"])
        self.assertFalse(result["core_unchanged"])
        self.assertEqual(result["controlled_core_files"], ["index.html", "index.html.gz"])
        self.assertTrue(all(row["mode"] == 0o644 for row in after.values()))

    def test_default_and_legacy_namespace_keep_html_and_all_core_frozen(self):
        del self.args.version_download_link
        before = refresh_delivery.core_inventory(refresh_delivery.verify_manifest(self.out))
        result = refresh_delivery.refresh(self.args)
        self.assertTrue(result["core_unchanged"])
        self.assertEqual(before, refresh_delivery.core_inventory(refresh_delivery.verify_manifest(self.out)))
        self.assertEqual((self.out / "index.html").read_bytes(), self.html)

    def test_crlf_and_nonascii_original_bytes_preserved_except_href(self):
        original = '<html>\r\n固定正文<a href="reproduce.zip" download>下载</a>\r\n<p>unchanged</p>\r\n</html>'.encode("utf-8")
        (self.out / "index.html").write_bytes(original)
        report.finalize_artifacts(self.out, gzip_paths=[self.out / "index.html"])
        refresh_delivery.refresh(self.args)
        checksum = hashlib.sha256((self.out / "reproduce.zip").read_bytes()).hexdigest().encode()
        expected = original.replace(b'href="reproduce.zip"', b'href="reproduce.zip?sha256=' + checksum + b'"')
        self.assertEqual((self.out / "index.html").read_bytes(), expected)
        self.assertEqual(gzip.decompress((self.out / "index.html.gz").read_bytes()), expected)

    def test_dry_run_has_controlled_plan_without_any_write(self):
        self.args.apply = False
        before = self.snapshot()
        result = refresh_delivery.refresh(self.args)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(result["controlled_core_files"], ["index.html", "index.html.gz"])
        self.assertIn("index.html.gz", result["planned_updates"])

    def test_original_manifest_tampering_rejected_before_staging(self):
        (self.out / "data.json").write_text("tampered\n")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            refresh_delivery.refresh(self.args)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.root.glob(".delivery.delivery-*")), [])

    def test_unreviewed_original_html_rejected_without_writes(self):
        (self.out / "index.html").write_bytes(self.html + b'<a href="reproduce.zip" download>duplicate</a>')
        report.finalize_artifacts(self.out)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            refresh_delivery.refresh(self.args)
        self.assertEqual(self.snapshot(), before)

    def test_versioning_requires_existing_gzip(self):
        (self.out / "index.html.gz").unlink()
        report.finalize_artifacts(self.out, gzip_paths=[], chmod_paths=[])
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "existing index"):
            refresh_delivery.refresh(self.args)
        self.assertEqual(self.snapshot(), before)

    def test_controlled_html_uses_copies_while_frozen_assets_are_safe_hardlinks(self):
        before = self.snapshot()
        real_finalize = refresh_delivery.finalize_artifacts
        observed = []
        def inspect(stage, **options):
            for name in ["index.html", "index.html.gz"]:
                self.assertNotEqual((stage / name).stat().st_ino, (self.out / name).stat().st_ino)
                self.assertEqual((self.out / name).read_bytes(), before[name]["bytes"])
            for name in ["data.json", "data.json.gz", "raw-evidence.tar.gz"]:
                self.assertEqual((stage / name).stat().st_ino, (self.out / name).stat().st_ino)
            real_finalize(stage, **options)
            self.assertEqual(self.snapshot(), before)
            observed.append(True)
        with patch.object(refresh_delivery, "finalize_artifacts", side_effect=inspect):
            refresh_delivery.refresh(self.args)
        self.assertEqual(observed, [True])

    def test_commit_failure_rolls_back_html_gzip_metadata_and_modes(self):
        before = self.snapshot()
        real_replace, injected = refresh_delivery.os.replace, []
        def fail_once(source, target):
            if Path(target) == self.out / "operations.jsonl" and not injected:
                injected.append(True)
                raise OSError("offline fixture commit failure")
            return real_replace(source, target)
        with patch.object(refresh_delivery.os, "replace", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "fixture commit failure"):
                refresh_delivery.refresh(self.args)
        self.assertEqual(injected, [True])
        self.assertEqual(self.snapshot(), before)
        refresh_delivery.verify_manifest(self.out)

    def test_changed_staged_frozen_asset_refused_without_touching_original(self):
        before = self.snapshot()
        real_finalize = refresh_delivery.finalize_artifacts
        real_replace = refresh_delivery.os.replace
        def replace_stage_asset(stage, **options):
            real_finalize(stage, **options)
            pending = stage / "replacement.tmp"
            pending.write_text("changed staged asset\n")
            real_replace(pending, stage / "data.json")
            real_finalize(stage, gzip_paths=[], chmod_paths=[])
        with patch.object(refresh_delivery, "finalize_artifacts", side_effect=replace_stage_asset):
            with self.assertRaisesRegex(RuntimeError, "Staged core"):
                refresh_delivery.refresh(self.args)
        self.assertEqual(self.snapshot(), before)

    def test_extra_staged_html_body_change_refused_despite_valid_manifest(self):
        before = self.snapshot()
        real_finalize = refresh_delivery.finalize_artifacts
        def change_body(stage, **options):
            real_finalize(stage, **options)
            index = stage / "index.html"
            index.write_bytes(index.read_bytes().replace(b"<p>unchanged</p>", b"<p>unreviewed body change</p>"))
            real_finalize(stage, gzip_paths=[index], chmod_paths=[index, stage / "index.html.gz"])
            refresh_delivery.verify_manifest(stage)
        with patch.object(refresh_delivery, "finalize_artifacts", side_effect=change_body):
            with self.assertRaisesRegex(RuntimeError, "Staged core"):
                refresh_delivery.refresh(self.args)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
