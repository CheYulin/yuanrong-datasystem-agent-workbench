# Worker QueryAndGet 快速穿刺方案

本目录给出方案 A 的设计、实现和验收记录。方案最初基于 `main/master`
`71fada0780e4f3d5475c7d7a9df1f5ae8e1bd042` 完成设计，最终实现已刷新到
`main/master@18bbb2051f2ef7390d0b6c8086d644a53b09284d`，PR HEAD 为
`aaef87b2b29e199d56269ea2f6782b66b40ca2c2`：
Client 选择目标 Worker 和传输类型，Worker 通过新的 `WorkerOCService::QueryAndGet` 门面复用现有 Get 核心，
并在需要时由 Worker 查询 Master。

- [详细设计](detailed-design.md)
- [实现计划](implementation-plan.md)（设计评审收敛后补充）
- [评审记录](review-notes.md)（多代理评审后补充）
- [需求图追溯](source-traceability.md)（用户提供 Excalidraw 的摘要与指纹）
- [最终验证](validation.md)
