# 辅助时序图（PlantUML）

| 文件 | 内容 |
|------|------|
| [seq-client-init-and-failover.puml](./seq-client-init-and-failover.puml) | **Client**：`ServiceDiscovery` 注入、**Init** 时 `SelectWorker` + 连 Worker；**`TryFastTransportAfterHeartbeat` / URMA 快路径**；**切流** 时 `GetAllWorkers`、**`Disconnect` + 旧 CWA 析构**、再 **Standby 上重新 Register 与 URMA 握手**（与 Po2 可并存）。 |
| [seq-urma-client-worker-lifecycle.puml](./seq-urma-client-worker-lifecycle.puml) | **仅 URMA**：`TryUrmaHandshake`、失败回退 TCP 与**可选重试**、**`stopUrmaHandshakeRetry_`** 与 `Disconnect` / Worker `RemoveClient` 对称释放、进程级 `UrmaManager` 关闭（与 [notes-use-urma-ub-lifecycle.md](../notes-use-urma-ub-lifecycle.md) 配套）。 |
| [seq-worker-etcd-lease.puml](./seq-worker-etcd-lease.puml) | **Worker**：`EtcdClusterManager` → `EtcdStore::InitKeepAlive` → `LeaseGrant` + **PutWithLeaseId** 写 `KeepAliveValue`；`EtcdKeepAlive` 流**仅续租**；`UpdateNodeState` 为**显式**改 value。 |

**Client 连接数如何给「切流」用？**

- **gRPC Client→Worker 心跳不** 写 `ETCD_CLUSTER_TABLE`。  
- **租约流（LeaseKeepAlive）只续 TTL**，不更新节点 value。  
- 要在 **GetAll** 里看到**每节点**连接数，需在 Worker 上把计数 **写进与租约绑定的同一条 KV**（`PutWithLeaseId`，`leaseId` 不变），例如扩展 `KeepAliveValue` 的序列化，并在 **`RegisterClient` / `RemoveClient`** 或**定时**路径里**节流**更新。  
- **Client** 在 `ServiceDiscovery::ObtainWorkers` 解析到该字段后，供 `GetStandbyWorkersForSwitch` 路径上的 **Po2** 使用。详见 [issue-rfc.md](../issue-rfc.md)、[design.md](../design.md)。

**渲染**

```bash
# 有 plantuml.jar / docker plantuml 时
plantuml -tsvg seq-client-init-and-failover.puml seq-urma-client-worker-lifecycle.puml seq-worker-etcd-lease.puml
```

或 IDE / `docs` 站点的 PlantUML 插件打开 `.puml`。

**若报 Syntax Error（时序图分区）**：

- 每一行「分组标题」**必须**写成 **`==` + 标题 + `==`（两侧双等号）**；只写行首 `==` 会导致解析失败。
- 较旧 **PlantUML** 可能不支持 `!theme plain`；本目录图已不依赖 `theme`。
- 避免用 **`User` 作 actor 名**（与部分引擎保留字/样式冲突），已改为 **`Client`**。
