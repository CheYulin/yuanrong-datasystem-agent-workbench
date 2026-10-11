# RFC

本目录存放 **特性开发任务**（RFC / 设计文档 / 验证手册 / PR 文案）的工作区。与 `docs/` 的区别：

| 目录 | 角色 |
|------|------|
| `docs/` | 面向读者的稳定文档（架构、可靠性、可观测、运维手册） |
| `rfc/` | 面向开发的实施任务：问题分析、方案、验证、issue/PR 文案 |
| `plans/` | 更细粒度的开发阶段 todo/进度（短周期、高频迭代） |

> **后续计划**：`plans/` 与 `rfc/` 将合并成统一的特性开发工作区。当前先保留两者共存。

---

## 状态

每个 RFC 顶层 `README.md` 必须带 **Status** 字段：

- **Draft**：方案草稿，尚未实施
- **In-Progress**：方案确认，部分代码已提交
- **Done**：代码已并入 datasystem 主干，文档作为历史参考
- **Archived**：已被更新方案取代

---

## 当前 RFC

按创建日期倒序排列（最新在上）：

| 日期 | RFC | Status | 落地位置（datasystem） |
|------|-----|--------|-----------------------|
| 2026-08-10 | [2026-08-10-etcd-membership-lease-rebind-recovery/](2026-08-10-etcd-membership-lease-rebind-recovery/README.md) | **Draft** | Issue [#1027](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1027)：补齐外部 ETCD missing-local 的 cleanup + cold rejoin 闭环；新增 focused ST、必要 UT 与单 Worker 断链恢复 cluster ST；见 [detailed-design](2026-08-10-etcd-membership-lease-rebind-recovery/detailed-design.md) |
