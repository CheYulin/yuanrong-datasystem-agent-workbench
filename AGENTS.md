# Repository Instructions

本仓库是 Open Yuanrong DataSystem 的轻量配套仓库。当前内容边界以 `README.md` 为准：主要承载 `docs/`、`rfc/`、`scripts/`，技能与 Agent 入口只提供必要的协作配置。

## 内容与任务边界

- 新的问题分析、bugfix 方案、设计、评审和验证记录统一放在 `rfc/YYYY-MM-DD-<topic>/`，并更新 `rfc/README.md`。
- 可复用说明放在 `docs/`；必要的辅助脚本放在 `scripts/`。优先复用产品仓能力，不扩展独立工作台或重复构建体系。新增脚本须有明确任务需求。
- `archive/` 是历史资料，不作为当前默认工作流。不要在其中追加新专题；需要借鉴历史资料时检查其适用版本。
- 既有 `plans/`、`workspace/` 等目录属于历史兼容材料，后续新专题统一进入 `rfc/`。
- 原始运行日志、缓存、构建产物及大文件放在仓库外。文档只记录必要证据与产物位置。
- 产品源码、产品测试和构建规则在 `yuanrong-datasystem` 修改，本仓只保留配套材料。

## Codex / Claude Code / Cursor

- Codex 从 `AGENTS.md` 读取约束，Claude Code 从 `CLAUDE.md` 转入同一份约束。
- 技能正文唯一来源是 `.skills/<name>/SKILL.md`。
- `.agents/skills/<name>`（Codex）和 `.claude/skills/<name>`（Claude Code）是指向共享技能目录的相对符号链接；Cursor 保留跳转入口。
- 按任务读取对应技能，不强制执行完整 harness 或远程验证。技能中的命令均从仓库根目录执行。
- 不要在工具入口复制技能正文。维护技能后检查名称、description、发现路径和命令引用。

## DataSystem 工程与 Git 约束

- 重要结论以当前源码或日志为准，区分事实、推断和未观察到的运行证据。
- 涉及 DataSystem 行为、源码或测试时，先读取产品仓 `AGENTS.md`、`.repo_context/README.md`、`index.md`、`maintenance.md`，再选择相关模块和工程工作流。
- 性能、并发安全、内存安全、恢复正确性和可用性是产品工程要求；验证范围按改动风险选择。
- 禁止向 `openeuler/yuanrong-datasystem` 的任何分支推送。每次 push 前验证 remote，只推送用户授权的 fork。
- 配对源码路径是同级 `../yuanrong-datasystem` 或显式 `DATASYSTEM_ROOT`；DataSystem 隔离 worktree 使用 `datasystem` 命名。
- 归档或迁移保留内容，修复受影响的链接和脚本路径；避免无关格式修改。
- 提交前检查 diff、相关验证结果和工作区状态，报告实际验证范围。

## 入口

- `README.md`：仓库定位与内容边界。
- `INDEX.md`：当前内容及既有脚本/技能入口。
- `.skills/README.md`：共享技能与工具发现方式。
- `docs/agent/README.md`：协作说明。
