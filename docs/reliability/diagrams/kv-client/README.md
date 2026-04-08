# KV Client 故障处理与 triage 配图（PlantUML）

对应 [`plans/kv_client_triage/FAULT_HANDLING_AND_DATA_RELIABILITY.md`](../../../../plans/kv_client_triage/FAULT_HANDLING_AND_DATA_RELIABILITY.md) **第六节** 叙事；与 [`00-kv-client-fema-read-paths-reliability.md`](../../00-kv-client-fema-read-paths-reliability.md) 对照阅读。

| 文件 | 说明 |
|------|------|
| [kv_client_triage_doc_map.puml](kv_client_triage_doc_map.puml) | `plans/kv_client_triage` 下各 md 的逻辑分层 |
| [fault_handling_ub_plane_and_tcp.puml](fault_handling_ub_plane_and_tcp.puml) | UB 多平面 / 单平面、~128ms / ~133ms 与短超时 |
| [fault_handling_sdk_etcd_failover.puml](fault_handling_sdk_etcd_failover.puml) | SDK ~2s 切流、etcd 隔离 ~3s 量级 |
| [fault_handling_data_reliability.puml](fault_handling_data_reliability.puml) | 异步持久化、分片迁移与预加载 |
| [fault_handling_etcd_degradation.puml](fault_handling_etcd_degradation.puml) | etcd 单节点 / 续租失败 / 全挂降级 |

**读写主路径时序**见 [`../../../flows/sequences/kv-client/`](../../../flows/sequences/kv-client/)。
