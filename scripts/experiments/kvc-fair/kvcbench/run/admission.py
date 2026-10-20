"""Read-only native gate admission shared by runner and workflow."""
import json
import os
from pathlib import Path
from kvcbench.platform.locks import open_path
from kvcbench.run.plan import BATCHES
from kvcbench.core.case_validation import measured_case


def verify_resource_lock(root, descriptor, cgroup):
    stage = json.loads((root / "batch-stage.json").read_text())
    observed = os.fstat(descriptor)
    if (stage.get("shared_suite_lock_inode") != observed.st_ino
            or stage.get("shared_suite_lock_device", observed.st_dev) != observed.st_dev
            or stage.get("cgroup", str(cgroup)) != str(cgroup)):
        raise ValueError("Runtime resource domain differs from the accepted deployment")
    if stage.get("resource_lock_path"):
        with open_path(Path(stage["resource_lock_path"])) as shared:
            current = os.fstat(shared.fileno())
            if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
                raise ValueError("Runtime resource lock was replaced")


def verify_gate(root, gate_name, products, expected_inputs):
    root = root / gate_name
    gate = json.loads((root / "summary.json").read_text())
    if (not gate.get("valid") or not gate.get("ended") or gate.get("failed")
            or set(gate.get("completed", [])) != {"r0-" + name for name in products}):
        raise RuntimeError("A complete successful native batch gate is required")
    for filename in ["batch-stage.json", "products.json"]:
        if (root / filename).read_bytes() != (expected_inputs / filename).read_bytes():
            raise RuntimeError("Gate execution inputs differ: " + filename)
    for name in products:
        for index in [0, 1]:
            for mode in ["insert"] if name == "lmcache" else ["insert", "upsert"]:
                raw = json.loads((root / ("r0-" + name) / f"client-{index}-{mode}.json").read_text())
                if (not raw.get("valid") or raw.get("client_index") != index
                        or raw.get("value_size") != 1024 or raw.get("batch_sizes") != BATCHES
                        or not all(case.get("valid") for case in raw.get("cases", []))
                        or [case.get("batch_keys") for case in raw.get("cases", [])] != BATCHES):
                    raise RuntimeError("Gate endpoint/batch evidence incomplete")


def verify_matrix(suite, rows, products, args):
    issues, failed, counts = [], [], {"valid": 0, "failed": 0, "not_supported": 0, "invalid": 0}
    for row in rows:
        cell = suite / f"r{row['repeat']}-{row['product']}"
        for case in row["operations"]:
            path = cell / f"b{case['batch']}-{case['operation']}.json"
            identity = cell.name + "/" + path.stem
            try:
                raw = json.loads(path.read_text())
                if not case["supported"]:
                    expected = {"product": row["product"], "repeat": row["repeat"], "batch": case["batch"],
                                "operation": case["operation"], "status": "not_supported"}
                    if (any(raw.get(key) != value for key, value in expected.items())
                            or not isinstance(raw.get("reason"), str) or not raw["reason"]):
                        raise ValueError("Unsupported-case identity differs from the fixed plan")
                    counts["not_supported"] += 1
                    continue
                epoch = next(epoch for epoch in row["epochs"] if epoch["id"] == case["epoch"])
                product = products[row["product"]]
                checked = measured_case(raw, {"identity": identity, "product": row["product"], "repeat": row["repeat"],
                    "batch": case["batch"], "operation": case["operation"], "supported": True},
                    {"isolated_epochs": True, "continue_readonly_errors": args.continue_readonly_errors,
                     "protocol": {"duration": args.duration, "warmup": args.warmup, "put_keys": args.put_keys},
                     "adapter": product["adapter"], "config": product["config"],
                     "run_id": suite.name + "-r" + str(row["repeat"]),
                     "out": str(Path(getattr(args, 'out', suite)) / cell.name / path.name),
                     "skip_preload": epoch["operations"][0] != case})
                counts[checked["status"]] += 1
                if not checked["evidence_verified"]:
                    issues.append({"identity": identity, "issues": checked["issues"]})
                if checked["status"] == "failed":
                    failed.append(identity)
            except (OSError, ValueError, TypeError, KeyError, StopIteration) as error:
                issues.append({"identity": identity, "error": str(error)})
                counts["invalid"] += 1
    return {"evidence_verified": not issues, "issues": issues, "native_failures": failed, "counts": counts}
