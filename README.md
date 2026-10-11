# yuanrong-datasystem-agent-workbench

Open Yuanrong DataSystem 的轻量配套仓库，主要承载 **文档、RFC 和必要的辅助脚本**。产品源码、产品测试和产品构建规则在独立的 `yuanrong-datasystem` 仓库维护。

## 内容边界

| 目录 | 用途 |
|------|------|
| [`docs/`](docs/) | 可复用的技术说明、操作指南、报告与证据说明 |
| [`rfc/`](rfc/README.md) | 新问题分析、bugfix 方案、设计、评审与验证记录 |
| [`scripts/`](scripts/README.md) | 已有辅助工具；新增工具仅在明确需要时加入，优先复用产品仓能力 |
| [`archive/`](archive/README.md) | 历史材料，保留供查阅，不作为当前任务入口 |

后续新专题统一建在 `rfc/YYYY-MM-DD-<topic>/`，至少提供一个 `README.md` 说明问题、证据、方案和验证状态，并登记到 [`rfc/README.md`](rfc/README.md)。不再新增顶层 `bugfix/` 或新的专题工作台。稳定且通用的说明再沉淀到 `docs/`；运行日志、构建缓存和大体积产物保存在仓库外。

历史 `bugfix/` 已归档到 `archive/bugfix/`，`workspace/archive/` 已归档到 `archive/workspace/`，DataSystem skills 提取包位于 `archive/extract/for-datasystem/`。既有 `plans/`、`workspace/` 等材料仍供历史查阅，后续新内容按上述三类组织。

## Codex 与 Claude Code

仓库约束统一在 [`AGENTS.md`](AGENTS.md)；Claude Code 通过 [`CLAUDE.md`](CLAUDE.md) 读取相同约束。技能正文只维护在 [`.skills/`](.skills/README.md)：

| 工具 | 项目技能入口 | 调用示例 |
|------|--------------|----------|
| Codex | `.agents/skills/<name>` → `.skills/<name>` | `$wb-docs` |
| Claude Code | `.claude/skills/<name>` → `.skills/<name>` | `/wb-docs` |
| Cursor（兼容） | `.cursor/skills/<name>/SKILL.md` 转向共享正文 | 按任务选择 skill |

六个既有技能保留：`wb-build`、`wb-dev`、`wb-daily`、`wb-perf`、`wb-docs`、`wb-html-publish`。按任务使用，不要求每个 RFC 运行完整构建或远程验证。具体命令见 [`.skills/README.md`](.skills/README.md) 和 [`INDEX.md`](INDEX.md)。

技能入口使用 Git 跟踪的相对符号链接。建议在 Linux/WSL checkout 中使用；Windows 原生 checkout 需启用符号链接，否则可按共享正文路径手工读取。`agents/openai.yaml` 只提供 Codex 的展示元数据，不代替技能发现目录。

## 协作规则

- 开始任务先确认仓库和版本，重要结论以 DataSystem 当前源码或日志为证据。
- 涉及产品行为时遵循产品仓的 `AGENTS.md` 和 `.repo_context/`；实现修改留在产品仓。
- 区分已验证事实、推断和待验证项，选择与风险匹配的验证范围。
- 推送前检查 remote，禁止推送到 `openeuler/yuanrong-datasystem`；只使用用户指定的 fork。
- 辅助脚本需要产品仓路径时，使用同级 `../yuanrong-datasystem` 或 `DATASYSTEM_ROOT`。

技能发现规范：[Codex](https://learn.chatgpt.com/docs/build-skills)、[Claude Code](https://code.claude.com/docs/en/skills)。
