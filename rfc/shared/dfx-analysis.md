# DFX 分析 (Design for X)

> 覆盖三个 RFC 的可靠性、安全、性能、可服务性分析

---

## 一、可靠性分析 (Reliability)

### 1.1 故障处理策略

| RFC | 故障场景 | 降级策略 | 重试机制 | 熔断策略 |
|-----|---------|---------|---------|---------|
| RFC1 | Snapshot 文件损坏 | 回退到上一版本 Snapshot → 走 SlotRecovery 全量恢复 | 不重试 (已自动降级) | N/A |
| RFC1 | 本地 NVMe 写失败 | 跳过本次 Snapshot + WARNING 日志 + Metrics 告警 | 下一个周期重试 (10s后) | 连续3次失败 → 停用 Snapshot |
| RFC1 | 升级后新版本异常 | 30s 超时自动回滚到旧版本 | 回滚失败 → 人工介入 | 单节点升级失败 → 暂停整个升级 |
| RFC2 | SyncReplicate 到 Backup 超时 (500us) | 该 Backup 标记为 FAIL → 继续等剩余 Backup | 不重试 (Quorum 未达即返回错误) | N/A (单次同步写入) |
| RFC2 | Quorum 未达到 (N/2+1) | 回滚 Primary SHM → 返回 REPLICA_QUORUM_FAILED | Client 可重试整个 Put | N/A |
| RFC2 | 全部副本不可用 | 返回 REPLICA_UNAVAILABLE (1011) | Client 等待 Master 分配新副本 | Client 侧指数退避 (1s→5s→30s) |
| RFC4 | ZMQ Pool 耗尽 | 排队等待 100ms → 超时返回 error | 等待释放，超时后返回 | 100ms 超时 |
| RFC4 | Jetty 错误 (URMA_CR_WR_FLUSH_ERR) | ReCreateJetty() 异步重建 | 异步重建 (<50ms) | 连续3次失败 → 移除该 Peer |

### 1.2 数据一致性保障

| RFC | 一致性场景 | 保障机制 |
|-----|----------|---------|
| RFC1 | 恢复后 Meta 不一致 | seqno 锚点 + Master DeltaSync 对账 |
| RFC1 | 恢复后 Data 丢失 (Primary 对象) | EmergencyRecover from Backup (URMA, <2ms/obj) |
| RFC1 | 恢复后 State 变化 (Promote) | Master StateSync: 告知 HashRing 变更 + Promote 记录 |
| RFC2 | 并发写入同一 key | Worker 端写锁 (PublishObjectWithLock) + Master 端版本检查 |
| RFC2 | 副本间 seqno 不一致 | 每 60s ReconcileReplicas 对账，从 Primary 增量修复 |
| RFC2 | 元数据 vs 数据不一致 (孤儿对象) | Master 端定期 Inspection: 检查 locations 中失效 Worker → 触发修复 |
| RFC4 | 池化连接状态不一致 | ZMQ_EVENTS 健康检查 (30s) + 断连自动重建 |

### 1.3 容灾设计

| RFC | 场景 | RPO | RTO | 恢复方式 |
|-----|------|-----|-----|---------|
| RFC1 | Worker 进程崩溃 | 10s (最近 Snapshot 间隔) | <3s | Snapshot + DeltaSync |
| RFC1 | Worker 节点宕机 (NVMe 可用) | 10s | <3s (新进程启动) | Snapshot + DeltaSync |
| RFC1 | Worker 节点宕机 (NVMe 损坏) | 全量 | 分钟级 | SlotRecovery 跨 Worker 恢复 |
| RFC2 | Primary 故障 | 0 (同步写入) | <5ms (Promote + Client 重路由) | Backup 提升 |
| RFC2 | Master 故障 (etcd 可用) | 0 | <5s | etcd 恢复 metadata |
| RFC2 | Master + etcd 全部故障 | <10s | <30s | Worker 自声明重建 |

---

## 二、安全设计 (Security)

### 2.1 安全配置项识别

| RFC | 安全敏感点 | 风险等级 | 防护措施 |
|-----|----------|:--:|------|
| RFC1 | Snapshot 文件读/写 | 中 | 文件权限 600 (仅 Worker 进程可读写) |
| RFC1 | Snapshot 数据泄露 (含 metadata) | 中 | 文件系统加密 (依赖节点加密) |
| RFC2 | SyncReplicate 数据劫持 | 高 | URMA RDMA 自带加密 (依赖 RDMA 链路加密) |
| RFC2 | 副本放置泄露 (已知 Worker 拓扑) | 低 | 内部集群, 外部不可达 |
| RFC2 | Promote 操作 (故障切换) | 高 | Master 鉴权 + etcd CAS 防止并发 Promote |
| RFC4 | Jetty 共享导致数据串扰 | 高 | URMA 层 MR (Memory Region) 隔离 |
| RFC4 | 连接池耗尽 (DoS) | 中 | pool_size 上限 + 排队超时 |

### 2.2 敏感操作检查

| 操作 | 风险 | 防护 |
|------|:--:|------|
| PromoteToPrimary (故障切换) | 并发 Promote 导致脑裂 | etcd CAS 乐观锁，只有第一个成功 |
| RollbackPublish (写入失败回滚) | 误删有效数据 | 仅回滚当前 Put 的对象，校验 version 匹配 |
| PruneOldSnapshots (清理旧快照) | 删除了唯一可恢复快照 | 保留最后 3 个，只在创建新快照成功后清理 |
| DeleteJetty (释放 Jetty) | 释放正在使用的 Jetty | UrmaResource 引用计数 + AsyncDelete (延迟删除) |

### 2.3 安全技术货架组件

| 组件 | 用途 | RFC |
|------|------|-----|
| AK/SK 认证 | Worker↔Master RPC 身份认证 | RFC1,2,4 (已有) |
| etcd TLS | Master etcd 通信加密 | RFC1,2 (已有) |
| URMA MR 隔离 | 不同连接间的内存隔离 | RFC4 (已有) |
| ZMQ Curve 加密 | ZMQ socket 通信加密 | RFC4 (可选) |
| gflags 配置校验 | 防止非法配置值 | RFC1,2,4 (已有) |

---

## 三、性能分析 (Performance)

### 3.1 性能瓶颈分析

| RFC | 瓶颈 | 当前指标 | 目标 | 优化方案 |
|-----|------|---------|------|---------|
| RFC1 | Snapshot 创建暂停写入 | <500ms | <500ms (已达标) | 增量 Snapshot (只序列化变更) |
| RFC1 | 恢复时 EmergencyRecover | <2ms/obj (URMA) | 10K Primary 对象 = 20s | 并行恢复 + 后台 LazyRecover |
| RFC2 | SyncReplicate 并行等待 | P99=21us per Backup | N=2: P99=21us | 已并行, 无优化空间 |
| RFC2 | Master CreateMeta | P99=378us (现网) | 无需优化 | RocksDB 批量写入 |
| RFC4 | Jetty 重建延迟 | ~50ms (Create+Modify) | <50ms | 已有异步重建 |
| RFC4 | ZMQ 连接预热 | 100 conns = 5s | <5s | 已异步 |

### 3.2 可服务性 (Serviceability)

| 维度 | 设计 |
|------|------|
| **Metrics 指标** | ReplicaManager/SnapshotManager/Pool 均暴露 Counter/Gauge/Histogram |
| **关键告警** | Snapshot 失败 > 3次, Quorum 失败率 > 1%, Pool 命中率 < 50% |
| **日志级别** | INFO: 正常流程 (Snapshot创建/恢复, SyncReplicate成功) / WARNING: 降级 (回退到全量恢复) / ERROR: 失败 |
| **调试工具** | `dscli snapshot list` / `dscli replica health` / `dscli pool stats` |
| **灰度发布** | 10% MaxUnavailable 升级, 先非关键节点, 30s 观察窗口 |
