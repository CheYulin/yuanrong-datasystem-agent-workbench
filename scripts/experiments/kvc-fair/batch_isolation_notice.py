#!/usr/bin/env python3
"""Explicit, reversible isolation erratum for the two historical KVC reports."""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from refresh_delivery import digest, inventory, verify_manifest


ALLOWED_REPORTS = {
    "/home/t14s/workspace/scratch/2026-10-20-kvc-compare/report": "kvc-fair-20261020",
    "/home/t14s/workspace/scratch/2026-10-20-kvc-tcp/report": "kvc-tcp-20261020",
    "/var/www/html/perf/kvc-fair-20261020": "kvc-fair-20261020",
    "/var/www/html/perf/kvc-tcp-20261020": "kvc-tcp-20261020",
}
MARKER = "<!-- kvc-isolation-erratum:v1 -->"
STATEMENT = ("此前两个 DataSystem worker 沿用同一默认 RocksDB 路径，是实验 harness 的目录隔离遗漏。"
             "因此旧 DataSystem 性能对比不再作为隔离公平基线；原始测量保留供历史审计。")
ZIP_SCOPE = "现有 reproduce.zip 保持原始历史字节、未修订，不能作为 DataSystem 隔离公平基线；适用限制以本页更正及 isolation-erratum.md/json 为准。"


def checksum(content):
    return hashlib.sha256(content).hexdigest()


def verify_notice_manifest(out, publication_receipt_sha256=None):
    if publication_receipt_sha256 is None:
        return verify_manifest(out)
    if not re.fullmatch(r"[0-9a-f]{64}", publication_receipt_sha256):
        raise ValueError("An explicit lowercase publication.json SHA256 is required")
    observed = inventory(out)
    if "SHA256SUMS" not in observed:
        raise RuntimeError("An existing regular SHA256SUMS is required")
    expected = {}
    for line in (out / "SHA256SUMS").read_text().splitlines():
        if not line.strip():
            continue
        value, separator, name = line.partition("  ")
        relative = Path(name)
        if (not separator or not re.fullmatch(r"[0-9a-f]{64}", value)
                or relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name
                or name in expected or name == "SHA256SUMS"):
            raise RuntimeError("Invalid or duplicate SHA256SUMS entry: " + line)
        expected[name] = value
    if (set(observed) - set(expected) - {"SHA256SUMS"} != {"publication.json"}
            or set(expected) - set(observed)):
        raise RuntimeError("Only the explicitly reviewed extra publication.json is allowed")
    for name, value in expected.items():
        if observed[name]["sha256"] != value:
            raise RuntimeError("Original artifact checksum mismatch: " + name)
    if observed["publication.json"]["sha256"] != publication_receipt_sha256:
        raise RuntimeError("publication.json SHA256 mismatch")
    return observed


def validate_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "yche.me" or parsed.query or parsed.fragment
            or not re.fullmatch(r"/perf/[A-Za-z0-9_-]+/", parsed.path)
            or parsed.path in {"/perf/kvc-fair-20261020/", "/perf/kvc-tcp-20261020/"}):
        raise ValueError("Use the explicit new report HTTPS URL within yche.me/perf/<name>/")
    return value


def insert_notice(original, new_report_url):
    source = original.decode("utf-8")
    matches = list(re.finditer(r"<main\b[^>]*>", source, re.IGNORECASE))
    if len(matches) != 1 or MARKER in source:
        raise ValueError("Expected exactly one main and no existing isolation erratum")
    classes = re.search(r"\bclass\s*=\s*([\"'])(.*?)\1", matches[0].group(), re.IGNORECASE | re.DOTALL)
    if not classes or "content-area" not in classes[2].split():
        raise ValueError("The unique main must be the historical content-area container")
    notice = (MARKER + '\n<aside id="isolation-erratum" role="alert" '
              'style="box-sizing:border-box;max-width:1320px;margin:20px auto;padding:20px;'
              'border:3px solid #a33b15;background:#fff1e8;color:#54220c;line-height:1.8">'
              '<strong style="font-size:21px">适用限制更正：旧 DataSystem 结果不作隔离公平基线</strong>'
              '<p>' + html.escape(STATEMENT) + '</p><p>' + html.escape(ZIP_SCOPE) + '</p>'
              '<p><a href="isolation-erratum.md">完整更正说明</a> · '
              '<a href="isolation-erratum.json">字节与范围审计</a> · '
              '<a href="' + html.escape(new_report_url, quote=True) + '">独立批量实验报告</a></p></aside>\n')
    at = matches[0].end()
    result = (source[:at] + notice + source[at:]).encode("utf-8")
    if result.replace(notice.encode("utf-8"), b"", 1) != original:
        raise RuntimeError("HTML changed beyond the single appended notice")
    return result


def refresh(args):
    original_path = Path(args.report_dir)
    out = original_path.resolve(strict=True)
    if not original_path.is_absolute() or original_path != out or str(out) not in ALLOWED_REPORTS:
        raise ValueError("Only the two exact historical report directories are allowed")
    if not re.fullmatch(r"[0-9a-f]{64}", args.expected_manifest_sha256):
        raise ValueError("An explicit lowercase SHA256SUMS CAS digest is required")
    new_url = validate_url(args.new_report_url)
    lock = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return refresh_locked(args, out, new_url)
    finally:
        os.close(lock)


def refresh_locked(args, out, new_url):
    if digest(out / "SHA256SUMS") != args.expected_manifest_sha256:
        raise ValueError("SHA256SUMS CAS mismatch; review the current exact report first")
    receipt_sha256 = getattr(args, "publication_receipt_sha256", None)
    before = verify_notice_manifest(out, receipt_sha256)
    if not {"index.html", "index.html.gz", "reproduce.zip"} <= before.keys():
        raise ValueError("Historical HTML, its gzip and original reproduction ZIP are required")
    if any(record["mode"] != 0o644 for record in before.values()):
        raise ValueError("Every original public file must already have mode 0644")
    original_html = (out / "index.html").read_bytes()
    original_gzip = (out / "index.html.gz").read_bytes()
    original_manifest = (out / "SHA256SUMS").read_bytes()
    if gzip.decompress(original_gzip) != original_html:
        raise ValueError("Historical HTML gzip does not match its original source")
    changed_html = insert_notice(original_html, new_url)
    originals = {"index.html": original_html, "index.html.gz": original_gzip, "SHA256SUMS": original_manifest}
    history = {"history/isolation-erratum/" + checksum(content) + "-" + name: content for name, content in originals.items()}
    additions = set(history) | {"isolation-erratum.md", "isolation-erratum.md.gz", "isolation-erratum.json", "isolation-erratum.json.gz"}
    if any((out / name).exists() or (out / name).is_symlink() for name in additions):
        raise ValueError("Erratum/history destinations must be new; never overwrite prior notice evidence")
    frozen = {name: record for name, record in before.items() if name not in {"index.html", "index.html.gz", "SHA256SUMS"}}
    issued = datetime.datetime.now(datetime.timezone.utc).isoformat()
    evidence = {"schema_version": 1, "kind": "historical_kvc_worker_storage_isolation_erratum", "issued_utc": issued,
                "report_id": ALLOWED_REPORTS[str(out)], "statement": STATEMENT, "new_report_url": new_url,
                "original_sha256sums_sha256": checksum(original_manifest),
                "original_files": {name: {"sha256": checksum(content), "bytes": len(content)} for name, content in originals.items()},
                "history": [{"path": name, "sha256": checksum(content), "bytes": len(content)} for name, content in sorted(history.items())],
                "original_zip": {"path": "reproduce.zip", "sha256": before["reproduce.zip"]["sha256"], "rewritten": False, "scope": ZIP_SCOPE},
                "immutable_inventory": frozen,
                "allowed_changes": ["index.html: single visible erratum inserted immediately inside the unique main.content-area, before hero", "index.html.gz: mtime=0 gzip of that HTML", "new isolation-erratum.md/json and gzip companions", "new content-addressed copies of original HTML/gzip/SHA256SUMS", "SHA256SUMS"],
                "measurement_changed": False, "original_data_plots_raw_scripts_binaries_zip_unchanged": True,
                "publication_receipt": ({"path": "publication.json", **before["publication.json"],
                                         "bytes": (out / "publication.json").stat().st_size,
                                         "added_to_manifest": True} if receipt_sha256 is not None else None),
                "helper_sha256": digest(Path(__file__)),
                "scope": "Corrects applicability only; no recomputation, source/product change, rerun, publication or modification of old raw data, plots or ZIP. The linked independent report has its own evidence and status."}
    history_lines = ["- [" + Path(name).name + "](" + name + "): `" + checksum(content) + "`" for name, content in sorted(history.items())]
    markdown = ("# 旧 KVC 对比适用限制更正\n\n" + STATEMENT + "\n\n" + ZIP_SCOPE + "\n\n"
                "原数据 JSON、CSV、图、原始证据、脚本、二进制及 reproduce.zip 均保持原字节；本次仅更正适用范围，不重算或重跑。"
                "独立补充报告的执行状态及结果以其自身证据为准。\n\n"
                "[独立批量实验报告](" + new_url + ") · [机器可读范围与原始哈希](isolation-erratum.json)\n\n"
                "原 SHA256SUMS：`" + checksum(original_manifest) + "`；原 ZIP：`" + before["reproduce.zip"]["sha256"] + "`。\n\n"
                "原始页面及清单的内容地址副本：\n\n" + "\n".join(history_lines) + "\n")
    replacements = {**history, "index.html": changed_html, "index.html.gz": gzip.compress(changed_html, mtime=0),
                    "isolation-erratum.md": markdown.encode("utf-8"),
                    "isolation-erratum.json": (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}
    for name in ["isolation-erratum.md", "isolation-erratum.json"]:
        replacements[name + ".gz"] = gzip.compress(replacements[name], mtime=0)
    proposed = {name: record for name, record in before.items() if name != "SHA256SUMS"}
    proposed.update({name: {"sha256": checksum(content), "mode": 0o644} for name, content in replacements.items()})
    replacements["SHA256SUMS"] = "".join(proposed[name]["sha256"] + "  " + name + "\n" for name in sorted(proposed)).encode()
    report = {"apply": bool(args.apply), "report_dir": str(out), "report_id": ALLOWED_REPORTS[str(out)],
              "original_manifest_sha256": checksum(original_manifest), "proposed_manifest_sha256": checksum(replacements["SHA256SUMS"]),
              "original_zip_sha256": before["reproduce.zip"]["sha256"], "zip_rewritten": False,
              "publication_receipt": evidence["publication_receipt"],
              "immutable_file_count": len(frozen), "updates": sorted(replacements),
              "new_report_url": new_url, "scope": evidence["scope"]}
    with tempfile.TemporaryDirectory(prefix="." + out.name + ".isolation-erratum-", dir=out.parent) as temporary:
        work = Path(temporary); stage, backup = work / "stage", work / "backup"
        stage.mkdir(); backup.mkdir()
        for name, content in replacements.items():
            target = stage / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content); target.chmod(0o644)
            if name in before:
                saved = backup / name; saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(out / name, saved)
        staged = {path.relative_to(stage).as_posix(): path for path in stage.rglob("*") if path.is_file()}
        if set(staged) != set(replacements) or any(path.read_bytes() != replacements[name] or path.stat().st_mode & 0o777 != 0o644 for name, path in staged.items()):
            raise RuntimeError("Staged notice transaction differs from its exact controlled bytes")
        if verify_notice_manifest(out, receipt_sha256) != before or digest(out / "SHA256SUMS") != args.expected_manifest_sha256:
            raise RuntimeError("Historical report changed during staging; no notice applied")
        if not args.apply:
            report.update(original_output_unchanged=True, immutable_files_unchanged=True)
            return report
        committed, created_dirs = [], set()
        try:
            for name in [*sorted(set(replacements) - {"SHA256SUMS"}), "SHA256SUMS"]:
                target = out / name
                created_dirs.update(parent for parent in target.parents if parent != out and out in parent.parents and not parent.exists())
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / name, target); committed.append(name)
            after = verify_manifest(out)
            if (set(after) != set(proposed) | {"SHA256SUMS"}
                    or any(after[name] != record for name, record in proposed.items())
                    or any(after[name] != record for name, record in frozen.items())):
                raise RuntimeError("Post-notice immutable inventory or exact transaction validation failed")
        except BaseException:
            for name in reversed(committed):
                if (backup / name).exists():
                    os.replace(backup / name, out / name)
                else:
                    (out / name).unlink()
            for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
    report.update(immutable_files_unchanged=True, final_manifest_sha256=digest(out / "SHA256SUMS"))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--publication-receipt-sha256", help="Accept only the extra publication.json with this exact SHA256; preserve it and include it in the new manifest")
    parser.add_argument("--new-report-url", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(refresh(args), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "measurements_changed": False}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
