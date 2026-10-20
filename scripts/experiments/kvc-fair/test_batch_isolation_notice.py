"""Temporary local erratum transactions; never touch the historical reports."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import batch_isolation_notice as subject
from report import finalize_artifacts


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in root.rglob("*") if p.is_file()}


class IsolationNoticeTests(unittest.TestCase):
    def fixture(self, parent):
        root = parent / "kvc-fair-20261020"; root.mkdir()
        (root / "index.html").write_bytes('<html>\r\n<body class="old"><main class="content-area"><header class="hero">原文</header>\r\n<a href="reproduce.zip?sha256=old" download>ZIP</a></main></body></html>'.encode())
        for name in ["reproduce.zip", "data.json", "summary.csv", "report.js", "raw-evidence.tar.gz"]:
            (root / name).write_bytes(("immutable fixture " + name).encode())
        finalize_artifacts(root)
        args = SimpleNamespace(report_dir=root, expected_manifest_sha256=subject.digest(root / "SHA256SUMS"),
                               new_report_url="https://yche.me/perf/kvc-batch-tcp-20261020/", apply=False)
        return args

    def run_notice(self, args):
        with patch.dict(subject.ALLOWED_REPORTS, {str(args.report_dir): "kvc-fair-20261020"}):
            return subject.refresh(args)

    def test_dryrun_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = snapshot(args.report_dir)
            result = self.run_notice(args)
            self.assertTrue(result["original_output_unchanged"])
            self.assertFalse(result["apply"])
            self.assertFalse(result["zip_rewritten"])
            self.assertEqual(snapshot(args.report_dir), before)

    def test_apply_only_notice_history_erratum_gzip_and_manifest_change(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = snapshot(args.report_dir); args.apply = True
            result = self.run_notice(args); after = snapshot(args.report_dir)
            for name, original in before.items():
                if name not in {"index.html", "index.html.gz", "SHA256SUMS"}:
                    self.assertEqual(after[name], original, name)
            old = before["index.html"][0]; changed = after["index.html"][0]
            self.assertEqual(changed, subject.insert_notice(old, args.new_report_url))
            self.assertIn("不再作为隔离公平基线".encode(), changed)
            self.assertIn("未修订".encode(), changed)
            self.assertLess(changed.index(b'id="isolation-erratum"'), changed.index(b'class="hero"'))
            self.assertGreater(changed.index(b'id="isolation-erratum"'), changed.index(b'<main class="content-area">'))
            self.assertEqual(after["index.html.gz"][0], gzip.compress(changed, mtime=0))
            evidence = json.loads(after["isolation-erratum.json"][0])
            self.assertFalse(evidence["original_zip"]["rewritten"])
            self.assertEqual(evidence["original_zip"]["sha256"], hashlib.sha256(before["reproduce.zip"][0]).hexdigest())
            self.assertTrue(result["immutable_files_unchanged"])
            for history in evidence["history"]:
                path = history["path"];content = after[path][0]
                self.assertEqual(hashlib.sha256(content).hexdigest(), history["sha256"])
                original_name = next(name for name in ["index.html", "index.html.gz", "SHA256SUMS"] if path.endswith("-" + name))
                self.assertEqual(content, before[original_name][0])
            self.assertEqual(set(after) - set(before), set(result["updates"]) - set(before))
            subject.verify_manifest(args.report_dir)

    def test_stale_cas_refused_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory));before = snapshot(args.report_dir)
            args.expected_manifest_sha256 = "0" * 64;args.apply = True
            with self.assertRaisesRegex(ValueError, "CAS mismatch"):
                self.run_notice(args)
            self.assertEqual(snapshot(args.report_dir), before)

    def test_unknown_directory_and_report_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory);args = self.fixture(parent)
            with self.assertRaisesRegex(ValueError, "exact historical"):
                subject.refresh(args)
            link = parent / "alias";link.symlink_to(args.report_dir, target_is_directory=True)
            original = args.report_dir;args.report_dir = link
            with patch.dict(subject.ALLOWED_REPORTS, {str(original): "kvc-fair-20261020"}), self.assertRaises(ValueError):
                subject.refresh(args)

    def test_unreviewed_or_old_report_url_refused(self):
        for url in ["http://yche.me/perf/new/", "https://other.me/perf/new/", "https://yche.me/perf/kvc-tcp-20261020/", "https://yche.me/perf/new/?x=1", "https://yche.me/perf/new/#x"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                subject.validate_url(url)

    def test_core_tamper_or_untracked_file_refused(self):
        for mode in ["tamper", "untracked"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                args = self.fixture(Path(directory));args.apply = True
                (args.report_dir / ("data.json" if mode == "tamper" else "untracked.bin")).write_bytes(b"unexpected")
                before = snapshot(args.report_dir)
                with self.assertRaises(RuntimeError):
                    self.run_notice(args)
                self.assertEqual(snapshot(args.report_dir), before)

    def test_middle_commit_failure_rolls_back_byte_modes_and_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory));args.apply = True;before = snapshot(args.report_dir)
            original = os.replace;calls = []

            def fail(source, target):
                if args.report_dir in Path(target).parents:
                    calls.append(str(target))
                    if len(calls) == 5:
                        raise OSError("synthetic commit interruption")
                return original(source, target)

            with patch.object(subject.os, "replace", side_effect=fail), self.assertRaises(OSError):
                self.run_notice(args)
            self.assertGreaterEqual(len(calls), 5)
            self.assertEqual(snapshot(args.report_dir), before)
            self.assertFalse((args.report_dir / "history").exists())

    def test_repeat_notice_refused_preserves_first_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory));args.apply = True;self.run_notice(args)
            args.expected_manifest_sha256 = subject.digest(args.report_dir / "SHA256SUMS")
            before = snapshot(args.report_dir)
            with self.assertRaises(ValueError):
                self.run_notice(args)
            self.assertEqual(snapshot(args.report_dir), before)


if __name__ == "__main__":
    unittest.main()
