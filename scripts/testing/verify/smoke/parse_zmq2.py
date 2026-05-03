#!/usr/bin/env python3
"""Print zmq_* rows from the last metrics_summary JSON line in a worker/client glog."""

import json
import sys
from typing import Optional, Tuple

MARKER = '"event":"metrics_summary"'
# Greedy .* can truncate valid JSON when the regex engine stops early; brace-balance handles long lines.


def extract_metrics_summary_json(line: str) -> Optional[dict]:
    idx = line.find(MARKER)
    if idx < 0:
        return None
    brace = line.rfind("{", 0, idx)
    if brace < 0:
        return None
    depth = 0
    for j in range(brace, len(line)):
        ch = line[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(line[brace : j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        prog = sys.argv[0] if sys.argv else "parse_zmq2.py"
        print(f"Usage: {prog} <path/to/*.INFO.log>", file=sys.stderr)
        print("Reads the log and prints zmq_* metrics from the last metrics_summary JSON line.", file=sys.stderr)
        return 2
    path = argv[0]
    last_ok: Optional[Tuple[str, dict]] = None
    errors = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if MARKER not in line:
                    continue
                data = extract_metrics_summary_json(line)
                if data is None:
                    errors += 1
                    continue
                if data.get("event") == "metrics_summary":
                    last_ok = (path, data)
    except OSError as e:
        print(f"open: {path}: {e}", file=sys.stderr)
        return 1

    if last_ok is None:
        print(f"No parseable '{MARKER}' line in {path} (skipped {errors} malformed line(s))", file=sys.stderr)
        return 1

    _fname, data = last_ok
    metrics = data.get("metrics") or []
    zmq_metrics = [m for m in metrics if str(m.get("name", "")).startswith("zmq_")]
    print("=" * 80)
    print(f"ZMQ Metrics Summary (last metrics_summary in {path})")
    print("=" * 80)
    print("  {:<35} {:>12} {:>12} {:>12}".format("Metric", "Count", "Avg(us)", "Max(us)"))
    print("  " + "-" * 35 + "  " + "-" * 12 + "  " + "-" * 12 + "  " + "-" * 12)
    for m in zmq_metrics:
        t = m.get("total", {})
        if not isinstance(t, dict):
            t = {}
        print(
            "  {:<35} {:>12} {:>12} {:>12}".format(
                m["name"],
                t.get("count", 0),
                t.get("avg_us", 0),
                t.get("max_us", 0),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
