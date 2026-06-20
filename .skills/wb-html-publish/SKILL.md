---
name: wb-html-publish
description: >-
  Publish conclusion-grade HTML to yche.me via xqyun /var/www/html git repo.
  Use when writing reports, competitive analysis pages, or registering the portal.
  Do not maintain local git-repos/htmls/.
---

# Workbench HTML Publish (yche.me)

## Canonical repo

- **Edit on:** `xqyun-32c32g:/var/www/html` (git)
- **Do not** maintain `git-repos/htmls/` locally

## Skill verification (xqyun only)

Build/verify skills run on **tiantiyun**; HTML publish checks run here:

```bash
# from local/WSL
bash scripts/harness/run_skill_html_verify_remote.sh
```

## Workflow

```bash
bash scripts/development/sync/publish_htmls_git.sh pull|status|push
# or ssh xqyun-32c32g, edit /var/www/html, register index.html sidebarPages, commit, push
curl -sI "https://yche.me/<path>.html" | head -1
```

Read remote `CLAUDE.md` + `assets/template.html` before writing.  
Publish **conclusions only** — not Draft RFC or raw L8 perf dumps.

## Ground truth (datasystem code claims)

```bash
codegraph status .
codegraph query <Symbol> --path .
```

Tag: CG-OK / CG-STALE / NARRATIVE.
