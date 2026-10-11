---
name: wb-html-publish
description: >-
  Publish conclusion-grade HTML to yche.me via xqyun /var/www/html git repo.
  Use when writing reports, competitive analysis pages, or registering the portal.
  Do not maintain local git-repos/htmls/.
---

共享约束：从仓库根目录执行命令，遵循 `AGENTS.md`。新增问题分析、bugfix 方案和验证记录放在 `rfc/YYYY-MM-DD-<topic>/`；可复用说明放在 `docs/`。仅按当前任务需要使用既有脚本，不自动扩展工作台或执行完整远程验证。

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

## 默认导航与字体

创建、修改或统一 yche.me 文档页时，读取 [紧凑文档导航约定](references/compact-navigation.md)，以用户指定的 [HIXL × NIXL 页面](https://yche.me/research/hixl-nixl-20261012.html) 为视觉基准：12px 导航、236px 桌面固定左栏、清晰编号与当前章节高亮、统一微软雅黑。使用本 skill 的公共导航适配资产，保留单一固定头栏与单一目录；顶部“章节目录”按钮控制同一目录的展开/收起，桌面章节跳转后保持目录展开；手机目录折叠，图注居中。不要将基准页的静态模式复制成所有页面的默认模式。

正文主章节默认 **6 个**，通常控制在 **6–8 个**（对应目录中的 h2；页面标题 h1 不计入）。细分内容放在 h3/h4 二级、三级标题；按内容合并章节，不靠隐藏目录项减少数量。短文无需凑足 6 章。

迁移旧页面时保留文章内容、原有发布时间、评论/权限和图表交互。完成参考文档里的真实浏览器验收后才能报告样式统一；服务器不可访问时明确区分本地准备与线上迁移。

## Ground truth (datasystem code claims)

```bash
codegraph status .
codegraph query <Symbol> --path .
```

Tag: CG-OK / CG-STALE / NARRATIVE.
