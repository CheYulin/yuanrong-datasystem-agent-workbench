#!/usr/bin/env python3
"""Create GitCode tracking issues for SSD→HBM deferred tasks."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("API_BASE", "https://api.gitcode.com/api/v5")
OWNER = os.environ.get("OWNER", "openeuler")
REPO = os.environ.get("REPO", "yuanrong-datasystem")
TOKEN_FILE = Path.home() / ".local" / "gitcode_token"

ISSUES = [
    {
        "key": "track1_parent",
        "title": "[Feature] SSD→HBM Direct (NDS) Track① — injectable interfaces + binmock verify",
        "body": """Phase-1 SSD→HBM direct I/O for local spilled objects only.

**Landed in PR (feat/ssd-hbm-direct)**
- AlignmentGate, MockIpcHbmBackend, FakeNdsSpillReader
- HbmMappingTable + NdsDirectPath eligibility
- ds_ut_nds (14 UT) + Gate0 5× HeteroD2H (xqyun isolated verify)

**Deferred**
- RegisterHbmBuffer RPC
- Worker Get NDS bypass
- NdsBinmockFlow e2e ST

RFC: yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/""",
        "labels": "kind/feature",
    },
    {
        "key": "task4b",
        "title": "[Feature] SSD→HBM Task 4b: RegisterHbmBuffer / Unregister RPC",
        "body": "Worker/client RPC: Export key → MockIpc Import → HbmMappingTable.\n\nRFC: rfc/2026-07-12-ssd-hbm-direct/implementation-plan.md Task 4b",
        "labels": "kind/feature",
    },
    {
        "key": "task5",
        "title": "[Feature] SSD→HBM Task 5: local spilled Get NDS bypass + fallback",
        "body": "Wire NdsDirectPath into worker_oc_service_get_impl.cpp KeepObjectDataInMemory.\n\nRFC: implementation-plan.md Task 5",
        "labels": "kind/feature",
    },
    {
        "key": "task6",
        "title": "[Feature] SSD→HBM Task 6: NdsBinmockFlow e2e ST",
        "body": "binmock e2e Register → spill → Get → FakeNds → D2H.\n\nRFC: implementation-plan.md Task 6",
        "labels": "kind/feature",
    },
    {
        "key": "task8",
        "title": "[Feature] SSD→HBM Task 8: WORKER_NDS_* observability",
        "body": "PerfKey / AccessRecorder for NDS direct vs fallback.\n\nRFC: observability.md",
        "labels": "kind/feature",
    },
    {
        "key": "task9",
        "title": "[Feature] SSD→HBM Task 9: CannIpcHbmBackend (L2 Stage A)",
        "body": "Real CANN IPC backend. L2 hardware validation.\n\nRFC: tech-brief-cann-ipc-hbm.md",
        "labels": "kind/feature",
    },
    {
        "key": "task10",
        "title": "[Feature] SSD→HBM Task 10: XdsNdsSpillReader (L2 Stage B)",
        "body": "Real xds read_file + drain_read. Depends on Stage A.\n\nRFC: tech-brief-xds-nds.md",
        "labels": "kind/feature",
    },
]


def load_token() -> str:
    for name in ("GITCODE_TOKEN", "GITCODE_ACCESS_TOKEN"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit("Missing GitCode token")


def create_issue(token: str, title: str, body: str, labels: str) -> dict:
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    query = urllib.parse.urlencode({"access_token": token})
    url = f"{API_BASE}/repos/{OWNER}/{REPO}/issues?{query}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "ssd-hbm-issue-create/1"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    token = load_token()
    out_dir = Path(__file__).resolve().parent.parent
    results: dict[str, dict] = {}
    for item in ISSUES:
        data = create_issue(token, item["title"], item["body"], item.get("labels", ""))
        number = data.get("number") or data.get("iid")
        url = data.get("html_url") or data.get("web_url") or data.get("url")
        results[item["key"]] = {"number": number, "url": url, "title": item["title"]}
        print(f"{item['key']}: #{number} {url}")
    out_file = out_dir / "issues-created.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
