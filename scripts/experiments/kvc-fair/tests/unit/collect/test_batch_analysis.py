"""Small offline evidence-tamper fixtures; no product processes or performance runs."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import contextlib
import hashlib
import io
import os
from pathlib import Path
from tests.paths import TOOL_ROOT
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import batch_analyze as subject
import batch_report
from benchmark import bucket, percentile


def raw_result(operation="get", batch=16):
    start, end = 1_000_000_000, 21_000_000_000
    target = 262144 // 8 // batch
    success = target if operation == "put" else 1000
    index = bucket(1000)
    clients = [{"client": client, "success": success, "failures": 0,
                "first_ns": start + 1000, "last_ns": end, "target": target,
                "start_offset_us": 1.0, "latency_sum_ns": success * 1000,
                "latency_max_ns": 1000, "histogram": {str(index): success},
                "per_second": {"0": success},
                "validated_keys": ((success - 1) // 128 + 1) * batch if operation == "get" else 0,
                "errors": []} for client in range(8)]
    metadata = {"product": "DataSystem", "source_sha": "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6",
                "native_semantics": {"write_existing_key": "insert_or_update"},
                "capabilities": {"put_batch": True, "get_batch": True, "exists_batch": True}}
    raw = {"parameters": {"op": operation, "batch": batch, "size": 1024, "concurrency": 8,
                           "pool_keys": 65536, "put_keys": 262144, "duration": 20,
                           "warmup": 3, "run_id": "formal-batch-001-r0", "skip_preload": True,
                           "adapter": "/fixture/batch_datasystem.py", "config": "/fixture/configs/datasystem.json"},
           "valid": True, "wall_start_ns": 1700000000000000000,
           "wall_end_ns": 1700000030000000000, "measure_start_mono_ns": start,
           "measure_end_mono_ns": end, "measure_start_wall_ns": 1700000005000000000,
           "duration_s": 20.0, "clients": clients, "failures": 0,
           "partial_success_keys_unknown": False, "failed_keys_upper_bound": 0,
           "setup": [{"client": i, "ready": True, "pool_keys": 8192,
                      "metadata": deepcopy(metadata), "preloaded": False} for i in range(8)],
           "warmup": [{"client": i, "warm": True} for i in range(8)],
           "post_validation": [{"client": i,
                                "post_validated_keys": 3 * batch if operation == "put" else min(success * batch, 8192) if operation == "upsert" else 0,
                                "restored_verified_keys": 8192 if operation == "upsert" else 0} for i in range(8)],
           "closed": [{"client": i, "closed": True} for i in range(8)],
           "process_exit_codes": [0] * 8, "mean_us": 1.0, "max_us": 1.0}
    refresh_totals(raw)
    return raw


def refresh_totals(raw):
    histogram = Counter()
    for client in raw["clients"]:
        histogram.update({int(key): value for key, value in client["histogram"].items()})
    count = sum(client["success"] for client in raw["clients"])
    batch = raw["parameters"]["batch"]
    duration = raw["duration_s"]
    raw.update(success=count, success_keys=count * batch, histogram={str(key): value for key, value in histogram.items()},
               batch_qps=count / duration, keys_s=count * batch / duration,
               throughput_mib_s=count * batch * 1024 / duration / (1 << 20)
               if raw["parameters"]["op"] in {"put", "upsert", "get"} else None,
               short_window=duration < 10, low_sample_p99=count < 1000)
    for name, fraction in [("p50", .5), ("p95", .95), ("p99", .99)]:
        raw[name] = percentile(histogram, fraction)


def measured(raw):
    row = {"identity": f"r0-datasystem/b{raw['parameters']['batch']}-{raw['parameters']['op']}",
           "product": "datasystem", "repeat": 0, "batch": raw["parameters"]["batch"],
           "operation": raw["parameters"]["op"], "supported": True}
    return subject.measured_row(raw, row, [], [])


class ResultIntegrityTests(unittest.TestCase):
    def test_analyzer_context_uses_frozen_product_independent_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory) / "formal-batch-001"
            (suite / "r0-datasystem").mkdir(parents=True)
            plan = [{"repeat": 0, "product": "datasystem",
                     "operations": [{"batch": 16, "operation": "get", "supported": True}]}]
            (suite / "plan.json").write_text(json.dumps(plan))
            (suite / "parameters.json").write_text(json.dumps({"action": "run", "out": "/fixture/formal-batch-001"}))
            (suite / "summary.json").write_text('{"valid":false,"failed":["partial unit fixture"]}')
            (suite / "products.json").write_text(json.dumps([
                {"name": "datasystem", "adapter": "/fixture/batch_datasystem.py", "config": "/fixture/configs/datasystem.json"}]))
            (suite / "r0-datasystem/b16-get.json").write_text(json.dumps(raw_result()))
            contexts = []

            def observe(raw, row, samples, times, context):
                contexts.append(context)
                return dict(row, valid=False, status="invalid", issues=["partial unit fixture"])

            with patch.object(subject, "measured_row", side_effect=observe):
                analysis = subject.analyze(suite)
            self.assertFalse(analysis["passed"])
            self.assertEqual(contexts[0]["run_id"], "formal-batch-001-r0")

    def test_coherent_get_and_fixed_volume_put_pass(self):
        for operation in ("get", "put", "upsert", "exist_hit", "exist_miss"):
            with self.subTest(operation=operation):
                row = measured(raw_result(operation))
                self.assertTrue(row["valid"], row["issues"])

    def test_duplicate_client_identity_rejected(self):
        raw = raw_result()
        raw["clients"][-1]["client"] = 6
        self.assertFalse(measured(raw)["valid"])

    def test_nonzero_process_exit_cannot_be_hidden_by_valid_boolean(self):
        raw = raw_result()
        raw["process_exit_codes"][-1] = -9
        self.assertFalse(measured(raw)["valid"])

    def test_fatal_cannot_be_hidden_by_valid_boolean(self):
        raw = raw_result()
        raw["fatal"] = "fixture fatal"
        self.assertFalse(measured(raw)["valid"])

    def test_mismatched_client_source_identity_is_rejected(self):
        raw = raw_result()
        raw["setup"][-1]["metadata"]["source_sha"] = "0" * 40
        self.assertFalse(measured(raw)["valid"])

    def test_forged_put_target_with_coherent_totals_rejected(self):
        raw = raw_result("put")
        for client in raw["clients"]:
            client.update(success=1, target=1, latency_sum_ns=1000,
                          histogram={str(bucket(1000)): 1}, per_second={"0": 1})
        refresh_totals(raw)
        self.assertFalse(measured(raw)["valid"], "PUT target must be derived independently from the protocol")

    def test_put_postvalidation_must_cover_every_client(self):
        raw = raw_result("put")
        raw["post_validation"] = raw["post_validation"][:-1]
        self.assertFalse(measured(raw)["valid"])

    def test_upsert_restore_zero_is_not_full_restoration(self):
        raw = raw_result("upsert")
        raw["post_validation"][-1]["restored_verified_keys"] = 0
        self.assertFalse(measured(raw)["valid"])

    def test_wrong_duration_warmup_and_pool_cannot_change_protocol(self):
        for key, value in (("duration", .1), ("warmup", 0), ("pool_keys", 8), ("put_keys", 8)):
            with self.subTest(key=key):
                raw = raw_result()
                raw["parameters"][key] = value
                self.assertFalse(measured(raw)["valid"])

    def test_wrong_first_client_window_is_rejected(self):
        raw = raw_result()
        raw["clients"][0]["first_ns"] = raw["measure_start_mono_ns"] - 1
        self.assertFalse(measured(raw)["valid"])

    def test_get_without_promised_content_samples_is_rejected(self):
        raw = raw_result()
        for client in raw["clients"]:
            client["validated_keys"] = 0
        self.assertFalse(measured(raw)["valid"])

    def test_histogram_and_global_percentile_tamper_rejected(self):
        raw = raw_result()
        raw["histogram"][str(bucket(1000))] -= 1
        self.assertFalse(measured(raw)["valid"])
        raw = raw_result()
        raw["p99"]["upper_us"] += 1
        self.assertFalse(measured(raw)["valid"])


def resource_samples():
    result = []
    for seconds in (9, 10, 15, 20, 21):
        sample = {"mono_ns": seconds * 1_000_000_000}
        for role, cpu, read, write, memory, anon, shmem in [
                ("server", 1, 1, 5, 2, 1, .25), ("client", 2, 2, 7, 3, 1.5, .5)]:
            sample[role] = {"cpu_usec": seconds * cpu * 1_000_000,
                            "read_bytes": seconds * read * (1 << 20),
                            "write_bytes": seconds * write * (1 << 20),
                            "memory": int(memory * (1 << 30)), "anon": int(anon * (1 << 30)),
                            "shmem": int(shmem * (1 << 30)), "file": 123456789}
        result.append(sample)
    return result


class ResourceTests(unittest.TestCase):
    def window(self, samples):
        row = {"start_mono_ns": 10_000_000_000, "end_mono_ns": 20_000_000_000, "duration_s": 10}
        subject.resource_window(row, samples, [sample["mono_ns"] for sample in samples])
        return row

    def test_disjoint_cgroup_totals_and_anon_shmem_do_not_double_count_file(self):
        row = self.window(resource_samples())
        self.assertEqual(row["resource_samples"], 3)
        self.assertEqual(row["resource_coverage"], 1)
        self.assertEqual(row["total_cpu_cores"], 3)
        self.assertEqual(row["total_read_mib_s"], 3)
        self.assertEqual(row["total_write_mib_s"], 12)
        self.assertEqual(row["peak_charged_memory_gib"], 5)
        self.assertEqual(row["peak_anon_shmem_gib"], 3.25)

    def test_counter_reset_and_intermediate_missing_are_null(self):
        samples = resource_samples()[1:-1]
        samples[1]["server"]["cpu_usec"] = 1
        samples[1]["client"]["read_bytes"] = None
        row = self.window(samples)
        self.assertIsNone(row["server_cpu_cores"])
        self.assertIsNone(row["total_cpu_cores"])
        self.assertIsNone(row["total_read_mib_s"])
        self.assertEqual(row["total_write_mib_s"], 12)

    def test_single_sample_has_memory_but_no_counter_rate_or_coverage(self):
        row = self.window(resource_samples()[2:3])
        self.assertEqual(row["peak_charged_memory_gib"], 5)
        for metric in ("total_cpu_cores", "total_read_mib_s", "total_write_mib_s", "resource_span_s", "resource_coverage"):
            self.assertIsNone(row[metric])

    def test_missing_memory_is_unknown_not_zero(self):
        samples = resource_samples()
        for sample in samples:
            sample["server"]["memory"] = None
        row = self.window(samples)
        self.assertIsNone(row["peak_charged_memory_gib"])
        self.assertEqual(row["peak_anon_shmem_gib"], 3.25)


class AggregateAndPackagingTests(unittest.TestCase):
    def test_workflow_output_option_alias_reaches_report_renderer(self):
        for option in ("--out", "--output"):
            with self.subTest(option=option), \
                    patch("sys.argv", ["batch_report.py", "--suite", "fixture-suite", option, "fixture-report", "--rfc", "fixture-docs"]), \
                    patch.object(batch_report, "render", return_value={"fixture": True}) as render, \
                    contextlib.redirect_stdout(io.StringIO()):
                batch_report.main()
                self.assertEqual(render.call_args.args[0].out, Path("fixture-report"))

    def test_aggregate_requires_distinct_complete_repeats_and_preserves_na(self):
        rows = [dict(measured(raw_result()), repeat=repeat) for repeat in range(3)]
        self.assertEqual(subject.aggregate(rows, 3)[0]["status"], "complete")
        rows[-1]["repeat"] = 1
        result = subject.aggregate(rows, 3)[0]
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["metrics"]["keys_s"]["median"])
        na = [dict(product="lmcache", operation="get", batch=16, repeat=repeat,
                   valid=False, status="not_supported") for repeat in range(3)]
        self.assertEqual(subject.aggregate(na, 3)[0]["status"], "not_supported")

    def test_metadata_compaction_keeps_all_client_references(self):
        row = measured(raw_result())
        analysis = {"rows": [row], "samples": []}
        compact = batch_report.compact_payload(analysis)
        observed = [compact["metadata_pool"][index] for index in compact["rows"][0]["client_metadata_ids"]]
        self.assertEqual(observed, row["client_metadata"])
        self.assertEqual(len(observed), 8)
        self.assertEqual(len(compact["metadata_pool"]), 1)

    def test_report_symlink_parent_cannot_create_output_inside_raw_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite, docs = root / "raw-suite", root / "docs"
            suite.mkdir()
            docs.mkdir()
            alias = root / "apparently-separate-parent"
            alias.symlink_to(suite, target_is_directory=True)
            args = SimpleNamespace(suite=suite, rfc=docs, out=alias / "report", device="253:2",
                                   allow_partial=True)
            with patch.object(batch_report, "analyze", side_effect=AssertionError("containment must be checked before evidence reads")):
                with self.assertRaisesRegex(ValueError, "separate"):
                    batch_report.render(args)
            self.assertFalse((suite / "report").exists())


class RuntimeBindingTests(unittest.TestCase):
    def fixture(self, parent):
        suite, runtime = parent / "suite", parent / "measured-inputs"
        suite.mkdir()
        (runtime / "configs").mkdir(parents=True)
        files = {"batch_benchmark.py": b"# local fixture only\nVALUE = 1\n",
                 "configs/lmcache.json": b'{"transport":"tcp"}\n'}
        inputs = {}
        for name, data in files.items():
            (runtime / name).write_bytes(data)
            inputs[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        stage = {"profile": "native-batch-tcp", "root": "/fixture/frozen-target", "inputs": inputs}
        self.write_stage(suite, stage)
        return suite, runtime, stage

    @staticmethod
    def write_stage(suite, stage):
        (suite / "batch-stage.json").write_text(json.dumps(stage))

    def verify(self, suite, runtime, require=True):
        return batch_report.verify_runtime_inputs(suite, runtime, require=require)

    def test_exact_stage_regular_files_have_sha_and_size_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, runtime, stage = self.fixture(Path(directory))
            actual_stage, records = self.verify(suite, runtime)
            self.assertEqual(actual_stage, stage)
            self.assertEqual(len(records), 2)
            self.assertEqual({record["sha256"] for record in records}, {item["sha256"] for item in stage["inputs"].values()})
            for record in records:
                self.assertFalse(Path(record["path"]).is_absolute())
                self.assertNotIn("..", Path(record["path"]).parts)
                data = Path(record["source"]).read_bytes()
                self.assertEqual(record["bytes"], len(data))
                self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())

    def test_same_size_sha_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, runtime, _ = self.fixture(Path(directory))
            path = runtime / "batch_benchmark.py"
            data = path.read_bytes()
            path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
            with self.assertRaises(ValueError):
                self.verify(suite, runtime)

    def test_sha_matching_but_recorded_size_wrong_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, runtime, stage = self.fixture(Path(directory))
            stage["inputs"]["batch_benchmark.py"]["bytes"] += 1
            self.write_stage(suite, stage)
            with self.assertRaises(ValueError):
                self.verify(suite, runtime)

    def test_missing_measured_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, runtime, _ = self.fixture(Path(directory))
            (runtime / "batch_benchmark.py").unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.verify(suite, runtime)

    def test_stage_traversal_and_absolute_paths_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            suite, runtime, stage = self.fixture(parent)
            escaped = parent / "escape.py"
            escaped.write_bytes(b"outside fixture\n")
            record = {"bytes": escaped.stat().st_size, "sha256": hashlib.sha256(escaped.read_bytes()).hexdigest()}
            for name in ("../escape.py", str(escaped)):
                with self.subTest(name=name):
                    altered = deepcopy(stage)
                    altered["inputs"][name] = record
                    self.write_stage(suite, altered)
                    with self.assertRaises(ValueError):
                        self.verify(suite, runtime)

    def test_measured_file_symlink_rejected_even_with_matching_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            suite, runtime, _ = self.fixture(parent)
            path = runtime / "batch_benchmark.py"
            target = parent / "identical-but-external.py"
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
            with self.assertRaises(ValueError):
                self.verify(suite, runtime)

    def test_measured_parent_symlink_rejected_even_with_matching_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            suite, runtime, _ = self.fixture(parent)
            config = runtime / "configs/lmcache.json"
            data = config.read_bytes()
            config.unlink()
            config.parent.rmdir()
            outside = parent / "identical-but-external-directory"
            outside.mkdir()
            (outside / "lmcache.json").write_bytes(data)
            (runtime / "configs").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.verify(suite, runtime)

    def test_unlisted_extra_file_is_not_returned_for_archival(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, runtime, stage = self.fixture(Path(directory))
            (runtime / "DO_NOT_ARCHIVE.json").write_text('{"local-extra":true}')
            _, records = self.verify(suite, runtime)
            self.assertEqual(len(records), len(stage["inputs"]))
            self.assertFalse(any("DO_NOT_ARCHIVE" in str(record) for record in records))

    def test_missing_runtime_must_not_pass_required_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, _, _ = self.fixture(Path(directory))
            with self.assertRaises(ValueError):
                self.verify(suite, None, require=True)

    def test_optional_partial_preview_has_no_false_runtime_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, _, stage = self.fixture(Path(directory))
            actual_stage, records = self.verify(suite, None, require=False)
            self.assertEqual(actual_stage, stage)
            self.assertEqual(records, [])

    def preview(self, parent):
        suite, runtime, stage = self.fixture(parent)
        products = {"products": [{"name": name, "adapter": f"/fixture/batch_{name}.py",
                                  "config": f"/fixture/configs/{name}.json"}
                                 for name in ["datasystem", "mooncake", "lmcache"]]}
        product_bytes = json.dumps(products).encode()
        (runtime / "products.json").write_bytes(product_bytes)
        (suite / "products.json").write_bytes(product_bytes)
        stage["inputs"]["products.json"] = {
            "bytes": len(product_bytes), "sha256": hashlib.sha256(product_bytes).hexdigest()}
        self.write_stage(suite, stage)
        (runtime / "DO_NOT_ARCHIVE.json").write_text('{"extra":true}')
        docs = parent / "docs"
        docs.mkdir()
        (docs / "fixture.md").write_text("# Partial packaging fixture\n")
        analysis = {"schema_version": 1, "profile": "native-batch-tcp", "suite": str(suite),
                    "parameters": {"root": stage["root"], "action": "run"}, "plan": [],
                    "summary": {"ended": "fixture-end", "valid": False, "failed": ["partial unit fixture"]},
                    "rows": [], "aggregates": [], "repeats": 3, "counts": {},
                    "issues": [{"errors": ["partial unit fixture"]}], "passed": False,
                    "samples": [], "sampling": {"samples": 0}, "events": [],
                    "aggregate_scope": "fixture", "latency_scope": "fixture", "resource_scope": "fixture"}
        args = SimpleNamespace(suite=suite, rfc=docs, out=parent / "report", device="253:2",
                               allow_partial=True, runtime_inputs=runtime, builds=None, gate=None,
                               environment=None, operation_log=[], raw_evidence=False)
        return runtime, stage, args, analysis

    def test_zip_measured_runtime_is_exact_stage_bytes_and_excludes_extras(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, stage, args, analysis = self.preview(Path(directory))
            with patch.object(batch_report, "analyze", return_value=analysis):
                result = batch_report.render(args)
            self.assertFalse(result["passed"])
            with zipfile.ZipFile(args.out / "reproduce.zip") as archive:
                self.assertIsNone(archive.testzip())
                prefix = "kvc-fair/measured-runtime/"
                names = [name for name in archive.namelist() if name.startswith(prefix)]
                self.assertEqual({name[len(prefix):] for name in names}, set(stage["inputs"]))
                for name, record in stage["inputs"].items():
                    data = archive.read(prefix + name)
                    self.assertEqual(data, (runtime / name).read_bytes())
                    self.assertEqual(len(data), record["bytes"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
                self.assertNotEqual(archive.read("kvc-fair/batch_benchmark.py"),
                                    archive.read(prefix + "batch_benchmark.py"),
                                    "Current tool source and frozen measured source are separate lineages")
                self.assertFalse(any("DO_NOT_ARCHIVE" in name for name in archive.namelist()))

    def test_source_replacement_after_verification_before_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, args, analysis = self.preview(Path(directory))
            original = batch_report.copy_file

            def replace_before_copy(source, out, relative):
                if relative == "measured-runtime/batch_benchmark.py":
                    path = Path(source)
                    path.write_bytes(path.read_bytes().replace(b"VALUE = 1", b"VALUE = 2"))
                return original(source, out, relative)

            with patch.object(batch_report, "analyze", return_value=analysis), \
                    patch.object(batch_report, "copy_file", side_effect=replace_before_copy):
                with self.assertRaises((ValueError, RuntimeError)):
                    batch_report.render(args)
            self.assertFalse((args.out / "index.html").exists())

    def test_successful_smoke_preview_is_not_formal_or_publication_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, args, analysis = self.preview(Path(directory))
            analysis.update(passed=True, issues=[], counts={"valid": 50, "not_supported": 10})
            analysis["summary"].update(action="smoke", valid=True, failed=[])
            analysis["parameters"]["action"] = "smoke"
            with patch.object(batch_report, "analyze", return_value=analysis):
                result = batch_report.render(args)
            self.assertTrue(result["passed"])
            self.assertFalse(result["publication_eligible"])
            html = (args.out / "index.html").read_text()
            self.assertIn("部分预览 · smoke", html)
            self.assertNotIn("矩阵完成 · 重算通过", html)

    def test_copied_runtime_changed_before_zip_append_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, args, analysis = self.preview(Path(directory))
            original = batch_report.reproduction_archive

            def replace_before_archive(scripts, docs, out):
                path = out / "measured-runtime/batch_benchmark.py"
                path.write_bytes(path.read_bytes().replace(b"VALUE = 1", b"VALUE = 2"))
                return original(scripts, docs, out)

            with patch.object(batch_report, "analyze", return_value=analysis), \
                    patch.object(batch_report, "reproduction_archive", side_effect=replace_before_archive):
                with self.assertRaises((ValueError, RuntimeError)):
                    batch_report.render(args)
            self.assertFalse((args.out / "index.html").exists())

    def test_zip_entry_sha_checked_even_when_crc_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, args, analysis = self.preview(Path(directory))
            original = zipfile.ZipFile.write

            def corrupt_archive_entry(archive, filename, arcname=None, *positional, **kwargs):
                if arcname == "kvc-fair/measured-runtime/batch_benchmark.py":
                    return archive.writestr(arcname, b"wrong measured bytes with valid ZIP CRC\n")
                return original(archive, filename, arcname, *positional, **kwargs)

            with patch.object(batch_report, "analyze", return_value=analysis), \
                    patch.object(zipfile.ZipFile, "write", new=corrupt_archive_entry):
                with self.assertRaises((ValueError, RuntimeError)):
                    batch_report.render(args)
            self.assertFalse((args.out / "index.html").exists())


class ConfigurationHistoryTests(unittest.TestCase):
    @staticmethod
    def products(env):
        return {"products": [{"name": "datasystem", "env": {}},
                             {"name": "mooncake", "env": env},
                             {"name": "lmcache", "env": {}}]}

    def test_default_profile_comes_from_frozen_manifest_not_ambient_env(self):
        with patch.dict(os.environ, {"MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER": "999999"}):
            profile = batch_report.configuration_profile(self.products({}))
        self.assertFalse(profile["capacity_adapted"])
        settings = {item["key"]: item for item in profile["settings"]}
        self.assertEqual([item["value"] for item in profile["settings"]], [1024, 1024, 4, 1000])
        self.assertTrue(all(item["origin"] == "pinned source default" for item in settings.values()))

    def test_8192_profile_changes_only_the_two_frozen_capacity_values(self):
        env = {"MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER": "8192",
               "MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER": "8192"}
        profile = batch_report.configuration_profile(self.products(env))
        self.assertTrue(profile["capacity_adapted"])
        self.assertEqual([item["value"] for item in profile["settings"]], [8192, 8192, 4, 1000])
        self.assertTrue(all(item["origin"] == "explicit frozen product env" for item in profile["settings"][:2]))
        self.assertTrue(all(item["origin"] == "pinned source default" for item in profile["settings"][2:]))

    def test_partial_capacity_override_is_not_labelled_full_8192_profile(self):
        profile = batch_report.configuration_profile(self.products({"MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER": "8192"}))
        self.assertFalse(profile["capacity_adapted"])
        self.assertEqual([item["value"] for item in profile["settings"][:2]], [8192, 1024])

    @staticmethod
    def history(parent, name, summary):
        root = parent / name
        root.mkdir(parents=True)
        data = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode()
        (root / "summary.json").write_bytes(data)
        return root, data

    def test_real_failed_history_is_copied_exactly_and_not_performance(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            summary = {"action": "smoke", "valid": False, "failed": ["fixture native TRANSFER_FAIL(-800)"],
                       "ended": "fixture-end", "start_wall_ns": 1, "end_wall_ns": 2, "duration_s": .1}
            root, data = self.history(parent, "default-c8-smoke", summary)
            out = parent / "report"
            phases, records = batch_report.history_evidence([root], out)
            self.assertEqual(len(phases), 1)
            self.assertEqual(phases[0]["status"], "failed")
            self.assertEqual(phases[0]["failed"], summary["failed"])
            self.assertFalse(phases[0]["performance_eligible"])
            self.assertEqual((out / phases[0]["evidence"]).read_bytes(), data)
            self.assertEqual(records[0]["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(phases[0]["sha256"], records[0]["sha256"])

    def test_failed_or_unterminated_history_cannot_be_labelled_passed(self):
        cases = [{"valid": True, "ended": "fixture-end", "failed": ["fixture failure"]},
                 {"valid": True, "ended": "fixture-end", "fatal": "fixture fatal"},
                 {"valid": True, "failed": []}]
        for index, fields in enumerate(cases):
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                root, _ = self.history(parent, f"history-{index}", dict(action="gate", **fields))
                phases, _ = batch_report.history_evidence([root], parent / "out")
                self.assertNotEqual(phases[0]["status"], "passed")

    def test_primary_run_history_is_not_reclassified_as_default_gate_or_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root, _ = self.history(parent, "another-primary", {"action": "run", "valid": True, "ended": "fixture-end"})
            phases, records = batch_report.history_evidence([root], parent / "out")
            self.assertEqual(phases, [])
            self.assertEqual(len(records), 1)

    def test_terminal_failed_formal_is_separate_history_without_perf_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root, _ = self.history(parent, "failed-formal", {"action": "run", "valid": False,
                                    "ended": "fixture-end", "failed": ["RPC deadline exceeded"]})
            phases, _ = batch_report.history_evidence([root], parent / "out")
            self.assertEqual(len(phases), 1)
            self.assertEqual(phases[0]["action"], "failed_formal_attempt")
            self.assertEqual(phases[0]["status"], "failed")
            self.assertFalse(phases[0]["performance_eligible"])

    def test_duplicate_history_basenames_rejected_before_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            one, data = self.history(parent, "first/gate", {"action": "gate", "valid": False, "ended": "first"})
            two, _ = self.history(parent, "second/gate", {"action": "gate", "valid": False, "ended": "second"})
            out = parent / "out"
            with self.assertRaises(ValueError):
                batch_report.history_evidence([one, two], out)
            self.assertEqual((out / "history/gate/summary.json").read_bytes(), data)

    def test_original_executed_history_helper_bytes_are_archived(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root, _ = self.history(parent, "default-history", {"action": "gate", "valid": False, "ended": "fixture-end"})
            content = b"# Original executed helper, not current canonical tool\r\nVALUE = 'old'\r\n"
            (root / "probe-source.py").write_bytes(content)
            out = parent / "out"
            _, records = batch_report.history_evidence([root], out)
            record = next(row for row in records if row["path"].endswith("probe-source.py"))
            self.assertEqual((out / record["path"]).read_bytes(), content)
            self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())

    def test_real_queue_probe_completion_is_not_native_performance_success(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            summary = {"kind": "mooncake_tcp_queue_capacity_diagnostic", "ended": "fixture-end",
                       "completed_and_clean": True, "completed": ["default", "queue8192"],
                       "benchmark_eligible": False,
                       "profiles": [{"name": "default", "native_valid": False, "cleanup_ok": True, "errors": []},
                                    {"name": "queue8192", "native_valid": True, "cleanup_ok": True, "errors": []}]}
            root, _ = self.history(parent, "capacity-diagnostic", summary)
            phases, _ = batch_report.history_evidence([root], parent / "out")
            self.assertEqual(len(phases), 1)
            self.assertEqual(phases[0]["status"], "completed")
            self.assertFalse(phases[0]["performance_eligible"])
            self.assertEqual([row["native_valid"] for row in phases[0]["diagnostic_profiles"]], [False, True])
            self.assertNotIn("默认配置", phases[0]["label"])


class TCPProofBindingTests(unittest.TestCase):
    def fixture(self, parent):
        from batch_gate import payload
        gate, scripts = parent / "gate", parent / "parsers"
        gate.mkdir(); scripts.mkdir()
        inputs, sources, cases = {}, {}, []
        from kvcbench.collect.provenance import TCP
        for name in TCP:
            content = ("# Synthetic parser provenance fixture " + name + "\n").encode()
            (scripts / name).parent.mkdir(parents=True, exist_ok=True)
            (scripts / name).write_bytes(content)
            sources[name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        trace = gate / "trace.log"
        trace.write_bytes(b"synthetic structural fixture; not an executed syscall trace\n")
        inputs["trace.log"] = {"bytes": trace.stat().st_size, "sha256": hashlib.sha256(trace.read_bytes()).hexdigest()}
        for client in [0, 1]:
            for mode in ["insert", "upsert"]:
                name = f"r0-datasystem/client-{client}-{mode}.json"
                source = gate / name
                source.parent.mkdir(exist_ok=True)
                gate_cases = [{"batch_keys": batch, "key_prefix": f"fixture-c{client}-{mode}-b{batch}-"} for batch in [1, 16, 256, 4096]]
                source.write_text(json.dumps({"cases": gate_cases}))
                inputs[name] = {"bytes": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
                for item in gate_cases:
                    batch = item["batch_keys"]
                    key = item["key_prefix"] + "k" + str(batch - 1)
                    observed = {"successful_bytes": batch * 1024 + 64, "full_payload_observed": True,
                                "payload_sample": {"actual_medium": "TCP", "sample_size": 1024,
                                                   "sample_version": 0, "sample_key_index": batch - 1,
                                                   "sample_key": key, "sample_binary_sha256": hashlib.sha256(payload(key, 0, 1024)).hexdigest(),
                                                   "input_path": "trace.log"}}
                    cases.append({"client_index": client, "write_mode": mode, "batch_keys": batch,
                                  "minimum_directional_bytes": batch * 1024, "gate_path": name,
                                  "valid": True, "errors": [], "wire": {"malformed": [],
                                  "operations": {op: deepcopy(observed) for op in ["put", "get"]}}})
        raw = {"schema_version": 3, "kind": "datasystem_native_batch_tcp_any_key_full_payload_audit",
               "valid": True, "suite": str(gate), "inputs": inputs, "parser_sources": sources,
               "cases": cases, "scope": "Synthetic structural fixture only; no transport runtime evidence."}
        proof = parent / "proof.json"
        self.write(proof, raw)
        return gate, scripts, proof, raw

    @staticmethod
    def write(path, raw):
        path.write_text(json.dumps(raw))

    def test_complete_structural_fixture_has_external_hash_bound_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            gate, scripts, proof, raw = self.fixture(Path(directory))
            summary, records = batch_report.verify_tcp_evidence(proof, gate, scripts)
            self.assertEqual((summary["case_count"], summary["direction_count"]), (16, 32))
            self.assertEqual(summary["input_files_revalidated"], 5)
            self.assertEqual(len(records), 6)
            self.assertEqual(summary["full_evidence"]["sha256"], hashlib.sha256(proof.read_bytes()).hexdigest())
            self.assertIn("without reparsing syscalls", summary["summary_scope"])
            self.assertNotIn("inputs", summary)

    def test_missing_duplicate_or_wrong_gate_case_is_rejected(self):
        mutations = [lambda raw: raw["cases"].pop(),
                     lambda raw: raw["cases"].append(deepcopy(raw["cases"][0])),
                     lambda raw: raw["cases"][0].update(gate_path="r0-datasystem/client-1-upsert.json")]
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                gate, scripts, proof, raw = self.fixture(Path(directory))
                mutate(raw); self.write(proof, raw)
                with self.assertRaises(ValueError):
                    batch_report.verify_tcp_evidence(proof, gate, scripts)

    def test_byte_bounds_partial_samples_and_exact_key_hash_are_enforced(self):
        fields = [("successful_bytes", 0), ("full_payload_observed", False)]
        sample_fields = [("sample_size", 32), ("sample_key_index", 1), ("sample_version", 1),
                         ("sample_key", "another-key"), ("sample_binary_sha256", "0" * 64),
                         ("input_path", "not-bound.log"), ("actual_medium", "SHM")]
        for key, value in fields + sample_fields:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                gate, scripts, proof, raw = self.fixture(Path(directory))
                observed = raw["cases"][0]["wire"]["operations"]["get"]
                (observed if key in dict(fields) else observed["payload_sample"])[key] = value
                self.write(proof, raw)
                with self.assertRaises(ValueError):
                    batch_report.verify_tcp_evidence(proof, gate, scripts)

    def test_original_v2_parser_inventory_remains_readable_with_explicit_original_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            gate, scripts, proof, raw = self.fixture(Path(directory))
            raw['schema_version'] = 2
            raw['parser_sources'] = {}
            for name in ['batch_tcp_verify.py', 'batch_gate.py', 'tcp_probe_ds_sizes.py']:
                source = scripts / name
                source.write_text('# original-format synthetic source fixture\n')
                raw['parser_sources'][name] = {'bytes': source.stat().st_size, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            self.write(proof, raw)
            summary, records = batch_report.verify_tcp_evidence(proof, gate, scripts)
            self.assertEqual(summary['case_count'], 16)
            self.assertEqual(len(records), 4)

    def test_bound_raw_file_or_parser_tampering_is_rejected(self):
        for name in ["trace.log", "kvcbench/collect/audits/tcp.py"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                gate, scripts, proof, _ = self.fixture(Path(directory))
                path = gate / name if name.endswith(".log") else scripts / name
                path.write_bytes(path.read_bytes() + b"tampered\n")
                with self.assertRaises(ValueError):
                    batch_report.verify_tcp_evidence(proof, gate, scripts)

    def test_unsafe_input_path_and_file_symlink_are_rejected(self):
        for mode in ["traversal", "symlink"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                gate, scripts, proof, raw = self.fixture(parent)
                if mode == "traversal":
                    raw["inputs"]["../escape.log"] = raw["inputs"]["trace.log"]
                    self.write(proof, raw)
                else:
                    (parent / "external.log").write_bytes((gate / "trace.log").read_bytes())
                    (gate / "trace.log").unlink()
                    (gate / "trace.log").symlink_to(parent / "external.log")
                with self.assertRaises(ValueError):
                    batch_report.verify_tcp_evidence(proof, gate, scripts)


class IsolationAndFailedLineageTests(unittest.TestCase):
    def isolation(self, parent):
        directory = parent / "gate/r0-datasystem"
        directory.mkdir(parents=True)
        workers, processes = [], []
        for index in [0, 1]:
            instance = f"/target/datasystem/run/w{index}"
            command = [instance + "/datasystem_worker", "--rocksdb_store_dir=" + instance + "/rocksdb"]
            source = directory / f"worker-{index}.flags"
            source.write_text(command[1] + "\n")
            workers.append({"worker_index": index, "pid": 100 + index, "start_ticks": str(200 + index),
                            "path": instance + "/worker.flags", "live_argv": command,
                            "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
            processes.append({"name": f"worker{index}", "pid": 100 + index,
                              "start_ticks": str(200 + index), "command": command})
        process = directory / "processes.json"
        process.write_text(json.dumps({"processes": processes}))
        configuration = {"workers": workers, "processes_sha256": hashlib.sha256(process.read_bytes()).hexdigest()}
        (directory / "tcp-configuration.json").write_text(json.dumps(configuration))
        return directory.parent, directory, configuration

    def test_saved_per_instance_paths_flags_and_live_identity_match(self):
        with tempfile.TemporaryDirectory() as directory:
            gate, _, _ = self.isolation(Path(directory))
            result = batch_report.verify_datasystem_isolation(gate)
            self.assertTrue(result["verified"])
            self.assertEqual(len({row["rocksdb_store_dir"] for row in result["workers"]}), 2)
            self.assertIn("not a claim of current process liveness", result["scope"])

    def test_shared_default_or_missing_explicit_storage_path_is_rejected(self):
        for value in ["", "/tmp/default/rocksdb"]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                gate, folder, configuration = self.isolation(Path(directory))
                path = folder / "worker-0.flags"
                path.write_text("--rocksdb_store_dir=" + value + "\n" if value else "--worker_address=localhost\n")
                configuration["workers"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                (folder / "tcp-configuration.json").write_text(json.dumps(configuration))
                with self.assertRaises(ValueError):
                    batch_report.verify_datasystem_isolation(gate)

    def test_flag_or_process_hash_and_live_process_identity_cannot_be_substituted(self):
        for mode in ["flags", "process", "pid", "argv"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                gate, folder, configuration = self.isolation(Path(directory))
                if mode == "flags":
                    (folder / "worker-1.flags").write_text("--rocksdb_store_dir=/tmp/shared\n")
                elif mode == "process":
                    (folder / "processes.json").write_text('{"processes":[]}')
                elif mode == "pid":
                    configuration["workers"][1]["pid"] = 999
                else:
                    configuration["workers"][1]["live_argv"] = ["worker", "--rocksdb_store_dir=/tmp/shared"]
                (folder / "tcp-configuration.json").write_text(json.dumps(configuration))
                with self.assertRaises(ValueError):
                    batch_report.verify_datasystem_isolation(gate)

    def failed(self, parent):
        root = parent / "failed-r3"
        suite, runtime = root / "formal-batch-001", root / "runtime-inputs"
        suite.mkdir(parents=True); runtime.mkdir()
        content = b"# actually executed failed-attempt benchmark\n"
        (runtime / "batch_benchmark.py").write_bytes(content)
        stage = {"root": "/target/failed-r3", "inputs": {"batch_benchmark.py": {
            "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}}}
        (suite / "batch-stage.json").write_text(json.dumps(stage))
        (suite / "summary.json").write_text(json.dumps({"action": "run", "valid": False,
            "ended": "fixture-end", "failed": ["RPC deadline exceeded"]}))
        return root, runtime, content

    def test_failed_attempt_frozen_runtime_is_separate_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory); root, _, content = self.failed(parent)
            out = parent / "report"
            phases, _, bindings, records = batch_report.failed_attempt_evidence([root], out)
            self.assertEqual([row["action"] for row in phases], ["failed_formal_attempt"])
            self.assertFalse(bindings[0]["performance_eligible"])
            self.assertEqual(bindings[0]["source_root"], "/target/failed-r3")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["path"], "failed-attempts/failed-r3/frozen-runtime/batch_benchmark.py")
            self.assertEqual((out / records[0]["path"]).read_bytes(), content)
            self.assertFalse((out / "measured-runtime").exists())

    def test_missing_or_tampered_failed_runtime_cannot_use_current_tools(self):
        for mode in ["missing", "tampered"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory); root, runtime, _ = self.failed(parent)
                path = runtime / "batch_benchmark.py"
                if mode == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"# current canonical source cannot replace original\n")
                with self.assertRaises((ValueError, OSError)):
                    batch_report.failed_attempt_evidence([root], parent / "report")


class ResourceEvidenceBindingTests(unittest.TestCase):
    def fixture(self, parent):
        import batch_resource_audit
        suite, output = parent / "suite", parent / "resource-audit"
        (suite / "resources").mkdir(parents=True)
        (suite / "r0-datasystem").mkdir()
        output.mkdir()
        summary = {"valid": False, "monitor_exit": 0, "interference_exit": 0}
        (suite / "summary.json").write_text(json.dumps(summary))
        (suite / "resources/samples.jsonl").write_text('\n'.join(json.dumps({"mono_ns": at}) for at in [1000000000, 2000000000, 3000000000]) + '\n')
        for name, value in {
            "b1-get.json": {"valid": True, "success": 2, "failures": 0,
                            "measure_start_mono_ns": 1000000000, "measure_end_mono_ns": 3000000000},
            "b16-get.json": {"valid": False, "fatal": "fixture failed before SDK window"},
            "b256-get.json": {"status": "not_supported"},
        }.items():
            (suite / "r0-datasystem" / name).write_text(json.dumps(value))
        raw = batch_resource_audit.audit(suite)
        self.write(output, raw)
        return suite, output, {"summary": summary}, TOOL_ROOT, raw

    @staticmethod
    def write(output, raw):
        from batch_resource_audit import markdown
        (output / "resource-audit.json").write_text(json.dumps(raw))
        (output / "resource-audit.md").write_text(markdown(raw))

    def test_all_source_inputs_and_window_na_unmeasured_sets_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, output, analysis, scripts, _ = self.fixture(Path(directory))
            summary, records = batch_report.verify_resource_evidence(output, suite, analysis, scripts)
            self.assertTrue(summary["verified"])
            self.assertEqual(summary["sdk_windows"], 1)
            self.assertEqual(summary["input_files_revalidated"], 5)
            self.assertEqual(len(records), 3)

    def test_boundary_or_duplicate_identity_or_na_membership_tamper_rejected(self):
        mutations = [lambda raw: raw["windows"][0].update(start_mono_ns=1000000001),
                     lambda raw: raw["windows"].append(deepcopy(raw["windows"][0])),
                     lambda raw: raw.update(unsupported=[]),
                     lambda raw: raw.update(unmeasured_results=[])]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                suite, output, analysis, scripts, raw = self.fixture(Path(directory))
                mutation(raw); self.write(output, raw)
                with self.assertRaises(ValueError):
                    batch_report.verify_resource_evidence(output, suite, analysis, scripts)

    def test_wrong_executed_helper_or_narrative_source_is_rejected(self):
        for mode in ["helper", "narrative"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                suite, output, analysis, scripts, raw = self.fixture(Path(directory))
                if mode == "helper":
                    raw["helper_sha256"] = "0" * 64; self.write(output, raw)
                else:
                    (output / "resource-audit.md").write_text("fabricated resource interpretation\n")
                with self.assertRaises(ValueError):
                    batch_report.verify_resource_evidence(output, suite, analysis, scripts)

    def test_missing_duplicate_or_changed_raw_input_rejected(self):
        for mode in ["missing", "duplicate", "source"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                suite, output, analysis, scripts, raw = self.fixture(Path(directory))
                if mode == "missing":
                    raw["inputs"].pop();self.write(output, raw)
                elif mode == "duplicate":
                    raw["inputs"].append(deepcopy(raw["inputs"][0]));self.write(output, raw)
                else:
                    (suite / "resources/samples.jsonl").write_text('{"mono_ns":5000000000}\n')
                with self.assertRaises(ValueError):
                    batch_report.verify_resource_evidence(output, suite, analysis, scripts)


if __name__ == "__main__":
    unittest.main()
