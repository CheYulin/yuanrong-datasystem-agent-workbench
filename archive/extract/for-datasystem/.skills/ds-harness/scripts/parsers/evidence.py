"""Parse harness step logs into structured evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def parse_ctest_log(text: str) -> dict[str, Any]:
    passed = len(re.findall(r"^\s*\d+/\d+\s+Test\s+\S+.*Passed", text, re.M | re.I))
    failed = len(re.findall(r"^\s*\d+/\d+\s+Test\s+\S+.*Failed", text, re.M | re.I))
    skipped = len(re.findall(r"^\s*\d+/\d+\s+Test\s+\S+.*Skipped", text, re.M | re.I))
    failed_names = re.findall(r"^\s*\d+/\d+\s+Test\s+(\S+).*Failed", text, re.M | re.I)
    total = passed + failed + skipped
    rate = round(100.0 * passed / total, 1) if total else None
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "success_rate_percent": rate,
        "failed_tests": failed_names[:20],
    }


def parse_test_results(evidence_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    layers: dict[str, Any] = {}
    for record in records:
        if record.get("id") not in {"smoke", "ut", "st"}:
            continue
        log_path = evidence_dir / f"{record['id']}.log"
        if not log_path.is_file():
            continue
        text = log_path.read_text(encoding="utf-8", errors="replace")
        layers[record["id"]] = {
            **parse_ctest_log(text),
            "elapsed_sec": record.get("elapsed_sec"),
            "status": record.get("status"),
        }
    total_failed = sum(v.get("failed", 0) for v in layers.values())
    return {
        "status": "parsed",
        "layers": layers,
        "total_failed": total_failed,
    }


def build_optimization_hint(records: list[dict[str, Any]]) -> str | None:
    ranked = sorted(records, key=lambda r: r.get("elapsed_sec", 0), reverse=True)
    if not ranked or ranked[0].get("elapsed_sec", 0) < 60:
        return None
    top = ranked[0]
    return (
        f"Longest step '{top['id']}' took {top.get('elapsed_sec')}s; "
        f"consider --skip-build or narrowing ctest filters before rerun."
    )


def compute_acceptance(
    *,
    status: str,
    command_group: str,
    records: list[dict[str, Any]],
    acceptance_metrics: dict[str, Any],
    test_results: dict[str, Any] | None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "overall_status": status,
        "steps_passed": sum(1 for r in records if r.get("status") in {"OK", "DRY_RUN"}),
        "steps_failed": sum(1 for r in records if r.get("status") == "FAIL"),
    }
    max_minutes = acceptance_metrics.get("max_minutes", {})
    step_timing: dict[str, Any] = {}
    for record in records:
        sid = record["id"]
        elapsed_min = round(record.get("elapsed_sec", 0) / 60.0, 2)
        budget = max_minutes.get(sid)
        step_timing[sid] = {
            "elapsed_min": elapsed_min,
            "budget_min": budget,
            "within_budget": budget is None or elapsed_min <= budget,
        }
    metrics["step_timing"] = step_timing
    if test_results and test_results.get("layers"):
        metrics["test_success_rate_percent"] = {
            k: v.get("success_rate_percent") for k, v in test_results["layers"].items()
        }
        metrics["total_test_failures"] = test_results.get("total_failed", 0)

    verdict = "PASS" if status in {"PASS", "DRY_RUN"} else "FAIL"
    over_budget = [k for k, v in step_timing.items() if v.get("within_budget") is False]
    if over_budget and status == "PASS":
        verdict = "WARN"
        metrics["over_budget_steps"] = over_budget

    failed_layer = next((r["id"] for r in records if r.get("status") == "FAIL"), None)
    return {
        "acceptance_verdict": verdict,
        "metrics": metrics,
        "failed_layer": failed_layer,
        "optimization_hint": build_optimization_hint(records),
    }
