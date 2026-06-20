#!/usr/bin/env python3
"""Run per-skill verification from profiles.yaml skill_verify table."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

WORKBENCH = Path(__file__).resolve().parents[2]
PROFILES_PATH = WORKBENCH / "scripts" / "harness" / "profiles.yaml"
HARNESS = WORKBENCH / "scripts" / "harness" / "ds_harness.py"
CANONICAL_SKILLS = (
    "wb-build",
    "wb-dev",
    "wb-daily",
    "wb-perf",
    "wb-docs",
    "wb-html-publish",
)


def load_config() -> dict[str, Any]:
    with PROFILES_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "skill_verify" not in data:
        raise SystemExit(f"missing skill_verify in {PROFILES_PATH}")
    return data


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(WORKBENCH), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def hostname_short() -> str:
    return socket.gethostname().split(".")[0]


def run_cmd(cmd: list[str], *, cwd: Path = WORKBENCH, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def run_step(step: dict[str, Any], *, skill: str, evidence_dir: Path, dry_run: bool) -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    record: dict[str, Any] = {"step": step, "started_at": started}

    if "harness" in step:
        group = step["harness"]
        profile = step["profile"]
        effective_dry = dry_run or bool(step.get("dry_run"))
        cmd = [
            "python3",
            str(HARNESS),
            group,
            "--profile",
            profile,
            "--json",
            "--evidence-dir",
            str(evidence_dir / "harness"),
        ]
        if effective_dry:
            cmd.append("--dry-run")
        code, out = run_cmd(cmd)
        record.update({"kind": "harness", "command": cmd, "exit_code": code, "output_tail": out[-2000:]})
        try:
            record["summary"] = json.loads(out)
        except json.JSONDecodeError:
            record["summary"] = None
        record["status"] = "PASS" if code == 0 else "FAIL"
        return record

    if step.get("check") == "file":
        path = WORKBENCH / step["path"]
        ok = path.is_file()
        record.update({"kind": "file", "path": str(path), "status": "PASS" if ok else "FAIL"})
        return record

    if step.get("check") == "help":
        cmd = step["command"].split()
        code, out = run_cmd(cmd)
        status = "PASS" if code == 0 else ("WARN" if step.get("allow_fail") else "FAIL")
        record.update({"kind": "help", "command": cmd, "exit_code": code, "status": status})
        return record

    if step.get("check") == "script":
        script = WORKBENCH / step["path"]
        args = step.get("args", [])
        cmd = ["bash", str(script), *args]
        code, out = run_cmd(cmd)
        status = "PASS" if code == 0 else ("WARN" if step.get("allow_fail") else "FAIL")
        record.update(
            {
                "kind": "script",
                "command": cmd,
                "exit_code": code,
                "output_tail": out[-1500:],
                "status": status,
            }
        )
        return record

    record.update({"kind": "unknown", "status": "FAIL"})
    return record


def verify_skill(
    skill: str,
    spec: dict[str, Any],
    *,
    dry_run: bool,
    runs_root: Path,
) -> dict[str, Any]:
    node = spec["node"]
    out_dir = runs_root / f"{skill}_{stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    env = {"VERIFY_ON_NODE": node}
    os.environ.update(env)

    records = []
    for step in spec.get("steps", []):
        records.append(run_step(step, skill=skill, evidence_dir=out_dir, dry_run=dry_run))

    statuses = [r["status"] for r in records]
    if "FAIL" in statuses:
        verdict = "FAIL"
    elif "WARN" in statuses:
        verdict = "WARN"
    else:
        verdict = "PASS"

    summary = {
        "skill": skill,
        "node": node,
        "verdict": verdict,
        "dry_run": dry_run,
        "git_sha": git_sha(),
        "hostname": hostname_short(),
        "evidence_dir": str(out_dir.relative_to(WORKBENCH)),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "steps": records,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def sync_for_node(node: str) -> None:
    if node == "xqyun-32c32g":
        script = WORKBENCH / "scripts/development/sync/sync_to_xqyun.sh"
    else:
        script = WORKBENCH / "scripts/harness/sync_workspace_to_tiantiyun.sh"
    if not script.is_file():
        return
    subprocess.run(["bash", str(script)], cwd=WORKBENCH, check=True)


def node_ssh_target(node: str) -> tuple[str, str]:
    with (WORKBENCH / "scripts/config/nodes.yaml").open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    entry = cfg["nodes"][node]
    return f"{entry['ssh_user']}@{entry['ssh_host']}", entry["workspace_root"]


def ssh_verify(skill: str, *, sync: bool, dry_run: bool) -> int:
    config = load_config()
    spec = config["skill_verify"][skill]
    node = spec["node"]
    if sync:
        sync_for_node(node)

    _, ws = node_ssh_target(node)
    remote_wb = f"{ws}/yuanrong-datasystem-agent-workbench"
    user_host, _ = node_ssh_target(node)
    dry_flag = " --dry-run" if dry_run else ""
    cmd = (
        f"set -euo pipefail; "
        f"WB=\"{remote_wb}\"; "
        f"[[ -d \"$WB\" ]] || WB=\"$HOME/workspace/git-repos/yuanrong-datasystem-agent-workbench\"; "
        f"cd \"$WB\"; "
        f"VERIFY_ON_NODE='{node}' python3 \"$WB/scripts/harness/verify_skill.py\" "
        f"--skill {skill} --local{dry_flag}"
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", user_host, "bash", "-lc", cmd],
        cwd=WORKBENCH,
    )
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", choices=CANONICAL_SKILLS, help="Verify one skill.")
    parser.add_argument("--all", action="store_true", help="Verify all six skills.")
    parser.add_argument("--dry-run", action="store_true", help="Force harness dry-run for all harness steps.")
    parser.add_argument("--sync", action="store_true", help="Sync workspace before remote SSH verify.")
    parser.add_argument("--local", action="store_true", help="Run on current host (skip SSH).")
    args = parser.parse_args(argv)

    if not args.skill and not args.all:
        parser.error("specify --skill <name> or --all")

    skills = list(CANONICAL_SKILLS) if args.all else [args.skill]
    config = load_config()
    verify_table = config["skill_verify"]

    if not args.local:
        failed = 0
        for skill in skills:
            if ssh_verify(skill, sync=args.sync, dry_run=args.dry_run):
                failed += 1
        return 1 if failed else 0

    runs_root = WORKBENCH / "results" / "skill_runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    manifest_entries = []

    failed = 0
    for skill in skills:
        if skill not in verify_table:
            print(f"unknown skill in skill_verify: {skill}", file=sys.stderr)
            failed += 1
            continue
        summary = verify_skill(skill, verify_table[skill], dry_run=args.dry_run, runs_root=runs_root)
        manifest_entries.append(
            {
                "skill": skill,
                "verdict": summary["verdict"],
                "evidence_dir": summary["evidence_dir"],
                "git_sha": summary["git_sha"],
            }
        )
        print(f"{summary['verdict']} {skill} -> {summary['evidence_dir']}")
        if summary["verdict"] == "FAIL":
            failed += 1

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "hostname": hostname_short(),
        "skills": manifest_entries,
    }
    (runs_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
