"""Independent native byte/EXIST correctness probe, outside performance timing."""
import argparse
import json
from pathlib import Path
import time
import traceback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    from kvcbench.run.workers.scalar import load_adapter
    config = json.loads(Path(args.config).read_text())
    config.update(value_size=1024, client_index=0, write_mode="insert")
    adapter = None
    result = {"valid": False, "started_wall_ns": time.time_ns(), "scope": "independent scalar native batch correctness; no performance samples"}
    try:
        adapter = load_adapter(args.adapter, config)
        key = "lab-qualification-" + str(time.time_ns())
        value = bytes(range(256)) * 4
        intervals = []
        def invoke(name, callback):
            before, mono = time.time_ns(), time.monotonic_ns()
            answer = callback()
            intervals.append({"operation": name, "start_wall_ns": before, "end_wall_ns": time.time_ns(),
                              "start_mono_ns": mono, "end_mono_ns": time.monotonic_ns()})
            return answer
        invoke("put", lambda: adapter.put_batch([key], [value]))
        if invoke("get", lambda: adapter.get_batch([key])) != [value]:
            raise AssertionError("GET exact bytes mismatch")
        if invoke("exist_hit", lambda: adapter.exists_batch([key])) != [True] or invoke("exist_miss", lambda: adapter.exists_batch([key + "-absent"])) != [False]:
            raise AssertionError("EXIST hit/miss mismatch")
        result.update(valid=True, operations=["put", "get", "exist_hit", "exist_miss"],
                      adapter_metadata=adapter.metadata, key=key, intervals=intervals)
    except BaseException:
        result["error"] = traceback.format_exc()
    finally:
        if adapter is not None:
            adapter.close()
        result["ended_wall_ns"] = time.time_ns()
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
