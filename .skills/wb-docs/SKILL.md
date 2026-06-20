---
name: wb-docs
description: >-
  Workbook reports and deliverables: perf Markdown, FEMA HTML, commit drafts, workbook Markdown sources.
---

# Workbench Docs Deliverables

| Deliverable | Command |
|-------------|---------|
| KV perf Markdown | `python3 scripts/metrics/gen_kv_perf_report.py <logs…>` |
| Bugfix ↔ FEMA HTML | `python3 scripts/analysis/generate_bugfix_fema_report.py` |
| Commit message draft | `bash scripts/development/git/generate_commit_message.sh` |

Workbook sources: `docs/observable/workbook/sheet*.md`  
Conclusion HTML → **wb-html-publish**.
