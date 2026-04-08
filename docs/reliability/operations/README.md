# 可靠性运维长文（KV Client / 部署扩缩容）

从 [`plans/kv_client_triage/details/`](../../../plans/kv_client_triage/details/) **镜像到本目录**，便于与 [`../00-kv-client-fema-*.md`](../00-kv-client-fema-index.md) 同层检索；**修订仍以 triage 深度分析为准时可回写 `plans/`**。

| 文档 | 说明 |
|------|------|
| [kv-client-ops-deploy-scaling-failure-triage.md](kv-client-ops-deploy-scaling-failure-triage.md) | 运维部署 / 扩缩容失败：L0–L5、冷启动、运行中变更 |
| [kv-client-worker-resource-log-triage.md](kv-client-worker-resource-log-triage.md) | `resource.log` 与[官方日志附录](https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/latest/appendix/log_guide.html) |
| [kv-client-scaling-scale-down-client-paths.md](kv-client-scaling-scale-down-client-paths.md) | K_SCALING / K_SCALE_DOWN 客户端可见性 |
| [kv-client-rpc-unavailable-triggers.md](kv-client-rpc-unavailable-triggers.md) | 1002 与 URMA 码分层 |

**PlantUML**：[`scaling_scale_down_sequences.puml`](../../flows/sequences/kv-client/scaling_scale_down_sequences.puml)（与 `plans/kv_client_triage/details/` 中源文件相同，以 `docs/flows` 下为准）。
