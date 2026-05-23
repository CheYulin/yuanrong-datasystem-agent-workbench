# PPT/Word 生成 Skills 参考

> 明天（2026-05-25）用这些 Skills 生成正式的需求文档和汇报 PPT

## 推荐方案

### 方案1: markwell (最推荐)
- **安装**: `npm install -g markwell && markwell install-skills`
- **能力**: Markdown ↔ PPTX/DOCX/XLSX 双向转换
- **优势**: 一条命令安装，有主题支持，双向转换
- **来源**: `npmjs.com/package/markwell`

### 方案2: artifactry (Markdown → PPT)
- **安装**: Claude Code plugin `/plugin install jeremy193a/artifactry`
- **能力**: Markdown → DOCX/PDF/PPTX，15 种风格模板
- **优势**: 支持 frontmatter 路由，专业风格
- **来源**: `github.com/jeremy193a/artifactry`

### 方案3: 手动 skill (备选)
- Anthropic 官方 skills: `github.com/anthropics/skills`
- 包含 pptx/docx/xlsx/pdf 完整 skill
- 安装: `/plugin marketplace add anthropics/skills`
- 然后 `/plugin install document-skills@anthropic-agent-skills`

## 明天工作流

1. 用我们写的 Markdown 设计文档作为输入
2. 通过 markwell 或 artifactry 一键生成 PPTX/DOCX
3. 手工调整格式和排版

## 关键参考

- `github.com/tfriedel/claude-office-skills` — Office 文档 skill 集合
- `github.com/obviousworks/Claude-AI-skills-collection-2026` — 技能大全
- `github.com/BehiSecc/awesome-claude-skills` — 精选列表
