#!/usr/bin/env python3
"""Create the single GitCode issue for SSD→HBM Track① PR-1 (datasystem repo)."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("API_BASE", "https://api.gitcode.com/api/v5")
# Fork has token permission; openeuler upstream returns 403 for most tokens.
OWNER = os.environ.get("OWNER", "yche-huawei")
REPO = os.environ.get("REPO", "yuanrong-datasystem")
TOKEN_FILE = Path.home() / ".local" / "gitcode_token"

# One PR → one issue. Deferred tasks (4b–6, L2) stay in RFC/WBS, not separate issues.
ISSUE = {
    "key": "track1",
    "title": "[Feature] SSD→HBM Direct (NDS) Track① — injectable interfaces + binmock verify",
    "body": """Phase-1 SSD→HBM direct I/O for **local spilled objects only** (feat/ssd-hbm-direct → openeuler/yuanrong-datasystem).

**This PR lands**
- `AlignmentGatePass`, `MockIpcHbmBackend`, `FakeNdsSpillReader`
- `HbmMappingTable`, `NdsDirectPath` eligibility helpers
- Focused UT target `ds_ut_nds` (14 cases) + Gate0 5× `HeteroD2HTest` (xqyun isolated verify)

**Explicitly out of scope (follow-up PRs, tracked in workbench RFC/WBS)**
- RegisterHbmBuffer / Unregister RPC
- Worker Get NDS bypass in `worker_oc_service_get_impl.cpp`
- `NdsBinmockFlow` e2e ST
- L2 real CANN IPC / xds backends

RFC: `yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/`""",
    "labels": "kind/feature",
}


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
    data = create_issue(token, ISSUE["title"], ISSUE["body"], ISSUE.get("labels", ""))
    number = data.get("number") or data.get("iid")
    url = data.get("html_url") or data.get("web_url") or data.get("url")
    results = {ISSUE["key"]: {"number": number, "url": url, "title": ISSUE["title"]}}
    print(f"{ISSUE['key']}: #{number} {url}")
    out_file = out_dir / "issues-created.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
