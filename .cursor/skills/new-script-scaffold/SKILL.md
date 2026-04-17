---
name: new-script-scaffold
description: >-
  Scaffold a new script under scripts/, register it in the ops entry point,
  and update documentation indexes. Use when adding a new runnable script
  to the repository.
---

# 新增脚本脚手架

## 触发场景

- 用户要求新增验证 / 分析 / 构建 / 文档类脚本
- Agent 识别到重复操作流程应沉淀为脚本

## 步骤

### 1. 选择子目录

| 类别 | 子目录 | 示例 |
|------|--------|------|
| 编译辅助 | `scripts/build/` | 第三方依赖、ST 兼容构建 |
| 开发阶段 | `scripts/development/` | 索引刷新、git 工具、共享库 |
| 测试阶段 | `scripts/testing/` | KV executor、brpc 用例 |
| 分析阶段 | `scripts/analysis/` | 性能对比、锁基线、bpftrace |
| 文档阶段 | `scripts/documentation/` | Excel 生成、工作簿 |

### 2. 创建脚本

- 文件名使用 `snake_case.sh` 或 `snake_case.py`。
- 头部注释包含：用途、输入、输出、依赖（如 `openpyxl`）。
- Bash 脚本加 `set -euo pipefail`。
- Python 脚本使用 `#!/usr/bin/env python3`。

### 3. 注册 `./ops` 入口

在仓库根目录 `ops` 脚本中添加路由：

```bash
<category>.<name>)
    exec scripts/<category>/<script-file> "$@"
    ;;
```

### 4. 更新文档索引

按 [`docs/agent/maintenance.md`](../../docs/agent/maintenance.md) 检查清单：

- [ ] `scripts/README.md` — 子目录说明
- [ ] `docs/agent/scripts-map.md` — 按任务选脚本章节
- [ ] `docs/agent/decision-tree.md` — 路由表（如适用）
- [ ] `AGENTS.md` — 若形成新 Skill

### 5. 验证

```bash
./ops <category>.<name> --help   # 确认入口可用
```
