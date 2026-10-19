#!/usr/bin/env python3
"""Negative cleanup guard: mismatched process identity must survive teardown."""
import argparse
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import runtime


parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
owned = subprocess.Popen(["sleep", "30"])
protected = subprocess.Popen(["sleep", "30"])
try:
    manifest = {"processes": [
        {"pid": protected.pid, "start_ticks": "deliberate-mismatch", "name": "must_survive"},
        {"pid": owned.pid, "start_ticks": runtime.start_ticks(owned.pid), "name": "owned"},
    ]}
    (args.out / "processes.json").write_text(json.dumps(manifest))
    runtime.stop(SimpleNamespace(task_root=str(args.out.parent), run_dir=str(args.out), stop_timeout=0.5))
    owned.wait(timeout=5)
    assert protected.poll() is None, "Mismatched identity process was signalled"
    results = json.loads((args.out / "stop-results.json").read_text())
    assert next(r for r in results if r["pid"] == protected.pid)["result"] == "already_stopped_or_reused"
    (args.out / "safety-result.json").write_text(json.dumps({"passed": True,
        "owned_process_terminated": owned.returncode < 0,
        "mismatched_identity_process_survived": True}, indent=2) + "\n")
    print("Exact-target cleanup safety passed")
finally:
    for process in [owned, protected]:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
