import importlib.util
import json
from pathlib import Path
from tests.paths import TOOL_ROOT
import subprocess
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("batch_gate", (TOOL_ROOT / "batch_gate.py"))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class FakeAdapter:
    def __init__(self, *, semantics="insert_or_update", limited=False):
        self.data = {}
        self.calls = []
        self.closed = False
        self.semantics = semantics
        self.actual_semantics = semantics
        self.limited = limited
        self.metadata = {
            "product": "fixture", "native_semantics": {"write_existing_key": semantics},
            "capabilities": {"put_batch": True, "get_batch": True, "exists_batch": True},
            "max_batch_keys": {"put": 10000, "get": 10000, "exists": 100000},
        }
        if limited:
            metadata = dict(self.metadata, capabilities={"put": {"supported_batch_keys": [1]},
                            "get": {"supported_batch_keys": [1]},
                            "exists": {"supported_batch_keys": [1, 16, 256, 4096]}})
            self.metadata = lambda: metadata
        self.reorder_get = False
        self.prefix_exists = False
        self.fail_exists = None
        self.fail_close = False

    def put_batch(self, keys, values):
        self.calls.append(("put", list(keys), list(values)))
        if self.limited and len(keys) != 1:
            raise AssertionError("Unsupported data batch must not be called")
        for key, value in zip(keys, values):
            if key not in self.data or self.actual_semantics == "insert_or_update":
                self.data[key] = value

    def get_batch(self, keys):
        self.calls.append(("get", list(keys)))
        if self.limited and len(keys) != 1:
            raise AssertionError("Unsupported data batch must not be called")
        if self.reorder_get:
            keys = sorted(keys, key=lambda key: int(key.rsplit("k", 1)[1]))
        return [self.data[key] for key in keys]

    def exists_batch(self, keys):
        self.calls.append(("exists", list(keys)))
        if self.fail_exists is not None:
            raise self.fail_exists
        answer = [key in self.data for key in keys]
        if self.prefix_exists:
            first_miss = answer.index(False) if False in answer else len(answer)
            answer = [i < first_miss for i in range(len(answer))]
        return answer

    def close(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("unlock/close failed")


def run(adapter, sizes=(1, 16, 256, 4096), index=0, size=1024):
    return gate.run_gate("fake-adapter.py", {}, client_index=index, batch_sizes=list(sizes), value_size=size,
                         run_id="fixture-gate", namespace="deterministic-fixture", loader=lambda path, config: adapter)


class GateTests(unittest.TestCase):
    def test_all_four_native_batches_check_full_bytes_and_order(self):
        adapter = FakeAdapter()
        result = run(adapter)
        self.assertTrue(result["valid"], result.get("fatal"))
        self.assertEqual(len(result["cases"]), 4)
        self.assertEqual(len(adapter.data), 1 + 16 + 256 + 4096)
        self.assertTrue(adapter.closed)
        self.assertEqual([len(call[1]) for call in adapter.calls if call[0] == "put"], [1, 1, 16, 16, 256, 256, 4096, 4096])
        for case in result["cases"]:
            self.assertEqual(case["semantic_verification_keys"], case["batch_keys"])
        self.assertTrue(all(not step["benchmark_eligible"] for step in result["steps"]))
        json.dumps(result, allow_nan=False)

    def test_insert_noop_is_checked_against_old_distinct_bytes(self):
        adapter = FakeAdapter(semantics="success_without_rewrite")
        result = run(adapter, [1, 16])
        self.assertTrue(result["valid"])
        for key, value in adapter.data.items():
            self.assertEqual(value, gate.payload(key, 0, 1024))

    def test_limited_data_operations_use_only_labeled_scalar_setup_and_spotchecks(self):
        adapter = FakeAdapter(semantics="success_without_rewrite", limited=True)
        result = run(adapter)
        self.assertTrue(result["valid"], result.get("fatal"))
        self.assertTrue(all(len(call[1]) == 1 for call in adapter.calls if call[0] in {"put", "get"}))
        self.assertTrue(any(len(call[1]) == 4096 for call in adapter.calls if call[0] == "exists"))
        case = result["cases"][-1]
        self.assertFalse(case["put_native_supported"])
        self.assertFalse(case["get_native_supported"])
        self.assertEqual(case["semantic_verification_keys"], 3)
        setup = [step for step in result["steps"] if step["name"] == "setup_scalar_puts" and step["batch_keys"] == 4096][0]
        self.assertTrue(setup["setup_scalar_only"])
        self.assertEqual(setup["result"]["adapter_calls"], 4096)
        self.assertEqual(len([step for step in result["steps"] if step["status"] == "not_applicable" and step["name"] == "get_full_exact"]), 3)

    def test_wrong_reverse_order_fails_closed_and_still_closes(self):
        adapter = FakeAdapter()
        adapter.reorder_get = True
        result = run(adapter, [16, 256])
        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_steps"], ["get_reverse_exact"])
        self.assertEqual(result["cases"][1]["status"], "not_run_after_failure")
        self.assertTrue(adapter.closed)
        failure = next(step for step in result["steps"] if step["status"] == "failed")
        self.assertGreater(failure["error"]["details"]["mismatched_keys"], 0)

    def test_prefix_only_exists_cannot_pass_sparse_independent_key_vector(self):
        adapter = FakeAdapter()
        adapter.prefix_exists = True
        result = run(adapter, [16])
        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_steps"], ["exists_mixed_sparse"])
        failure = next(step for step in result["steps"] if step["status"] == "failed")
        self.assertNotEqual(failure["error"]["details"]["expected_bits"], failure["error"]["details"]["observed_bits"])
        self.assertTrue(adapter.closed)

    def test_transport_timeout_is_a_failure_not_a_miss_and_never_retried(self):
        adapter = FakeAdapter()
        adapter.fail_exists = TimeoutError("transport timeout")
        result = run(adapter, [16])
        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_steps"], ["fresh_keys_initially_absent"])
        self.assertEqual(len(adapter.calls), 1)
        self.assertTrue(adapter.closed)

    def test_declared_insert_cannot_silently_overwrite(self):
        adapter = FakeAdapter(semantics="success_without_rewrite")
        adapter.actual_semantics = "insert_or_update"
        result = run(adapter, [16])
        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_steps"], ["verify_existing_write_full_exact"])

    def test_declared_upsert_cannot_ack_without_updating(self):
        adapter = FakeAdapter()
        adapter.actual_semantics = "success_without_rewrite"
        result = run(adapter, [16])
        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_steps"], ["verify_existing_write_full_exact"])

    def test_close_failure_invalidates_otherwise_successful_gate(self):
        adapter = FakeAdapter()
        adapter.fail_close = True
        result = run(adapter, [1])
        self.assertFalse(result["valid"])
        self.assertTrue(result["cases"][0]["valid"])
        self.assertEqual(result["failed_steps"], ["close_adapter"])

    def test_non_json_metadata_fails_and_closes(self):
        adapter = FakeAdapter()
        adapter.metadata["not_json"] = object()
        result = run(adapter, [1])
        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_steps"], ["inspect_metadata"])
        self.assertTrue(adapter.closed)
        json.dumps(result, allow_nan=False)

    def test_non_json_exception_details_do_not_destroy_failure_artifact(self):
        adapter = FakeAdapter()
        error = RuntimeError("failure")
        error.details = {"not_json": object()}
        adapter.fail_exists = error
        result = run(adapter, [1])
        self.assertFalse(result["valid"])
        json.dumps(result, allow_nan=False)
        failure = next(step for step in result["steps"] if step["status"] == "failed")
        self.assertEqual(failure["error"]["details_unserializable_type"], "dict")

    def test_initialization_failure_has_timing_and_no_fake_case_success(self):
        def fail(path, config):
            raise ImportError("native SDK unavailable")
        result = gate.run_gate("fake", {}, client_index=0, batch_sizes=[1], value_size=1024,
                               run_id="failure", loader=fail)
        self.assertFalse(result["valid"])
        self.assertEqual(result["cases"], [])
        self.assertEqual(result["failed_steps"], ["initialize_adapter"])
        self.assertGreaterEqual(result["steps"][0]["duration_ns"], 0)

    def test_missing_or_invalid_capability_cannot_silently_be_treated_as_supported(self):
        for caps in [{}, {"put": {"supported_batch_keys": [True]}}]:
            adapter = FakeAdapter()
            adapter.metadata["capabilities"] = caps
            result = run(adapter, [1])
            self.assertFalse(result["valid"])
            self.assertEqual(result["failed_steps"], ["inspect_metadata"])
            self.assertEqual(adapter.calls, [])

    def test_b1_mixed_is_not_applicable_but_both_states_are_observed(self):
        result = run(FakeAdapter(), [1])
        names = {step["name"]: step for step in result["steps"]}
        self.assertEqual(names["exists_mixed_sparse"]["status"], "not_applicable")
        self.assertEqual(names["exists_full_hit"]["result"]["observed_bits"], "1")
        self.assertEqual(names["exists_all_miss"]["result"]["observed_bits"], "0")

    def test_disjoint_client_namespaces_and_key_derived_payloads(self):
        first, second = FakeAdapter(), FakeAdapter()
        left, right = run(first, [16], index=0), run(second, [16], index=1)
        self.assertTrue(left["valid"] and right["valid"])
        self.assertFalse(set(first.data) & set(second.data))
        self.assertEqual(len(set(first.data.values())), 16)
        self.assertNotEqual(left["key_namespace"], right["key_namespace"])

    def test_step_wall_and_monotonic_intervals_are_separate_from_sdk_benchmarks(self):
        result = run(FakeAdapter(), [1, 16])
        total = 0
        for step in result["steps"]:
            if step["status"] == "not_applicable":
                self.assertIsNone(step["duration_s"])
                continue
            self.assertEqual(step["duration_ns"], step["end_mono_ns"] - step["start_mono_ns"])
            self.assertGreaterEqual(step["end_wall_ns"], step["start_wall_ns"])
            total += step["duration_ns"]
        self.assertLessEqual(total, result["duration_ns"])


class OutputTests(unittest.TestCase):
    def test_immutable_json_cannot_replace_original_and_leaves_no_pending_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.json"
            gate.write_immutable(path, {"valid": True})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                gate.write_immutable(path, {"valid": False})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_cli_runs_in_a_fresh_directory_without_product_sdk_or_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = root / "fake.py"
            adapter.write_text("from tests.unit.run.test_batch_gate import FakeAdapter\n"
                               "def new_adapter(config):\n"
                               "    assert config['value_size']==32 and config['client_index']==1\n"
                               "    return FakeAdapter()\n")
            config = root / "config.json"
            config.write_text("{}\n")
            out = root / "gate.json"
            command = [sys.executable, str(Path(gate.__file__)), "--adapter", str(adapter), "--config", str(config),
                       "--client-index", "1", "--run-id", "cli", "--out", str(out),
                       "--batch-sizes", "1", "16", "--value-size", "32"]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            result = json.loads(out.read_text())
            self.assertTrue(result["valid"])
            original = out.read_bytes()
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertIn("already exists", second.stderr)
            self.assertEqual(out.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
