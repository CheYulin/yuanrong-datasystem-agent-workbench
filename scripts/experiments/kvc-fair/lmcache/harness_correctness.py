"""Short correctness runs only; output QPS is deliberately not a ranked result."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["fake", "real"], required=True)
    parser.add_argument("--concurrencies", nargs="+", type=int, default=[2, 8])
    args = parser.parse_args()
    output = ROOT / ".evidence" / (f"harness-correctness-{args.kind}-{time.time_ns()}")
    output.mkdir(parents=True)
    config = output / "config.json"
    config.write_text("{}")
    adapter = ROOT / ("fake_mechanics_backend.py" if args.kind == "fake" else "adapter_lmcache.py")
    rows = []
    cases = [(1024, 2)] if args.kind == "fake" else [(s, c) for c in args.concurrencies for s in (1024, 65536, 1048576)]
    for size, concurrency in cases:
        run_id = f"correctness-{uuid.uuid4().hex}"
        for op in ("put", "get", "exist_hit", "exist_miss"):
            result_path = output / f"s{size}-c{concurrency}-{op}.json"
            command = [sys.executable, str(ROOT.parent / "benchmark.py"), "--adapter", str(adapter), "--config", str(config), "--op", op, "--size", str(size), "--concurrency", str(concurrency), "--duration", "0.05" if args.kind == "fake" else "2", "--warmup", "0", "--total-bytes", str(max(1 << 20, size * concurrency * 4)), "--preload-bytes", str(1 << 20), "--run-id", run_id, "--out", str(result_path)]
            if args.kind == "real" and op.startswith("exist"):
                command.append("--skip-preload")
            with result_path.with_suffix(".log").open("w") as log:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
            result = json.loads(result_path.read_text())
            assert completed.returncode == 0 and result["valid"], result
            assert result["success"] == sum(c["success"] for c in result["clients"])
            assert result["success"] == sum(result["histogram"].values())
            assert result["failures"] == 0
            for q in ("p50", "p95", "p99"):
                assert result[q]["lower_us"] <= result[q]["upper_us"]
            validations = sum(c["validations"] for c in result["clients"]) + sum(c["post_validations"] for c in result.get("post_validation", []))
            rows.append({"size": size, "concurrency": concurrency, "op": op, "valid": True, "success": result["success"], "failures": result["failures"], "validations": validations, "evidence": str(result_path)})
    if args.kind == "fake":
        config.write_text(json.dumps({"fault": "get_wrong_bytes"}))
        result_path = output / "intentional-corruption.json"
        command = [sys.executable, str(ROOT.parent / "benchmark.py"), "--adapter", str(adapter), "--config", str(config), "--op", "get", "--size", "1024", "--concurrency", "2", "--duration", "0.05", "--warmup", "0", "--run-id", "intentional-corruption", "--out", str(result_path)]
        with result_path.with_suffix(".log").open("w") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=30)
        result = json.loads(result_path.read_text())
        assert completed.returncode != 0 and not result["valid"] and "mismatch" in result["fatal"]
    summary = {"purpose": "harness correctness only, not performance comparison", "passed": True, "cases": rows}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"passed": True, "cases": len(rows), "output": str(output)}))


if __name__ == "__main__":
    main()
