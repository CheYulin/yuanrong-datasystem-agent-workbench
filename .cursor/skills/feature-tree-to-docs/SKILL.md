---
name: feature-tree-to-docs
description: >-
  Converts `workspace/reliability/feature_tree.txt` (TSV, hierarchical blanks)
  into the Agent-facing Markdown `docs/feature-tree/openyuanrong-data-system-feature-tree.md`
  with 表头说明 and full table; drops the 上线时间 column. Optionally refreshes
  the sibling `.xlsx`. Use when updating the feature tree source, syncing docs,
  or when the user mentions 特性树、feature_tree、或 openYuanrong 能力矩阵文档化。
---

# 特性树 → docs（本仓库约定）

## 固定路径（相对 `vibe-coding-files/` 根）

| 角色 | 路径 |
|------|------|
| **源** | `workspace/reliability/feature_tree.txt` |
| **输出 Markdown** | `docs/feature-tree/openyuanrong-data-system-feature-tree.md` |
| **可选 Excel** | `docs/feature-tree/openyuanrong-data-system-feature-tree.xlsx`（与 MD 同内容，无上线时间列） |

## 转换规则

1. **分隔符**：首行表头、数据行为 **Tab** 分隔。
2. **省略列**：丢弃 **`上线时间`**，其余四列保留：`维度`、`特性`、`子特性`、`可靠性测试是否覆盖`。
3. **层级填充**：若某行 **`维度`** 或 **`特性`** 为空，沿用上一行非空值（延续同一维度/特性下的子特性行）。
4. **Markdown**：正文含简短说明、指向源文件与可选 xlsx、**`## 表头说明`**（列语义）、**`## 特性树表`**（完整 GFM 表）；单元格内 `|` 需转义为 `\|`。

## 完成后

- 若 `docs/feature-tree/README.md` 或 `docs/README.md` 中特性树入口未指向该 MD，应补上链接（见现有条目）。
- 修改源表后应重新生成 MD（及按需更新 xlsx），避免文档与 `feature_tree.txt` 不一致。
