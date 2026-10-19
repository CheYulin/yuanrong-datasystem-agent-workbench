#!/usr/bin/env python3
"""Review or commit this published report without staging unrelated site changes.

Run on the site host. The default is a dry run with an isolated index and object
directory; --apply creates one commit without pushing or writing worktree files.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


HOMEPAGE = "index.html"
METADATA = "assets/js/document-index-20261011.json"
REMOTE = re.compile(r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)CheYulin/CheYulin\.github\.io(?:\.git)?/?", re.I)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key: " + key)
        value[key] = item
    return value


def home_array(content):
    text = content.decode("utf-8")
    matches = list(re.finditer(r"var P\s*=\s*", text))
    if len(matches) != 1:
        raise ValueError("Expected exactly one homepage P array")
    begin = matches[0].end()
    value, _ = json.JSONDecoder(object_pairs_hook=unique_object).raw_decode(text[begin:])
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("Homepage P must be an array of objects")
    return text, begin, value


def registered_blobs(head_home, head_metadata, published_home, published_metadata, key):
    _, _, published = home_array(published_home)
    entries = [row for row in published if row.get("p") == key]
    metadata = json.loads(published_metadata, object_pairs_hook=unique_object)
    if len(entries) != 1 or key not in metadata:
        raise ValueError("Published report needs exactly one homepage entry and its metadata")
    entry, meta = entries[0], metadata[key]
    text, begin, pages = home_array(head_home)
    existing = [row for row in pages if row.get("p") == key]
    if existing and existing != [entry]:
        raise ValueError("HEAD already has a different or duplicate report entry")
    if not existing:
        insertion = "\n" + json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + ("," if pages else "")
        text = text[:begin + 1] + insertion + text[begin + 1:]
    base = json.loads(head_metadata, object_pairs_hook=unique_object)
    if not isinstance(base, dict) or not isinstance(meta, dict):
        raise ValueError("Document metadata must be an object")
    metadata_text = head_metadata.decode("utf-8")
    if key in base:
        if base[key] != meta:
            raise ValueError("HEAD already has different report metadata")
    else:
        begin = metadata_text.index("{") + 1
        insertion = "\n" + json.dumps(key, ensure_ascii=False) + ":" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + ("," if base else "")
        metadata_text = metadata_text[:begin] + insertion + metadata_text[begin:]
    return {HOMEPAGE: text.encode("utf-8"), METADATA: metadata_text.encode("utf-8")}


def excluded(relative):
    parts = relative.parts
    name = relative.name.lower()
    return (name == "raw-evidence.tar.gz" or name.startswith("publication")
            or name.startswith(("browser-qa", "qa-source", "ui-issues"))
            or any(p.startswith(".") or p.lower() in {"evidence", "screenshots", "browser-qa", "qa"} for p in parts)
            or (relative.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                and (re.match(r"\d+-", name) is not None or any(s in name for s in ["browser", "screenshot", "publication", "qa"]))))


class Publication:
    def __init__(self, args):
        self.args = args
        self.site = args.site.resolve()
        report = Path(args.report)
        if report.is_absolute() or ".." in report.parts or not report.parts:
            raise ValueError("Report must be a relative site path")
        self.report = report
        self.directory = self.site / report
        if self.directory.resolve() != self.directory or self.site not in self.directory.parents:
            raise ValueError("Report must remain inside the site without symlink aliases")
        for key in ["GIT_DIR", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"]:
            if key in os.environ:
                raise ValueError("Run without inherited " + key)
        self.environment = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        if Path(self.git("rev-parse", "--show-toplevel").decode().strip()).resolve() != self.site:
            raise ValueError("--site must be the Git worktree root")
        self.branch = self.git("symbolic-ref", "--quiet", "HEAD").decode().strip()
        if self.branch != "refs/heads/master":
            raise ValueError("Only the site master branch is supported")
        for push in [False, True]:
            flags = ["--push"] if push else []
            urls = self.git("remote", "get-url", "--all", *flags, "origin").decode().splitlines()
            if not urls or not all(REMOTE.fullmatch(url) for url in urls):
                raise ValueError("origin must be CheYulin/CheYulin.github.io for fetch and push")
        self.head = self.git("rev-parse", "HEAD").decode().strip()
        self.index = self.git_path("index")
        self.head_file = self.git_path("HEAD")
        if self.index.is_symlink():
            raise ValueError("Refusing a symlinked real index")
        if Path(str(self.index) + ".lock").exists() or Path(str(self.head_file) + ".lock").exists():
            raise RuntimeError("Existing Git index/HEAD lock; another operation may be active")
        self.assert_no_staged()
        self.index_snapshot = self.index_state()
        self.dirty_tracked = {self.site / os.fsdecode(name) for name in
                              self.git("ls-files", "--modified", "--deleted", "-z").split(b"\0") if name}
        self.worktree_before = self.worktree_snapshot()

    def git(self, *command, env=None, input=None):
        result = subprocess.run(["git", "-c", "core.splitIndex=false", *command], cwd=self.site,
                                env=env or self.environment, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError("git " + " ".join(command[:3]) + ": " + result.stderr.decode(errors="replace").strip())
        return result.stdout

    def git_path(self, name):
        path = Path(self.git("rev-parse", "--git-path", name).decode().strip())
        return path if path.is_absolute() else self.site / path

    def assert_no_staged(self):
        if self.git("diff", "--cached", "--name-only", "-z", "HEAD", "--"):
            raise RuntimeError("Refusing pre-staged changes; the real index must match HEAD")

    def index_state(self):
        before = self.index.stat()
        content = self.index.read_bytes()
        after = self.index.stat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after):
            raise RuntimeError("Real index changed while it was read")
        return identity(after), content

    def worktree_snapshot(self):
        paths = set(self.dirty_tracked)
        paths.update(self.directory.rglob("*"))
        paths.update(self.site / name for name in [HOMEPAGE, METADATA, HOMEPAGE + ".gz", METADATA + ".gz"])
        result = {}
        for path in sorted(paths):
            relative = str(path.relative_to(self.site))
            if path.is_symlink():
                result[relative] = ["symlink", os.readlink(path)]
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(8 << 20), b""):
                        digest.update(chunk)
                result[relative] = ["file", stat.S_IMODE(path.stat().st_mode), digest.hexdigest()]
            elif not path.exists():
                result[relative] = ["missing"]
        return result

    def assert_unchanged(self, locked=False):
        if Path(str(self.head_file) + ".lock").exists() or (not locked and Path(str(self.index) + ".lock").exists()):
            raise RuntimeError("Concurrent Git index/HEAD lock")
        if self.git("symbolic-ref", "--quiet", "HEAD").decode().strip() != self.branch or self.git("rev-parse", "HEAD").decode().strip() != self.head:
            raise RuntimeError("Concurrent HEAD change; original index and worktree were not replaced")
        if self.index_state() != self.index_snapshot:
            raise RuntimeError("Concurrent real index change; original index was not replaced")
        self.assert_no_staged()
        if self.worktree_snapshot() != self.worktree_before:
            raise RuntimeError("Concurrent worktree change; refusing to commit the snapshot")

    def run(self):
        for name in ["index.html", "SHA256SUMS"]:
            if not (self.directory / name).is_file():
                raise ValueError("Missing published report file: " + name)
        head_sources = {name: self.git("show", self.head + ":" + name) for name in [HOMEPAGE, METADATA]}
        blobs = registered_blobs(head_sources[HOMEPAGE], head_sources[METADATA],
                                 (self.site / HOMEPAGE).read_bytes(), (self.site / METADATA).read_bytes(), str(self.report / "index.html"))
        for name in list(blobs):
            if self.git("ls-tree", "--name-only", self.head, "--", name + ".gz"):
                blobs[name + ".gz"] = (self.git("show", self.head + ":" + name + ".gz") if blobs[name] == head_sources[name]
                                         else gzip.compress(blobs[name], mtime=0))
        registration_modes = {name: self.git("ls-tree", self.head, "--", name).split(b" ", 1)[0].decode()
                              for name in blobs}
        removed, skipped = [], []
        for path in sorted(self.directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("Refusing report symlink: " + str(path))
            if not path.is_file():
                continue
            if excluded(path.relative_to(self.directory)):
                skipped.append(str(path.relative_to(self.site)))
                continue
            if path.stat().st_size > self.args.max_blob_mib * (1 << 20):
                raise ValueError("Oversized report blob: " + str(path.relative_to(self.site)))
            blobs[str(path.relative_to(self.site))] = path.read_bytes()
        for raw in self.git("ls-tree", "-r", "--name-only", "-z", self.head, "--", str(self.report) + "/").split(b"\0"):
            if raw:
                name = os.fsdecode(raw)
                if name not in blobs and not excluded(Path(name).relative_to(self.report)):
                    removed.append(name)
        if any(len(content) > self.args.max_blob_mib * (1 << 20) for content in blobs.values()):
            raise ValueError("Oversized registration or report blob")
        with tempfile.TemporaryDirectory(prefix="kvc-publication-") as temporary:
            temporary = Path(temporary)
            objects = temporary / "objects"; objects.mkdir()
            env = dict(self.environment, GIT_INDEX_FILE=str(temporary / "index"), GIT_OBJECT_DIRECTORY=str(objects),
                       GIT_ALTERNATE_OBJECT_DIRECTORIES=str(self.git_path("objects").resolve()))
            (temporary / "index").write_bytes(self.index_snapshot[1])
            self.git("read-tree", self.head, env=env)
            records = []
            for name, content in blobs.items():
                oid = self.git("hash-object", "-w", "--stdin", input=content, env=env).decode().strip()
                mode = registration_modes.get(name) or ("100755" if (self.site / name).stat().st_mode & 0o111 else "100644")
                records.append((mode + " " + oid + "\t" + name).encode() + b"\0")
            for name in removed:
                records.append(("0 " + "0" * len(self.head) + "\t" + name).encode() + b"\0")
            self.git("update-index", "-z", "--index-info", input=b"".join(records), env=env)
            changed = self.git("diff", "--cached", "--name-only", "-z", self.head, "--", env=env).split(b"\0")
            allowed = set(blobs) | set(removed)
            if any(os.fsdecode(name) not in allowed for name in changed if name):
                raise AssertionError("Temporary index contains unrelated paths")
            self.assert_unchanged()
            summary = {"mode": "apply" if self.args.apply else "dry-run", "head": self.head,
                       "paths": [os.fsdecode(name) for name in changed if name], "excluded": skipped,
                       "guarded_worktree_files": len(self.worktree_before)}
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            print(self.git("diff", "--cached", "--stat", self.head, "--", env=env).decode(), flush=True)
            diff = self.git("diff", "--cached", "--no-ext-diff", "--binary", self.head, "--", env=env)
            if self.args.diff_output:
                destination = self.args.diff_output.resolve()
                git_directories = [Path(self.git("rev-parse", flag).decode().strip()) for flag in ["--git-dir", "--git-common-dir"]]
                protected = [self.site] + [(p if p.is_absolute() else self.site / p).resolve() for p in git_directories]
                if any(destination == root or root in destination.parents for root in protected):
                    raise ValueError("Diff output must be outside the site worktree and Git administrative directories")
                destination.write_bytes(diff)
                print("Exact diff: " + str(destination), flush=True)
            else:
                print(diff.decode("utf-8"), flush=True)
            if not any(changed):
                return dict(summary, result="no-op")
            if not self.args.apply:
                self.assert_unchanged()
                return dict(summary, result="reviewed; no refs, index or worktree changed")
            tree = self.git("write-tree", env=env).decode().strip()
            commit = self.git("commit-tree", tree, "-p", self.head, input=(self.args.message + "\n").encode(), env=env).decode().strip()
            self.apply(commit, (temporary / "index").read_bytes(), env)
            return dict(summary, result="committed", commit=commit)

    def apply(self, commit, new_index, env):
        locks = []
        ref_updated = False
        try:
            for path in [Path(str(self.index) + ".lock")]:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                locks.append((path, descriptor, os.fstat(descriptor).st_ino))
            self.assert_unchanged(locked=True)
            pack = self.git("pack-objects", "--stdout", "--revs", input=(commit + "\n^" + self.head + "\n").encode(), env=env)
            self.git("index-pack", "--stdin", input=pack)
            self.assert_unchanged(locked=True)
            with os.fdopen(os.dup(locks[0][1]), "wb") as stream:
                stream.write(new_index)
                stream.flush()
                os.fchmod(stream.fileno(), stat.S_IMODE(self.index.stat().st_mode))
                os.fsync(stream.fileno())
            self.git("update-ref", self.branch, commit, self.head)
            ref_updated = True
            if (self.git("symbolic-ref", "--quiet", "HEAD").decode().strip() != self.branch
                    or self.git("rev-parse", "HEAD").decode().strip() != commit
                    or self.index_state() != self.index_snapshot):
                raise RuntimeError("Concurrent HEAD/index change before index installation")
            if self.worktree_snapshot() != self.worktree_before:
                raise RuntimeError("Concurrent worktree change before index installation")
            os.replace(locks[0][0], self.index)
            ref_updated = False
        except BaseException:
            if ref_updated:
                self.git("update-ref", self.branch, self.head, commit)
            raise
        finally:
            for path, descriptor, inode in reversed(locks):
                os.close(descriptor)
                if path.exists() and path.stat().st_ino == inode:
                    path.unlink()
        if self.worktree_snapshot() != self.worktree_before:
            raise RuntimeError("Commit created, but a concurrent external worktree change was detected: " + commit)
        if self.git("symbolic-ref", "--quiet", "HEAD").decode().strip() != self.branch or self.git("rev-parse", "HEAD").decode().strip() != commit:
            raise RuntimeError("Commit created, but HEAD changed concurrently: " + commit)
        if self.git("diff", "--cached", "--name-only", "-z", commit, "--"):
            raise RuntimeError("Commit created, but real index changed concurrently: " + commit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("/var/www/html"))
    parser.add_argument("--report", default="perf/kvc-fair-20261020")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--message", default="docs: publish KVC fair comparison report")
    parser.add_argument("--max-blob-mib", type=int, default=20)
    parser.add_argument("--diff-output", type=Path)
    args = parser.parse_args()
    if not 0 < args.max_blob_mib <= 50:
        parser.error("--max-blob-mib must be in 1..50")
    print(json.dumps(Publication(args).run(), ensure_ascii=False))


if __name__ == "__main__":
    main()
