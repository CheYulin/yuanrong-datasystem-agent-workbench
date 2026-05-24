# 三层需求分解 + 并行执行计划

## 需求层级模型

```
IR (Incident Requirement)    — 业务需求，一个 IR 对应一个用户场景
  └── SR (System Requirement) — 系统需求，可独立测试验收
        └── AR (Atomic Requirement) — 原子任务，可独立并行开发
```

## 并行执行总览

```
人力A (Worker)  IR-1,2 →──→ IR-7 ──→ IR-8 ──→ 调优
人力B (分布)    IR-3 ──→ IR-5,6 ──→ IR-10 → IR-9
人力C (连接)    IR-11 ──→ IR-13 ──→ IR-12 → IR-14

Week 1   Week 2   Week 3   Week 4   Week 5   Week 6   Week 7   Week 8
───────┬───────┬───────┬───────┬───────┬───────┬───────┬───────┬───────
 A:Snap  A:Snap  A:Recv  A:Recv  A:Fail  A:Fail  A:Rec   A:Perf
 B:Upg   B:Upg   B:WrMR  B:WrMR  B:Read  B:Read  B:TTL   B:TTL
 C:ZPool  C:ZPool C:UPool C:UPool C:Jetty C:Jetty C:Gov   C:Perf
                      ↑
                集成测试窗口
```

---

## RFC1: 滚动升级原地恢复

| IR | 名称 | SR | 人 | 依赖 | 周 |
|----|------|----|----|------|:--:|
| IR-1 | 本地持久化 | SR-1.1 Snapshot 创建 · SR-1.2 CRC 校验 · SR-1.3 空间管理 | A | 无 | 1-2 |
| IR-2 | 快速恢复 | SR-2.1 Meta 恢复+对账 · SR-2.2 Data 恢复 · SR-2.3 State 恢复 | A | IR-1 | 2-3 |
| IR-3 | 升级态 | SR-3.1 UPGRADING 状态机 · SR-3.2 回滚 · SR-3.3 编排 | B | 无 | 1-2 |
| IR-4 | 版本兼容 | SR-4.1 Snapshot 版本检测 · SR-4.2 混合集群通信 | A | IR-1 | 4 |

**IR-1 详细 AR 拆解:**

| AR | 描述 | 验证 | 人天 |
|----|------|------|:--:|
| 1.1 | StateSnapshotPb 定义 + 序列化 → 本地 NVMe (write+fdatasync) | 单节点 Snapshot 文件完整可读 | 1.5d |
| 1.2 | Manifest 格式 (JSON + CRC32) + 多版本管理 | Manifest 可解析，CRC 校验通过 | 1d |
| 1.3 | SnapshotManager::Create() + 周期调度 (10s 间隔) | 连续 10 个周期均成功 | 1.5d |
| 1.4 | SnapshotManager::RestoreFromLatest() → Meta 恢复到内存 | 恢复后 object_table 条目数 = Snapshot 时 | 2d |
| 1.5 | VerifySnapshot() + 损坏检测 + 自动回退到上一版本 | 手工损坏 → 自动选择前一个 Snapshot | 1d |
| 1.6 | PruneOldSnapshots() (保留最近 3 个) + 磁盘空间预检 | 超过 3 个后最旧被删除 | 0.5d |

**IR-2 详细 AR 拆解:**

| AR | 描述 | 验证 | 人天 |
|----|------|------|:--:|
| 2.1 | Meta 对账: Worker → Master DeltaSync(seqno) | 重启后 object_table 与 etcd 一致 | 1.5d |
| 2.2 | State 对账: Worker → Master StateSync(HashRing, Promotions, Slots) | 重启后 HashRing 位置与 Master 一致 | 1d |
| 2.3 | Data 紧急恢复: Primary 对象 SHM 校验 + EmergencyRecover | kill -9 后 Primary 对象可立即 Get | 2d |
| 2.4 | Data 后台恢复: Backup 对象 LazyRecover | 后台 5min 内完成恢复 | 1d |

---

## RFC2+3: 数据多副本 (可靠性+性能)

| IR | 名称 | SR | 人 | 依赖 | 周 |
|----|------|----|----|------|:--:|
| IR-5 | 同步写入多副本 | SR-5.1 ReplicaManager · SR-5.2 SyncReplicate RPC · SR-5.3 Quorum | A,B | 无 | 1-3 |
| IR-6 | 副本反亲和 | SR-6.1 ReplicaPlacementPolicy · SR-6.2 Rack/Zone 感知 | B | HashRing | 2-3 |
| IR-7 | 故障切换 | SR-7.1 故障检测 · SR-7.2 Promote · SR-7.3 Client 重路由 | A | IR-5 | 4-5 |
| IR-8 | 数据修复 | SR-8.1 增量对账 · SR-8.2 全量恢复 | B | IR-7 | 5-6 |
| IR-9 | 一致性老化 | SR-9.1 TTL 同步 · SR-9.2 SeqNo 对账 | C | IR-5 | 6-7 |
| IR-10 | 均衡读取+NUMA | SR-10.1 副本评分 · SR-10.2 NUMA 偏好 · SR-10.3 性能 | B,C | IR-5,6 | 5-7 |

**IR-5 详细 AR 拆解:**

| AR | 描述 | 验证 | 人天 |
|----|------|------|:--:|
| 5.1 | ReplicaManager::CreateReplicas() — 并行 SyncReplicate 到 N 个 Backup | N=2 均 ACK 才返回 OK | 3d |
| 5.2 | SyncReplicateReqPb/RspPb 定义 + Worker-Worker RPC 实现 | 单 Backup 写入延迟与 URMA Write 一致 | 2d |
| 5.3 | Quorum=N/2+1 逻辑 + 不足时回滚 | 1/2 Backup 失败 → 返回 REPLICA_QUORUM_FAILED | 1d |
| 5.4 | CreateMeta 扩展: 携带完整 locations[] 和 quorum_size | Master 侧 locations 包含全部副本 | 1.5d |
| 5.5 | replica_count 配置 + best-effort 降级 | N=3, 空间不足时 N=2, 返回 OK | 1d |
| 5.6 | PublishObjectWithReplication 伪代码落地 | 端到端: Client Put→SyncReplicate→CreateMeta | 2d |

**IR-7 详细 AR 拆解:**

| AR | 描述 | 验证 | 人天 |
|----|------|------|:--:|
| 7.1 | Master 端故障检测: 心跳超时 3s → 触发 Promote | kill -9 Primary, 3s 内 Master 检测 | 2d |
| 7.2 | PromoteToPrimary: 选择 seqno 最高的 Backup | 1000 次切换, P99.99 < 5ms | 3d |
| 7.3 | Client ReplicaSetPb 缓存刷新 + 重路由 | Client 自动切换到新 Primary | 2d |
| 7.4 | 旧 Primary 恢复后 ReplicaManager::Reconcile | 旧 Primary 转为 Backup, seqno 对账 | 2d |

---

## RFC4: 1K节点建链优化

| IR | 名称 | SR | 人 | 依赖 | 周 |
|----|------|----|----|------|:--:|
| IR-11 | ZMQ 连接池 | SR-11.1 ZMQConnectionPool · SR-11.2 健康检查 · SR-11.3 预热 | C | 无 | 1-2 |
| IR-12 | Jetty 复用 | SR-12.1 JettyManager 分配 · SR-12.2 负载均衡 | C | URMA API | 3-5 |
| IR-13 | URMA QP 池化 | SR-13.1 QP 预建 · SR-13.2 QP 复用 · SR-13.3 回收 | C | 无 | 2-3 |
| IR-14 | 连接治理 | SR-14.1 空闲回收 · SR-14.2 统计 · SR-14.3 内存优化 | C | IR-11,13 | 6 |

**IR-11 详细 AR 拆解:**

| AR | 描述 | 验证 | 人天 |
|----|------|------|:--:|
| 11.1 | ZMQConnectionPool 数据结构: PeerPool (idle/active/queue) | 10 conns/peer, 并发 Acquire/Release | 2d |
| 11.2 | Acquire(): 复用 idle → 新建 → 排队 (100ms timeout) | 命中率 > 90% | 1.5d |
| 11.3 | HealthCheck(): ZMQ_EVENTS 检测断连并重建 | kill -9 对端, 30s 内检测并重建 | 1d |
| 11.4 | Prewarm(): 扩容时异步建连 100 peers | 100 连接 5s 内完成 | 1d |
| 11.5 | 集成 ZmqStubImpl::InitConn → Pool::Acquire | 现有 RPC 通路不受影响 | 1.5d |

---

## 每周里程碑

| 周 | 里程碑 | 产出 |
|:--:|--------|------|
| W1 | SnapshotManager + ZMQ Pool 可用 | 单节点 Create/Restore 通过, Pool 命中率 > 90% |
| W2 | UPGRADING 状态机 + URMA Pool | 状态切换测试通过, QP 预建 < 5s |
| W3 | SyncReplicate 端到端 | N=2 同步写入通过, P99 < 3ms |
| W4 | **集成测试窗口** | 三 RFC 联调, 回归测试, Perf 基线 |
| W5 | 故障切换 + Jetty | Promote 1000 次 P99.99 < 5ms |
| W6 | 均衡读取 + 连接治理 | 1写10读 QPS +30% |
| W7 | 全量测试 | 1024 节点模拟, 所有 SR 验收 |
| W8 | Bug 修复 + 性能调优 + 文档 | 交付 |

## 风险与缓冲

| 风险 | 概率 | 影响 | 缓冲 |
|------|:--:|------|------|
| URMA Jetty 海思驱动 bug | 高 | Jetty 复用不可用 | +2w (移至 W9-10) |
| 多副本性能不达标 | 中 | P99 > 3ms | +1w (优化 batch/并行度) |
| 1024 节点测试环境 | 高 | 无法验收 | 100 节点验证逻辑正确性 |
| Snapshot 恢复期间对账冲突 | 中 | Meta 不一致 | +1w (加强 DeltaSync) |

## 人力汇总

| 人力 | 负责 IR | 总人天 |
|------|---------|:--:|
| A (Worker 侧) | IR-1,2,4,7,8 | 33d |
| B (分布/放置侧) | IR-3,5,6,9,10 | 34d |
| C (连接/传输侧) | IR-11,12,13,14 | 34d |
| **合计** | **14 IR** | **~100d** |
