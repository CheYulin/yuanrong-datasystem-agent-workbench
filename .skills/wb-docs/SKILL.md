---
name: wb-docs
description: >-
  Workbook reports and deliverables: perf Markdown, FEMA HTML, commit drafts, workbook Markdown sources.
---

共享约束：从仓库根目录执行命令，遵循 `AGENTS.md`。新增问题分析、bugfix 方案和验证记录放在 `rfc/YYYY-MM-DD-<topic>/`；可复用说明放在 `docs/`。仅按当前任务需要使用既有脚本，不自动扩展工作台或执行完整远程验证。

# Workbench Docs Deliverables

| Deliverable | Command |
|-------------|---------|
| KV perf Markdown | `python3 scripts/metrics/gen_kv_perf_report.py <logs…>` |
| Bugfix ↔ FEMA HTML | `python3 scripts/analysis/generate_bugfix_fema_report.py` |
| Commit message draft | `bash scripts/development/git/generate_commit_message.sh` |

Workbook sources: `docs/observable/workbook/sheet*.md`  
Conclusion HTML → **wb-html-publish**.
