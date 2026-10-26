import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from kvclab.onehop import (
    CHANNEL, CLIENT_GUARDS, CLIENT_MULTI, CLIENT_SYMBOLS, EXPECTED, ISSUE, META_SYMBOLS,
    WORKER_GUARDS, WORKER_MULTI,
    _stop_child, assess, client_main, parse_counts, probe_program, require_symbols, verify_one_hop,
)


class OneHopTests(unittest.TestCase):
    def valid_client(self):
        return {"valid": True, "read_matches": True, "put_calls": 1,
                "native_api": "mset", "batch_size": 1}

    def test_complete_counters_and_exact_readback_are_required(self):
        result = assess(dict(EXPECTED), self.valid_client(), 0, True, True)
        self.assertTrue(result["verified"])
        self.assertTrue(all(result["checks"].values()))

    def test_policy_or_empty_counts_are_never_runtime_proof(self):
        for counts in ({}, dict.fromkeys(EXPECTED, 0)):
            self.assertFalse(assess(counts, self.valid_client(), 0, True, True)["verified"])

    def test_any_extra_rpc_retry_or_remote_metadata_call_fails(self):
        for name in EXPECTED:
            with self.subTest(name=name):
                counts = dict(EXPECTED)
                counts[name] += 1
                self.assertFalse(assess(counts, self.valid_client(), 0, True, True)["verified"])

    def test_missing_local_metadata_positive_or_request_positive_fails(self):
        for name in ("local_multi", "worker_multi", "client_multi", "channel", "issue"):
            counts = dict(EXPECTED)
            counts[name] = 0
            self.assertFalse(assess(counts, self.valid_client(), 0, True, True)["verified"])

    def test_same_host_shm_probe_then_inline_publish_remains_two_rpcs_and_fails(self):
        counts = dict(EXPECTED)
        counts.update(client_multicreate=1, client_multicreate_channel=1, client_multicreate_issue=1,
                      worker_multicreate=1, channel=2, issue=2)
        result = assess(counts, self.valid_client(), 0, True, True)
        self.assertFalse(result["verified"])
        self.assertTrue(result["checks"]["attributed_call_totals_consistent"])
        self.assertFalse(result["checks"]["exact_rpc_and_metadata_counts"])

    def test_missing_or_unattributed_create_request_cannot_be_hidden_in_publish_total(self):
        counts = dict(EXPECTED)
        counts.update(client_multicreate=1, client_multicreate_channel=1, client_multicreate_issue=1)
        result = assess(counts, self.valid_client(), 0, True, True)
        self.assertFalse(result["verified"])
        self.assertFalse(result["checks"]["attributed_call_totals_consistent"])

    def test_missing_ready_end_or_clean_exit_fails(self):
        for code, ready, ended in ((1, True, True), (0, False, True), (0, True, False)):
            self.assertFalse(assess(dict(EXPECTED), self.valid_client(), code, ready, ended)["verified"])

    def test_scalar_or_multiple_sdk_calls_and_failed_value_verification_fail(self):
        for update in ({"native_api": "set"}, {"put_calls": 2}, {"batch_size": 16},
                       {"valid": False}, {"read_matches": False}):
            self.assertFalse(assess(dict(EXPECTED), {**self.valid_client(), **update}, 0, True, True)["verified"])

    def test_count_parser_requires_each_named_counter_exactly_once(self):
        text = "KVC_ONEHOP_ATTACHED abc\n" + "\n".join(f"@counts[{name}]: {value}" for name, value in EXPECTED.items())
        self.assertEqual(EXPECTED, parse_counts(text))
        for bad in (text + "\n@counts[issue]: 1", text.replace("@counts[issue]: 1", ""),
                    text + "\n@counts[unknown]: 0"):
            with self.assertRaises(ValueError):
                parse_counts(bad)

    def test_program_uses_exact_symbols_and_pid_filters(self):
        workers = [{"pid": 101, "library": "/safe/worker.so"}, {"pid": 102, "library": "/safe/worker.so"}]
        program = probe_program(201, "/safe/client.so", "/safe/brpc.so", workers, "a" * 32)
        for symbol in (CLIENT_MULTI, CHANNEL, ISSUE, WORKER_MULTI, *META_SYMBOLS.values(),
                       *CLIENT_GUARDS.values(), *WORKER_GUARDS.values()):
            self.assertIn(symbol, program)
        self.assertIn("pid == 101 && @active", program)
        self.assertIn("pid == 102 && @active", program)
        self.assertIn("@controllers[pid,arg2] = $kind", program)
        self.assertIn("@controllers[pid,arg0]", program)
        self.assertIn('print(@counts); clear(@counts)', program)
        self.assertNotIn("CreateMeta*", program)
        self.assertNotIn(".cold", program)
        self.assertNotIn("@active = 0", program)
        self.assertIn("delete(@controllers[pid,@current_controller[pid,tid]])", program)
        for name in CLIENT_SYMBOLS:
            self.assertIn(f'@counts["{name}_channel"] = sum(1)', program)
            self.assertIn(f'@counts["{name}_issue"] = sum(1)', program)

    def test_program_rejects_injected_probe_paths_or_pids(self):
        for client, library, workers, nonce in (
            (201, '/safe/a";system("bad")', [], "a" * 32),
            ("201 || 1", "/safe/client.so", [], "a" * 32),
            (201, "/safe/client.so", [{"pid": "1 || 1", "library": "/safe/worker.so"}], "a" * 32),
            (201, "/safe/client.so", [], "not-a-nonce"),
        ):
            with self.assertRaises(ValueError):
                probe_program(client, library, "/safe/brpc.so", workers, nonce)

    def test_missing_symbols_fail_before_attach(self):
        with patch("kvclab.onehop.subprocess.run", return_value=Mock(stdout="000 T SomeOtherSymbol\n")):
            with self.assertRaisesRegex(RuntimeError, "symbols are missing"):
                require_symbols("/unused/lib.so", [CLIENT_MULTI])

    def test_missing_tool_returns_unverified_receipt_without_starting_children(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "qualification"
            with patch("kvclab.onehop.shutil.which", return_value=None), patch("kvclab.onehop.subprocess.Popen") as spawn:
                result = verify_one_hop({}, {"product": "datasystem", "active": True}, "/unused/config.json", out)
            self.assertFalse(result["verified"])
            self.assertEqual("not_verified", result["status"])
            self.assertIn("Missing qualification tool", result["reason"])
            self.assertEqual(result, json.loads((out / "one-hop.json").read_text()))
            spawn.assert_not_called()

    def test_inactive_or_other_product_never_runs_qualification(self):
        for deployment in ({"product": "mooncake", "active": True}, {"product": "datasystem", "active": False}):
            with tempfile.TemporaryDirectory() as directory, patch("kvclab.onehop.subprocess.Popen") as spawn:
                result = verify_one_hop({}, deployment, "/unused/config", Path(directory) / "out")
                self.assertFalse(result["verified"])
                spawn.assert_not_called()

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one-hop.json"
            path.write_text("preserved")
            with self.assertRaises(FileExistsError):
                verify_one_hop({}, {}, "/unused/config", directory)
            self.assertEqual("preserved", path.read_text())

    def test_probe_cleanup_only_signals_the_recorded_owned_child(self):
        process = Mock(pid=101, returncode=None)
        process.poll.side_effect = [None, 0]
        process.wait.side_effect = lambda **_kwargs: setattr(process, "returncode", 0)
        with patch("kvclab.onehop.ticks", return_value="recorded"), patch("kvclab.onehop.member", return_value=True):
            outcome = _stop_child(process, {"start_ticks": "recorded"}, Path("/owned/monitor"), True)
        self.assertTrue(outcome["terminal"])
        import signal
        process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_cleanup_refuses_reused_or_outside_domain_pid(self):
        for recorded, actual, member in (("old", "new", True), ("same", "same", False)):
            process = Mock(pid=101)
            process.poll.return_value = None
            with patch("kvclab.onehop.ticks", return_value=actual), patch("kvclab.onehop.member", return_value=member):
                with self.assertRaisesRegex(RuntimeError, "ownership changed"):
                    _stop_child(process, {"start_ticks": recorded}, Path("/owned/monitor"), True)
            process.send_signal.assert_not_called()
            process.kill.assert_not_called()

    def test_client_performs_exact_adapter_native_batch_once_then_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text('{"value_size":1024}')
            adapter = Mock(metadata={"data_placement_policy": "PREFERRED_META_OWNER"})
            written = {}

            def put(keys, values):
                self.assertEqual(1, len(keys))
                self.assertEqual(1, len(values))
                written.update(key=keys[0], value=values[0])

            def get(keys):
                self.assertEqual([written["key"]], keys)
                return [written["value"]]

            adapter.put_batch.side_effect = put
            adapter.get_batch.side_effect = get
            output = io.StringIO()
            with patch("kvclab.adapters.datasystem.new_adapter", return_value=adapter), patch(
                "kvclab.onehop.sys.stdin", io.StringIO("put\nverify\n")
            ), contextlib.redirect_stdout(output):
                status = client_main(config, root / "client-result.json", "a" * 32)
            result = json.loads((root / "client-result.json").read_text())
            self.assertEqual(0, status)
            self.assertTrue(result["valid"])
            self.assertEqual(1, result["put_calls"])
            self.assertEqual("mset", result["native_api"])
            adapter.put_batch.assert_called_once()
            adapter.get_batch.assert_called_once()
            adapter.close.assert_called_once()
            self.assertIn("KVC_ONEHOP_CLIENT_READY", output.getvalue())
            self.assertIn("KVC_ONEHOP_PUT_DONE", output.getvalue())
            self.assertNotIn("throughput", result)

    def test_client_rejects_wrong_readback_or_unreleased_write(self):
        for release in ("put\nverify\n", "incorrect\n"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / "config.json"
                config.write_text('{"value_size":32}')
                adapter = Mock(metadata={})
                adapter.get_batch.return_value = [b"wrong"]
                with patch("kvclab.adapters.datasystem.new_adapter", return_value=adapter), patch(
                    "kvclab.onehop.sys.stdin", io.StringIO(release)
                ), contextlib.redirect_stdout(io.StringIO()):
                    status = client_main(config, root / "result.json", "a" * 32)
                self.assertEqual(1, status)
                self.assertFalse(json.loads((root / "result.json").read_text())["valid"])
                if release == "incorrect\n":
                    adapter.put_batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
