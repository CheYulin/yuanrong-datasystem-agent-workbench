import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from tests.unit.run.test_batch_gate import FakeAdapter
import batch_gate


from kvcbench.collect.audits import tcp as audit


def stamp(ns):
    return f"{ns // 1_000_000_000}.{ns % 1_000_000_000:09d}"


def fixture_suite(root):
    cell = root / "r0-datasystem"
    cell.mkdir()
    workers = []
    processes = []
    for index in [0, 1]:
        flags = {"worker_address": f"127.0.0.1:{31531 + index}", "ipc_through_shared_memory": "false",
                 "enable_rdma": "false", "enable_urma": "false"}
        flags_path = cell / f"worker-{index}.flags"
        flags_path.write_text("".join(f"--{key}={value}\n" for key, value in flags.items()))
        workers.append({"worker_index": index, "pid": 1000 + index, "start_ticks": "10",
                        "flags": flags, "live_argv": ["worker", *[f"--{key}={value}" for key, value in flags.items()]],
                        "sha256": audit.file_record(flags_path)["sha256"]})
        processes.append({"name": f"worker{index}", "pid": 1000 + index, "start_ticks": "10"})
    manifest = cell / "processes.json"
    manifest.write_text(json.dumps({"processes": processes}))
    (cell / "tcp-configuration.json").write_text(json.dumps({"workers": workers, "processes_sha256": audit.file_record(manifest)["sha256"]}))
    for index in [0, 1]:
        for mode in ["insert", "upsert"]:
            adapter = FakeAdapter()
            adapter.metadata["source_sha"] = audit.SOURCE_SHA
            gate = batch_gate.run_gate("fake", {}, client_index=index, batch_sizes=audit.BATCHES,
                                       value_size=1024, run_id=f"offline-{index}-{mode}", namespace="fixture",
                                       loader=lambda path, config: adapter)
            assert gate["valid"]
            (cell / f"client-{index}-{mode}.json").write_text(json.dumps(gate))
            traces = cell / f"wire-client-{index}-{mode}"
            traces.mkdir()
            lines = []
            for case in gate["cases"]:
                count = case["batch_keys"]
                key = case["key_prefix"] + "k0"
                prefix = batch_gate.payload(key, 0, 1024)
                escaped = "".join(f"\\x{value:02x}" for value in prefix)
                for name, syscall in [("put_initial_native_batch", "writev"), ("get_full_exact", "readv")]:
                    step = next(row for row in gate["steps"] if row.get("batch_keys") == count and row["name"] == name)
                    ns = (step["start_wall_ns"] + step["end_wall_ns"]) // 2
                    # The fixture deliberately uses the other configured Worker.
                    endpoint = f"127.0.0.1:{31532 - index}"
                    lines.append(f'{stamp(ns)} {syscall}(9<TCP:[127.0.0.1:40000->{endpoint}]>, '
                                 f'[{{iov_base="{escaped}", iov_len={count * 1024}}}], 1) = {count * 1024} <0.000100>\n')
            (traces / "strace.1234").write_text("".join(lines))
    return cell


class TcpAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cached = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.cached.cleanup)
        fixture_suite(Path(cls.cached.name))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(self.cached.name, self.root, dirs_exist_ok=True)
        self.cell = self.root / "r0-datasystem"

    def split_get(self, first_marker="", second_marker="", first_declared=195, returned=1237, gap=", "):
        path = self.cell / "wire-client-0-insert/strace.1234"
        gate = json.loads((self.cell / "client-0-insert.json").read_text())
        key = gate["cases"][0]["key_prefix"] + "k0"
        body = batch_gate.payload(key, 0, 1024)
        first, second = bytes(165) + body[:30], body[30:] + bytes(48)
        encode = lambda value: "".join(f"\\x{byte:02x}" for byte in value)
        lines = path.read_text().splitlines()
        position = next(index for index, line in enumerate(lines) if "readv(" in line)
        prefix = lines[position].split("[{iov_base=", 1)[0]
        vectors = (f'{{iov_base="{encode(first)}"{first_marker}, iov_len={first_declared}}}' + gap
                   + f'{{iov_base="{encode(second)}"{second_marker}, iov_len=3901}}')
        lines[position] = prefix + "[" + vectors + f"], 2) = {returned} <0.000017>"
        path.write_text("\n".join(lines) + "\n")
        return path, position, prefix, first, second

    def first_get(self, result):
        return result["cases"][0]["wire"]["operations"]["get"]

    def test_full_payload_across_complete_successful_iov_prefixes(self):
        self.split_get()
        result = audit.verify(self.root)
        self.assertTrue(result["valid"], result.get("fatal"))
        sample = self.first_get(result)["payload_sample"]
        self.assertEqual(sample["iov_indices"], [0, 1])
        self.assertEqual(sample["syscall_stream_byte_offset"], 165)
        self.assertEqual([part["bytes"] for part in sample["sample_iov_fragments"]], [30, 994])
        self.assertEqual(sample["successful_syscall_bytes"], 1237)

    def test_iov_truncation_on_either_side_is_a_join_barrier(self):
        original = (self.cell / "wire-client-0-insert/strace.1234").read_text()
        for markers in [("...", ""), ("", "...")]:
            (self.cell / "wire-client-0-insert/strace.1234").write_text(original)
            self.split_get(*markers)
            result = audit.verify(self.root)
            self.assertFalse(self.first_get(result)["full_payload_observed"])
            self.assertFalse(result["valid"])

    def test_unprinted_successful_gap_cannot_be_removed_when_joining(self):
        self.split_get(first_declared=196)
        result = audit.verify(self.root)
        self.assertFalse(self.first_get(result)["full_payload_observed"])

    def test_printed_but_unsuccessful_last_payload_byte_cannot_be_used(self):
        self.split_get(returned=1188)
        result = audit.verify(self.root)
        self.assertGreaterEqual(self.first_get(result)["successful_bytes"], 1024)
        self.assertFalse(self.first_get(result)["full_payload_observed"])

    def test_omitted_vector_or_unknown_pointer_is_a_join_barrier(self):
        original = (self.cell / "wire-client-0-insert/strace.1234").read_text()
        for gap in [", ..., ", ", {iov_base=0x1234, iov_len=0}, "]:
            (self.cell / "wire-client-0-insert/strace.1234").write_text(original)
            self.split_get(gap=gap)
            result = audit.verify(self.root)
            self.assertFalse(self.first_get(result)["full_payload_observed"])

    def test_payload_halves_in_different_syscalls_are_not_joined(self):
        path, position, prefix, first, second = self.split_get()
        encode = lambda value: "".join(f"\\x{byte:02x}" for byte in value)
        lines = path.read_text().splitlines()
        lines[position] = (prefix + f'[{{iov_base="{encode(first)}", iov_len=195}}], 1) = 195 <0.000017>\n'
                           + prefix + f'[{{iov_base="{encode(second)}", iov_len=3901}}], 1) = 1042 <0.000017>')
        path.write_text("\n".join(lines) + "\n")
        result = audit.verify(self.root)
        self.assertEqual(self.first_get(result)["successful_bytes"], 1237)
        self.assertFalse(self.first_get(result)["full_payload_observed"])

    def test_split_fragments_from_another_payload_version_cannot_pass(self):
        path, position, _, _, _ = self.split_get()
        gate = json.loads((self.cell / "client-0-insert.json").read_text())
        key = gate["cases"][0]["key_prefix"] + "k0"
        old = batch_gate.payload(key, 0, 1024)[30:]
        new = batch_gate.payload(key, 1, 1024)[30:]
        encode = lambda value: "".join(f"\\x{byte:02x}" for byte in value)
        lines = path.read_text().splitlines()
        lines[position] = lines[position].replace(encode(old), encode(new))
        path.write_text("\n".join(lines) + "\n")
        result = audit.verify(self.root)
        self.assertFalse(self.first_get(result)["full_payload_observed"])

    def test_all_endpoint_mode_batch_combinations_pass_with_compact_anchors(self):
        result = audit.verify(self.root)
        self.assertTrue(result["valid"], result.get("fatal"))
        self.assertEqual(len(result["cases"]), 16)
        self.assertEqual({(case["client_index"], case["write_mode"], case["batch_keys"]) for case in result["cases"]},
                         {(index, mode, count) for index in [0, 1] for mode in ["insert", "upsert"] for count in audit.BATCHES})
        self.assertEqual(len(result["inputs"]), 12)
        for case in result["cases"]:
            for observed in case["wire"]["operations"].values():
                self.assertEqual(observed["successful_bytes"], case["batch_keys"] * 1024)
                self.assertTrue(observed["payload_prefix_observed"])
                self.assertTrue(observed["full_payload_observed"])
                sample = observed["payload_sample"]
                self.assertEqual(sample["sample_key_index"], 0)
                self.assertEqual(sample["sample_version"], 0)
                self.assertEqual(sample["sample_size"], 1024)
                self.assertNotIn("text", observed["anchors"][0])
                self.assertFalse(Path(observed["anchors"][0]["path"]).is_absolute())
        json.dumps(result, allow_nan=False)

    def test_large_metadata_byte_count_without_payload_prefix_is_rejected(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        text = path.read_text()
        import re
        path.write_text(re.sub(r'iov_base="[^"]+"', 'iov_base="metadata-only"', text))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        failed = [case for case in result["cases"] if not case["valid"]]
        self.assertEqual(len(failed), 4)
        self.assertTrue(all(any("prefix" in error for error in case["errors"]) for case in failed))

    def test_payload_prefix_without_full_directional_volume_is_rejected(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        import re
        path.write_text(re.sub(r'\) = \d+ <', ') = 1 <', path.read_text()))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        self.assertTrue(any("insufficient" in error for case in result["cases"] for error in case["errors"]))

    def test_only_32_byte_prefix_cannot_pass_even_with_large_tcp_count(self):
        import re
        path = self.cell / "wire-client-0-insert/strace.1234"
        path.write_text(re.sub(r'iov_base="((?:\\x[0-9a-f]{2})+)"',
                              lambda match: 'iov_base="' + match[1][:128] + '"', path.read_text()))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        put = result["cases"][0]["wire"]["operations"]["put"]
        self.assertTrue(put["payload_prefix_observed"])
        self.assertFalse(put["full_payload_observed"])

    def test_nonzero_same_batch_key_with_full_payload_is_valid_and_named(self):
        import hashlib,re
        path = self.cell / "wire-client-0-insert/strace.1234"
        gate = json.loads((self.cell / "client-0-insert.json").read_text())
        case = next(case for case in gate["cases"] if case["batch_keys"] == 4096)
        old = batch_gate.payload(case["key_prefix"] + "k0", 0, 1024)
        key = case["key_prefix"] + "k8"
        body = batch_gate.payload(key, 0, 1024)
        encode = lambda data: "".join(f"\\x{value:02x}" for value in data)
        path.write_text(path.read_text().replace(encode(old), encode(body)))
        result = audit.verify(self.root)
        self.assertTrue(result["valid"], result.get("fatal"))
        current = next(case for case in result["cases"] if case["client_index"] == 0 and case["write_mode"] == "insert" and case["batch_keys"] == 4096)
        for observed in current["wire"]["operations"].values():
            self.assertFalse(observed["legacy_first_key_prefix_observed"])
            sample = observed["payload_sample"]
            self.assertEqual(sample["sample_key"], key)
            self.assertEqual(sample["sample_key_index"], 8)
            self.assertEqual(sample["sample_binary_sha256"], hashlib.sha256(body).hexdigest())

    def test_another_namespace_or_version_cannot_supply_payload_evidence(self):
        import re
        path = self.cell / "wire-client-0-insert/strace.1234"
        original = path.read_text()
        gate = json.loads((self.cell / "client-0-insert.json").read_text())
        key = gate["cases"][0]["key_prefix"] + "k0"
        for body in [batch_gate.payload("another-namespace-k0", 0, 1024), batch_gate.payload(key, 1, 1024)]:
            encoded = "".join(f"\\x{value:02x}" for value in body)
            path.write_text(re.sub(r'iov_base="(?:\\x[0-9a-f]{2})+"', lambda match: 'iov_base="' + encoded + '"', original, count=1))
            result = audit.verify(self.root)
            self.assertFalse(result["valid"])
            self.assertFalse(result["cases"][0]["wire"]["operations"]["put"]["full_payload_observed"])

    def test_full_printed_iov_beyond_successful_write_range_cannot_pass(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        lines = path.read_text().splitlines()
        first = lines[0]
        old = first[first.index("[{iov_base="):first.index("}], 1") + 1]
        metadata = "".join("\\x00" for _ in range(512))
        new = '[{iov_base="' + metadata + '", iov_len=512}, ' + old[1:]
        lines[0] = first.replace(old, new).replace("}], 1)", "}], 2)")
        path.write_text("\n".join(lines) + "\n")
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        put = result["cases"][0]["wire"]["operations"]["put"]
        self.assertGreaterEqual(put["successful_bytes"], 1024)
        self.assertTrue(put["payload_prefix_observed"])
        self.assertFalse(put["full_payload_observed"])

    def test_unapproved_worker_endpoint_cannot_supply_tcp_proof(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        path.write_text(path.read_text().replace("->127.0.0.1:31532", "->127.0.0.1:9999"))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        self.assertEqual(result["cases"][0]["wire"]["operations"]["put"]["successful_bytes"], 0)

    def test_payload_on_unix_socket_cannot_pass(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        path.write_text(path.read_text().replace("<TCP:", "<UNIX:"))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])

    def test_reverse_direction_cannot_be_counted_as_put(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        path.write_text(path.read_text().replace("writev(", "readv("))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        self.assertEqual(result["cases"][0]["wire"]["operations"]["put"]["successful_bytes"], 0)

    def test_resumed_tcp_read_is_reconstructed_from_per_thread_trace(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        lines = path.read_text().splitlines()
        position = next(i for i, line in enumerate(lines) if "readv(" in line)
        timestamp, tail = lines[position].split(" ", 1)
        descriptor, response = tail.split(">, ", 1)
        lines[position] = timestamp + " " + descriptor + ">, <unfinished ...>\n" + timestamp + " <... readv resumed>" + response
        path.write_text("\n".join(lines) + "\n")
        result = audit.verify(self.root)
        self.assertTrue(result["valid"], result.get("fatal"))
        anchor = result["cases"][0]["wire"]["operations"]["get"]["anchors"][0]
        self.assertLess(anchor["first_line"], anchor["line"])

    def test_outside_api_window_is_not_counted(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        path.write_text("\n".join("1.000000 " + row.split(" ", 1)[1] for row in path.read_text().splitlines()) + "\n")
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        self.assertEqual(result["cases"][0]["wire"]["operations"]["get"]["successful_bytes"], 0)

    def test_missing_or_single_f_pid_prefix_trace_cannot_pass_as_ff_format(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        path.write_text("\n".join("[pid 1234] " + line for line in path.read_text().splitlines()) + "\n")
        self.assertFalse(audit.verify(self.root)["valid"])
        path.unlink()
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        self.assertIn("No per-thread", result["fatal"]["message"])

    def test_both_flag_and_live_argv_tcp_configuration_are_required(self):
        path = self.cell / "tcp-configuration.json"
        original = json.loads(path.read_text())
        for field in ["ipc_through_shared_memory", "enable_rdma", "enable_urma"]:
            config = json.loads(json.dumps(original))
            config["workers"][0]["flags"][field] = "true"
            path.write_text(json.dumps(config))
            result = audit.verify(self.root)
            self.assertFalse(result["valid"])
            self.assertIn("configuration mismatch", result["fatal"]["message"])
        config = json.loads(json.dumps(original))
        config["workers"][0]["live_argv"].append("--enable_rdma=true")
        path.write_text(json.dumps(config))
        self.assertFalse(audit.verify(self.root)["valid"])

    def test_missing_step_or_payload_derivation_mismatch_fails(self):
        path = self.cell / "client-0-insert.json"
        original = json.loads(path.read_text())
        bad = json.loads(json.dumps(original))
        bad["steps"] = [step for step in bad["steps"] if not (step.get("batch_keys") == 1 and step["name"] == "get_full_exact")]
        path.write_text(json.dumps(bad))
        self.assertFalse(audit.verify(self.root)["valid"])
        bad = json.loads(json.dumps(original))
        next(step for step in bad["steps"] if step.get("batch_keys") == 1 and step["name"] == "get_full_exact")["result"]["observed_values_sha256"] = "0" * 64
        path.write_text(json.dumps(bad))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        self.assertIn("derivation", result["fatal"]["message"])

    def test_duplicate_batch_identity_and_wrong_source_are_rejected(self):
        path = self.cell / "client-0-insert.json"
        original = json.loads(path.read_text())
        bad = json.loads(json.dumps(original))
        bad["cases"][1] = bad["cases"][0]
        path.write_text(json.dumps(bad))
        self.assertFalse(audit.verify(self.root)["valid"])
        bad = json.loads(json.dumps(original))
        bad["metadata"]["source_sha"] = "0" * 40
        path.write_text(json.dumps(bad))
        self.assertFalse(audit.verify(self.root)["valid"])

    def test_evidence_symlinks_are_rejected(self):
        path = self.cell / "wire-client-0-insert/strace.1234"
        sibling = path.with_name("saved")
        path.rename(sibling)
        path.symlink_to(sibling)
        self.assertFalse(audit.verify(self.root)["valid"])

    def test_copied_configuration_bytes_are_bound_to_live_snapshot_digests(self):
        path = self.cell / "worker-0.flags"
        path.write_text(path.read_text().replace("--enable_rdma=false", "--enable_rdma=true"))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        self.assertIn("flags SHA", result["fatal"]["message"])

    def test_copied_process_identity_mismatch_is_rejected(self):
        path = self.cell / "processes.json"
        manifest = json.loads(path.read_text())
        manifest["processes"][0]["start_ticks"] = "11"
        path.write_text(json.dumps(manifest))
        config_path = self.cell / "tcp-configuration.json"
        config = json.loads(config_path.read_text())
        config["processes_sha256"] = audit.file_record(path)["sha256"]
        config_path.write_text(json.dumps(config))
        result = audit.verify(self.root)
        self.assertFalse(result["valid"])
        self.assertIn("process identity", result["fatal"]["message"])

    def test_cli_writes_once_without_contacting_products(self):
        out = self.root / "tcp-audit.json"
        self.assertEqual(audit.main(["--suite", str(self.root), "--out", str(out)]), 0)
        original = out.read_bytes()
        with self.assertRaises(SystemExit) as caught:
            audit.main(["--suite", str(self.root), "--out", str(out)])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(out.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
