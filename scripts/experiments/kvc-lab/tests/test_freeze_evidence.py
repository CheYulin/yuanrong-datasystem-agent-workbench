"""Evidence transfer is tested with adversarial local tar streams, without SSH."""
import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("freeze_evidence", Path(__file__).resolve().parents[1] / "validation/freeze_evidence.py")
FREEZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FREEZE)


def identity(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def archive(path, entries):
    with tarfile.open(path, "w") as output:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                info.type, info.linkname = kind, "outside.json"
                output.addfile(info)
            elif kind == "oversized":
                info.size = FREEZE.MAX_FILE + 1
                output.addfile(info)
            else:
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))


def evidence():
    root = "v-v0/001-mooncake"
    raw = root + "/r0-s1024-b1-get/raw.json"
    names = (FREEZE.BASE - {"complete.json"}) | {raw, root + "/qualification.json", root + "/resource-limits.json",
            root + "/resources.jsonl.gz", root + "/master-config.json", root + "/adapter-8192.json", "_evidence/full.log"}
    records = [{"name": name, "source": name.removesuffix(".gz"), **identity(b"")} for name in sorted(names)]
    job_id = "a" * 32
    return {"job": {"id": job_id, "status": "succeeded", "provenance": {"source_sha256": FREEZE.SOURCE},
            "completion": {"job_id": job_id, "status": "succeeded", "results": [{"raw_artifact": raw}], "artifacts": records}}}


class FreezeEvidenceTests(unittest.TestCase):
    def test_only_terminal_pinned_run_with_minimal_bound_members_is_selected(self):
        value = evidence()
        job, selected = FREEZE.selection(value)
        self.assertEqual(job["id"], "a" * 32)
        self.assertNotIn("_evidence/full.log", selected)
        self.assertIn("v-v0/001-mooncake/resources.jsonl.gz", selected)
        self.assertIn("v-v0/001-mooncake/adapter-8192.json", selected)
        self.assertTrue(FREEZE.BASE <= set(selected))

    def test_running_wrong_source_or_unbound_raw_is_rejected(self):
        for mutation in [lambda job: job.update(status="running"),
                         lambda job: job["provenance"].update(source_sha256="0" * 64),
                         lambda job: job["completion"]["results"][0].update(raw_artifact="unlisted/raw.json")]:
            value = evidence()
            mutation(value["job"])
            with self.assertRaises(ValueError):
                FREEZE.selection(value)

    def test_manifest_traversal_duplicate_and_missing_resource_are_rejected(self):
        for mutation in [lambda records: records.append({"name": "../outside.json"}),
                         lambda records: records.append(copy.deepcopy(records[0])),
                         lambda records: records.__setitem__(slice(None), [row for row in records if not row["name"].endswith("resources.jsonl.gz")])]:
            value = evidence()
            mutation(value["job"]["completion"]["artifacts"])
            with self.assertRaises(ValueError):
                FREEZE.selection(value)

    def test_exact_regular_member_set_and_hashes_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "out"
            out.mkdir()
            path = root / "in.tar"
            archive(path, [("case/raw.json", b"{}", None), ("complete.json", b"[]", None)])
            values = FREEZE.extract(path, out, {"case/raw.json": identity(b"{}"), "complete.json": None})
            self.assertEqual({row["name"] for row in values}, {"case/raw.json", "complete.json"})
            self.assertEqual((out / "case/raw.json").read_bytes(), b"{}")

    def test_tar_links_absolute_and_parent_members_never_escape(self):
        for name, kind in [("../outside.json", None), ("/outside.json", None), ("raw.json", tarfile.SYMTYPE), ("raw.json", tarfile.LNKTYPE), ("raw.json", "oversized")]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                out = root / "out"
                out.mkdir()
                path = root / "in.tar"
                archive(path, [(name, b"{}", kind)])
                with self.assertRaises(ValueError):
                    FREEZE.extract(path, out, {"raw.json": identity(b"{}")})
                self.assertFalse((root / "outside.json").exists())

    def test_added_missing_duplicate_and_rewritten_members_fail(self):
        for entries, selected in [
                ([("raw.json", b"{}", None), ("extra.json", b"{}", None)], {"raw.json": identity(b"{}")}),
                ([("raw.json", b"{}", None)], {"raw.json": identity(b"{}"), "missing.json": identity(b"{}")}),
                ([("raw.json", b"{}", None)] * 2, {"raw.json": identity(b"{}")}),
                ([("raw.json", b"[]", None)], {"raw.json": identity(b"{}")})]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                out = root / "out"
                out.mkdir()
                path = root / "in.tar"
                archive(path, entries)
                with self.assertRaises(ValueError):
                    FREEZE.extract(path, out, selected)


if __name__ == "__main__":
    unittest.main()
