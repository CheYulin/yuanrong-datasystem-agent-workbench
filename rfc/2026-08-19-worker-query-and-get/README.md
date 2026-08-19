# Worker QueryAndGet 快速穿刺方案

本目录给出基于 DataSystem `main/master` `71fada0780e4f3d5475c7d7a9df1f5ae8e1bd042` 的方案 A：
Client 选择目标 Worker 和传输类型，Worker 通过新的 `WorkerOCService::QueryAndGet` 门面复用现有 Get 核心，
并在需要时由 Worker 查询 Master。

- [详细设计](detailed-design.md)
- [实现计划](implementation-plan.md)（设计评审收敛后补充）
- [评审记录](review-notes.md)（多代理评审后补充）

