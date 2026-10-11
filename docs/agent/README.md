# Agent 协作入口

本仓是 DataSystem 的轻量配套仓库，定位与约束以 [`README.md`](../../README.md) 和 [`AGENTS.md`](../../AGENTS.md) 为准。

## 内容分工

- 新问题分析、bugfix 方案、设计、评审和验证记录：[`rfc/`](../../rfc/README.md)。
- 通用技术说明和操作指南：[`docs/`](../)。
- 明确需要的辅助工具：[`scripts/`](../../scripts/README.md)，优先复用已有能力。
- 历史 bugfix、workspace 和提取包：[`archive/`](../../archive/README.md)。
- 产品源码、产品测试和构建规则：独立的 `yuanrong-datasystem` 仓库。

## 工具入口

Codex 读取 `AGENTS.md`，Claude Code 读取 `CLAUDE.md`。共享技能正文位于 `.skills/`，两者通过各自发现目录中的符号链接加载，见 [技能说明](../../.skills/README.md)。不要为不同工具复制工作流。

命令从仓库根目录执行。配对源码目录使用同级 `../yuanrong-datasystem` 或 `DATASYSTEM_ROOT`。涉及产品行为时先读产品仓的 `AGENTS.md` 与 `.repo_context/`，结论以当前源码和日志为证据。

按任务选择已有脚本和风险验证范围，入口见 [`INDEX.md`](../../INDEX.md)、[`scripts-map.md`](scripts-map.md)。维护约定见 [`maintenance.md`](maintenance.md)。
