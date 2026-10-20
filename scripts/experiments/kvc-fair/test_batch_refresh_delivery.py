"""Temporary offline delivery transactions; no report rendering or remote action."""
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
import warnings
import zipfile

import batch_refresh_delivery as subject
import report


def snapshot(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            for path in root.rglob("*") if path.is_file()}


class BatchRefreshTests(unittest.TestCase):
    def fixture(self, parent):
        out, rfc = parent / "report", parent / "rfc"
        out.mkdir(); rfc.mkdir()
        (rfc / "build.md").write_bytes("# 更新构建说明\r\n".encode())
        (out / "docs").mkdir()
        (out / "docs/build.md").write_bytes(b"# Original document\n")
        (out / "data.json").write_bytes(b'{"frozen":true}\n')
        (out / "raw-evidence.tar.gz").write_bytes(b"frozen raw archive placeholder\n")
        (out / "operations.jsonl").write_bytes(b'{"original_ledger":true}\n')
        (out / "proof.json").write_bytes(b'{"raw_proof":true}\n')
        with zipfile.ZipFile(out / "reproduce.zip", "w") as archive:
            archive.comment = b"Original archive comment"
            for name, content in {"batch_report.py": b"# ORIGINAL generator source\n",
                                  "measured-runtime/batch_benchmark.py": b"# frozen actually measured source\n",
                                  "proof/trace.txt": b"raw evidence\r\n",
                                  "docs/build.md": b"# Original document\n",
                                  "operations.jsonl": b'{"original_ledger":true}\n'}.items():
                info = zipfile.ZipInfo("kvc-fair/" + name, date_time=(2024, 2, 4, 6, 8, 10))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o750) << 16
                info.internal_attr = 1
                info.compress_type = zipfile.ZIP_STORED if name.endswith(".py") else zipfile.ZIP_DEFLATED
                info.extra = b"\x01\x99\x02\x00xy"
                info.comment = b"member comment " + name.encode()
                archive.writestr(info, content)
            info = zipfile.ZipInfo("kvc-fair/extras/original.bin", date_time=(2020, 1, 2, 3, 4, 6))
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, b"unlisted additional original member\x00\xff")
        checksum = hashlib.sha256((out / "reproduce.zip").read_bytes()).hexdigest()
        html = '<!doctype html>\r\n<title>原始页面</title>\r\n<a href="reproduce.zip?sha256=' + checksum + '" download>下载</a>\r\n'
        (out / "index.html").write_bytes(html.encode())
        report.finalize_artifacts(out)
        args = SimpleNamespace(out=out, rfc=rfc, operation_log=[], delivery_tool=[], apply=False)
        return args

    def test_dryrun_leaves_all_original_bytes_and_modes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = snapshot(args.out)
            result = subject.refresh(args)
            self.assertFalse(result["apply"])
            self.assertTrue(result["original_output_unchanged"])
            self.assertEqual(snapshot(args.out), before)

    def test_apply_updates_docs_while_all_frozen_core_bytes_modes_stay_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = snapshot(args.out)
            args.apply = True
            result = subject.refresh(args)
            self.assertTrue(result["frozen_core_unchanged"])
            after = snapshot(args.out)
            for name, original in before.items():
                if not subject.mutable(name) and name not in {"index.html", "index.html.gz"}:
                    self.assertEqual(after[name], original, name)
            self.assertEqual((args.out / "docs/build.md").read_bytes(), (args.rfc / "build.md").read_bytes())
            self.assertEqual(gzip.decompress((args.out / "docs/build.md.gz").read_bytes()), (args.rfc / "build.md").read_bytes())
            subject.verify_manifest(args.out)

    def test_zip_original_generator_measured_sources_extras_and_attributes_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = subject.zip_inventory(args.out / "reproduce.zip")
            args.apply = True; subject.refresh(args)
            after = subject.zip_inventory(args.out / "reproduce.zip")
            for name, original in before.items():
                if not subject.zip_mutable(name):
                    self.assertEqual(after[name], original, name)
            with zipfile.ZipFile(args.out / "reproduce.zip") as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.comment, b"Original archive comment")
                self.assertEqual(archive.read("kvc-fair/batch_report.py"), b"# ORIGINAL generator source\n")
                self.assertEqual(archive.read("kvc-fair/extras/original.bin"), b"unlisted additional original member\x00\xff")

    def test_html_crlf_nonascii_changes_only_exact_hash_href(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = (args.out / "index.html").read_bytes()
            args.apply = True; result = subject.refresh(args)
            expected = report.version_zip_href(before.decode(), result["zip_sha256"]).encode()
            self.assertEqual((args.out / "index.html").read_bytes(), expected)
            self.assertIn(b"\r\n", expected)
            self.assertEqual((args.out / "index.html.gz").read_bytes(), gzip.compress(expected, mtime=0))

    def test_delivery_tools_content_addressed_and_not_original_execution_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory); args = self.fixture(parent)
            tool = parent / "extra-tool.py"; tool.write_bytes(b"# extra delivery-only tool\n")
            args.delivery_tool = [tool]; args.apply = True
            subject.refresh(args)
            manifest = json.loads((args.out / "delivery-tools/manifest.json").read_bytes())
            self.assertEqual(len(manifest["tools"]), 3)
            self.assertIn("do not assert that any tool ran", manifest["scope"])
            for row in manifest["tools"]:
                self.assertFalse(row["original_generator_or_measured_runtime"])
                content = (args.out / row["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(content).hexdigest(), row["sha256"])
                self.assertTrue(Path(row["path"]).name.startswith(row["sha256"] + "-"))

    def test_no_log_input_preserves_original_operation_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = (args.out / "operations.jsonl").read_bytes()
            args.apply = True; subject.refresh(args)
            self.assertEqual((args.out / "operations.jsonl").read_bytes(), before)
            with zipfile.ZipFile(args.out / "reproduce.zip") as archive:
                self.assertEqual(archive.read("kvc-fair/operations.jsonl"), before)

    def test_explicit_external_log_snapshot_is_not_source_ledger_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory); args = self.fixture(parent)
            log = parent / "operations.jsonl"
            content = b'{"step":"fixture-only","start_wall_ns":1,"end_wall_ns":2,"returncode":0}\n'
            log.write_bytes(content); args.operation_log = [log]; args.apply = True
            subject.refresh(args)
            self.assertEqual(log.read_bytes(), content)
            rows = [json.loads(line) for line in (args.out / "operations.jsonl").read_text().splitlines()]
            self.assertEqual(rows[0]["record_type"], "delivery_ledger_snapshot")
            self.assertEqual(rows[1]["step"], "fixture-only")

    def test_staged_core_mutation_even_with_valid_rebuilt_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = snapshot(args.out)
            original = subject.finalize_artifacts

            def tamper(stage, *positional, **kwargs):
                self.assertNotEqual((stage / "data.json").stat().st_ino, (args.out / "data.json").stat().st_ino)
                (stage / "data.json").write_bytes(b'{"tampered":true}\n')
                return original(stage, *positional, **kwargs)

            args.apply = True
            with patch.object(subject, "finalize_artifacts", side_effect=tamper), self.assertRaises(RuntimeError):
                subject.refresh(args)
            self.assertEqual(snapshot(args.out), before)

    def test_staged_html_body_change_rejected_even_with_valid_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = snapshot(args.out)
            original = subject.finalize_artifacts

            def tamper(stage, *positional, **kwargs):
                path = stage / "index.html"
                path.write_bytes(path.read_bytes().replace(b"<!doctype html>", b"<!doctype html><p>unreviewed</p>"))
                return original(stage, *positional, **kwargs)

            args.apply = True
            with patch.object(subject, "finalize_artifacts", side_effect=tamper), self.assertRaises(RuntimeError):
                subject.refresh(args)
            self.assertEqual(snapshot(args.out), before)

    def test_mid_commit_failure_rolls_back_files_and_new_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); before = snapshot(args.out); args.apply = True
            original = os.replace; calls = []

            def fail_second_commit(source, target):
                if args.out in Path(target).parents:
                    calls.append(str(target))
                    if len(calls) == 2:
                        raise OSError("fixture commit failure")
                return original(source, target)

            with patch.object(subject.os, "replace", side_effect=fail_second_commit), self.assertRaises(OSError):
                subject.refresh(args)
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(snapshot(args.out), before)
            self.assertFalse((args.out / "delivery-tools").exists())

    def test_original_manifest_tamper_and_untracked_file_rejected_without_mutation(self):
        for mode in ["checksum", "untracked", "duplicate"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                args = self.fixture(Path(directory)); args.apply = True
                if mode == "checksum":
                    (args.out / "data.json").write_bytes(b"tampered raw data")
                elif mode == "untracked":
                    (args.out / "unknown.bin").write_bytes(b"untracked")
                else:
                    path = args.out / "SHA256SUMS"; path.write_bytes(path.read_bytes() + path.read_bytes().splitlines(keepends=True)[0])
                before = snapshot(args.out)
                with self.assertRaises(RuntimeError):
                    subject.refresh(args)
                self.assertEqual(snapshot(args.out), before)

    def test_unsafe_zip_path_duplicate_or_symlink_rejected(self):
        for mode in ["traversal", "duplicate", "symlink"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                args = self.fixture(Path(directory)); args.apply = True
                with warnings.catch_warnings(), zipfile.ZipFile(args.out / "reproduce.zip", "a") as archive:
                    warnings.simplefilter("ignore", UserWarning)
                    name = "kvc-fair/../escape" if mode == "traversal" else "kvc-fair/batch_report.py" if mode == "duplicate" else "kvc-fair/link"
                    info = zipfile.ZipInfo(name)
                    if mode == "symlink":
                        info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, b"fixture")
                report.finalize_artifacts(args.out)
                before = snapshot(args.out)
                with self.assertRaises(ValueError):
                    subject.refresh(args)
                self.assertEqual(snapshot(args.out), before)

    def test_bad_original_mode_and_mismatched_html_gzip_rejected(self):
        for mode in ["mode", "gzip"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                args = self.fixture(Path(directory)); args.apply = True
                if mode == "mode":
                    (args.out / "data.json").chmod(0o640)
                else:
                    (args.out / "index.html.gz").write_bytes(gzip.compress(b"different HTML", mtime=0))
                    report.finalize_artifacts(args.out, gzip_paths=[])
                before = snapshot(args.out)
                with self.assertRaises(ValueError):
                    subject.refresh(args)
                self.assertEqual(snapshot(args.out), before)

    def test_repeat_refresh_keeps_original_generator_and_previous_tool_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory)); args.apply = True; subject.refresh(args)
            before = json.loads((args.out / "delivery-tools/manifest.json").read_bytes())
            (args.rfc / "build.md").write_bytes(b"# Second docs-only update\n")
            subject.refresh(args)
            after = json.loads((args.out / "delivery-tools/manifest.json").read_bytes())
            self.assertEqual({row["path"] for row in before["tools"]}, {row["path"] for row in after["tools"]})
            with zipfile.ZipFile(args.out / "reproduce.zip") as archive:
                self.assertEqual(archive.read("kvc-fair/batch_report.py"), b"# ORIGINAL generator source\n")


if __name__ == "__main__":
    unittest.main()
