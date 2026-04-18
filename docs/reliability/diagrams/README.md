# 可靠性配图（PlantUML）

与 [`../05-reliability-design.md`](../05-reliability-design.md) 和 [`../01-architecture-and-paths.md`](../01-architecture-and-paths.md) 配合阅读。

| 文件 | 说明 |
|------|------|
| [fault_handling_ub_plane_and_tcp.puml](fault_handling_ub_plane_and_tcp.puml) | UB 多平面 / 单平面故障，~128ms / ~133ms 与短超时 |
| [fault_handling_sdk_etcd_failover.puml](fault_handling_sdk_etcd_failover.puml) | SDK ~2s 切流、etcd 租约到期隔离（`t = node_timeout_s`） |
| [fault_handling_etcd_degradation.puml](fault_handling_etcd_degradation.puml) | etcd 单节点 / 续租失败 / 全挂降级的状态图 |
| [fault_handling_data_reliability.puml](fault_handling_data_reliability.puml) | 异步持久化 + 故障后预加载恢复 |

读写主路径时序见 [`../../flows/sequences/kv-client/`](../../flows/sequences/kv-client/)。
