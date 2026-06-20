#!/usr/bin/env python3
"""Unified workbench harness for build/dev/daily/perf workflows.

The harness is intentionally conservative: profiles define the workflow, dry-run
validates routing without touching remotes, and every run writes structured evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


WORKBENCH = Path(__file__).resolve().parents[2]
PROFILES_PATH = WORKBENCH / "scripts" / "harness" / "profiles.yaml"
DEFAULT_PROFILE_BY_COMMAND = {
    "build": "build.quick",
    "dev": "dev.quick",
    "daily": "daily.full",
    "perf": "perf.hotspot",
    "bench": "bench.dsbench.smoke",
    "format": "dev.quick",
    "sync": "dev.quick",
    "doctor": "dev.quick",
}


def load_profiles() -> dict[str, Any]:
    with PROFILES_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "profiles" not in data:
        raise SystemExit(f"invalid profiles file: {PROFILES_PATH}")
    return data


def resolve_datasystem_root() -> Path:
    for key in ("DATASYSTEM_ROOT", "YUANRONG_DATASYSTEM_ROOT"):
        raw = os.environ.get(key)
        if raw:
            return Path(raw).resolve()

    sibling = WORKBENCH.parent / "yuanrong-datasystem"
    if sibling.exists():
        return sibling.resolve()
    return WORKBENCH.parent


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(WORKBENCH), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def pick_backend_value(value: Any, backend: str) -> Any:
    if isinstance(value, dict):
        return value.get(backend)
    return value


def render_command(command: str, *, backend: str, node: str, profile: str, datasystem_root: Path) -> str:
    return command.format(
        backend=backend,
        node=node,
        profile=profile,
        workbench_root=str(WORKBENCH),
        datasystem_root=str(datasystem_root),
    )


def profile_steps(profile: dict[str, Any], *, backend: str, node: str, profile_name: str) -> list[dict[str, Any]]:
    datasystem_root = resolve_datasystem_root()
    steps: list[dict[str, Any]] = []
    for raw in profile.get("steps", []):
        command = pick_backend_value(raw.get("command"), backend)
        if not command:
            continue
        uses = pick_backend_value(raw.get("uses"), backend)
        steps.append(
            {
                "id": raw["id"],
                "label": raw.get("label", raw["id"]),
                "uses": uses,
                "command": render_command(
                    str(command),
                    backend=backend,
                    node=node,
                    profile=profile_name,
                    datasystem_root=datasystem_root,
                ),
            }
        )
    return steps


def ensure_evidence_dir(config: dict[str, Any], profile_name: str, evidence_dir: str | None) -> Path:
    if evidence_dir:
        out = Path(evidence_dir)
        if not out.is_absolute():
            out = WORKBENCH / out
    else:
        root = WORKBENCH / config.get("defaults", {}).get("evidence_root", "results/harness")
        out = root / f"{stamp()}-{profile_name}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(WORKBENCH))
    except ValueError:
        return str(path)


def write_steps(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_build_timing(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["step", "elapsed_sec", "status", "long_tail_rank"])
        writer.writeheader()
        ranked = sorted(records, key=lambda r: r.get("elapsed_sec", 0), reverse=True)
        rank_by_id = {r["id"]: i + 1 for i, r in enumerate(ranked)}
        for record in records:
            writer.writerow(
                {
                    "step": record["id"],
                    "elapsed_sec": record.get("elapsed_sec", 0),
                    "status": record.get("status", "UNKNOWN"),
                    "long_tail_rank": rank_by_id.get(record["id"], ""),
                }
            )


def write_placeholder_evidence(evidence_dir: Path, profile_name: str, profile: dict[str, Any], records: list[dict[str, Any]]) -> None:
    requested = set(profile.get("evidence", []))
    if "build_timing.csv" in requested:
        write_build_timing(evidence_dir / "build_timing.csv", records)
    if "test_results.json" in requested:
        write_json(
            evidence_dir / "test_results.json",
            {
                "profile": profile_name,
                "status": "pending-real-parser",
                "tests": [],
                "failures": [r for r in records if r.get("status") == "FAIL"],
                "long_tail": sorted(records, key=lambda r: r.get("elapsed_sec", 0), reverse=True)[:10],
            },
        )
    if "coverage.json" in requested:
        write_json(
            evidence_dir / "coverage.json",
            {
                "profile": profile_name,
                "status": "pending-real-parser",
                "line_percent": None,
                "function_percent": None,
                "branch_percent": None,
                "thresholds": profile.get("thresholds", {}).get("coverage", {}),
            },
        )
    if "bench_results.json" in requested:
        write_json(
            evidence_dir / "bench_results.json",
            {
                "profile": profile_name,
                "status": "pending-real-parser",
                "tool": profile.get("skill", "bench"),
                "steps": [r["id"] for r in records],
                "failures": [r for r in records if r.get("status") == "FAIL"],
            },
        )
    if "perf_hotspots.md" in requested:
        (evidence_dir / "perf_hotspots.md").write_text(
            "\n".join(
                [
                    "# Performance Hotspots",
                    "",
                    "## Evidence",
                    "Generated from harness step logs. Replace placeholders with parsed perf/bpftrace/metrics rows when available.",
                    "",
                    "## Judgment",
                    "Rank hotspots by measured elapsed time, regression size, or sampled cost.",
                    "",
                    "## Suggestion",
                    "Start with the highest-cost step or metric and rerun the matching perf profile after changes.",
                    "",
                    "## Recheck",
                    f"`python3 scripts/harness/ds_harness.py perf --profile {profile_name}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    if "bench_results.json" in requested:
        write_json(
            evidence_dir / "bench_results.json",
            {
                "profile": profile_name,
                "status": "pending-real-parser",
                "benchmarks": [],
                "failures": [r for r in records if r.get("status") == "FAIL"],
            },
        )


def run_step(step: dict[str, Any], evidence_dir: Path, dry_run: bool) -> dict[str, Any]:
    log_path = evidence_dir / f"{step['id']}.log"
    started = time.time()
    record = {
        "id": step["id"],
        "label": step["label"],
        "uses": step.get("uses"),
        "command": step["command"],
        "log": display_path(log_path),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        log_path.write_text(f"DRY RUN: {step['command']}\n", encoding="utf-8")
        record.update({"status": "DRY_RUN", "exit_code": 0, "elapsed_sec": 0})
        return record

    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            step["command"],
            cwd=WORKBENCH,
            shell=True,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    elapsed = round(time.time() - started, 3)
    record.update(
        {
            "status": "OK" if proc.returncode == 0 else "FAIL",
            "exit_code": proc.returncode,
            "elapsed_sec": elapsed,
        }
    )
    return record


def run_profile(args: argparse.Namespace) -> dict[str, Any]:
    config = load_profiles()
    defaults = config.get("defaults", {})
    profile_name = args.profile or DEFAULT_PROFILE_BY_COMMAND[args.command_group]
    profiles = config["profiles"]
    if profile_name not in profiles:
        raise SystemExit(f"unknown profile {profile_name!r}; available: {', '.join(sorted(profiles))}")

    profile = profiles[profile_name]
    if profile.get("command_group") != args.command_group and args.command_group not in {"format", "sync", "doctor"}:
        raise SystemExit(f"profile {profile_name!r} belongs to {profile.get('command_group')}, not {args.command_group}")

    backend = args.backend or defaults.get("backend", "cmake")
    node = args.node or defaults.get("node", "tiantiyun-80c128g")
    evidence_dir = ensure_evidence_dir(config, profile_name, args.evidence_dir)
    steps = profile_steps(profile, backend=backend, node=node, profile_name=profile_name)

    if args.command_group == "format":
        steps = [s for s in steps if s["id"] == "lint-line-width"]
    elif args.command_group == "sync":
        steps = [
            {
                "id": "sync-workspace",
                "label": "Sync workspace to tiantiyun",
                "uses": "scripts/harness/sync_workspace_to_tiantiyun.sh",
                "command": f"bash scripts/harness/sync_workspace_to_tiantiyun.sh --node {node}",
            }
        ]
    elif args.command_group == "doctor":
        steps = []

    records = []
    effective_dry_run = args.dry_run or args.command_group == "doctor"
    for step in steps:
        record = run_step(step, evidence_dir, effective_dry_run)
        records.append(record)
        if profile.get("stop_on_failure") and not effective_dry_run and record.get("exit_code", 0) != 0:
            break
    status = "PASS"
    if args.dry_run:
        status = "DRY_RUN"
    if any(r.get("exit_code", 0) != 0 for r in records):
        status = "FAIL"

    summary = {
        "status": status,
        "profile": profile_name,
        "skill": profile.get("skill"),
        "command_group": args.command_group,
        "backend": backend,
        "node": node,
        "git_sha": git_sha(),
        "evidence_dir": display_path(evidence_dir),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps_total": len(records),
        "steps_failed": sum(1 for r in records if r.get("status") == "FAIL"),
        "acceptance_metrics": config.get("acceptance_metrics", {}).get(
            profile.get("acceptance_group") or profile.get("command_group"), {}
        ),
        "steps": records,
    }

    write_steps(evidence_dir / "steps.jsonl", records)
    write_placeholder_evidence(evidence_dir, profile_name, profile, records)
    write_json(evidence_dir / "summary.json", summary)
    return summary


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=["cmake", "bazel"], help="Build backend.")
    parser.add_argument("--node", help="Node name from scripts/config/nodes.yaml.")
    parser.add_argument("--profile", help="Profile name from scripts/harness/profiles.yaml.")
    parser.add_argument("--evidence-dir", help="Override evidence output directory.")
    parser.add_argument("--dry-run", action="store_true", help="Plan commands and write evidence without executing them.")
    parser.add_argument("--json", action="store_true", help="Print summary JSON to stdout.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command_group", required=True)
    for name in ("build", "dev", "daily", "perf", "bench", "format", "sync", "doctor"):
        add_common_flags(sub.add_parser(name))
    parser.add_argument("--list-profiles", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = run_profile(args)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"{summary['status']} {summary['profile']} -> {summary['evidence_dir']}")
    return 1 if summary["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
