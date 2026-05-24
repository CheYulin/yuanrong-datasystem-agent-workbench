# 滚动升级原地恢复

**Status**: Draft  
**仓库**: yuanrong-datasystem  
**作者**: 作者  
**需求来源**: 业务项目 / 通算元戎  

## 目标

1. **本地持久化**: Worker 支持将关键状态持久化到本地盘，重启后秒级恢复
2. **快速恢复**: 滚动升级中 Worker 重启时避免全量数据迁移，P99.99 恢复时延 < 3s

## 竞品参考

见 `../shared/mooncake-competitive-analysis.md` §一

## 设计文档

- [design.md](./design.md) — 完整设计
- [4+1-view.md](./4+1-view.md) — 架构视图
- [requirement-decomposition.md](./requirement-decomposition.md) — 需求拆解规格
- [use-cases.md](./use-cases.md) — 用例描述
