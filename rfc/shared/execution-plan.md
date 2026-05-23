# 三层需求分解 + 并行执行计划

## 需求层级模型

```
IR (Incident Requirement)    — 业务需求，一个 IR 对应一个用户场景
  └── SR (System Requirement) — 系统需求，可独立测试验收
        └── AR (Atomic Requirement) — 原子任务，可独立并行开发
```

## 并行执行总览

```
Week 1-2               Week 3-4               Week 5-6               Week 7-8
─────────────────────────────────────────────────────────────────────────
人力A: IR-1 Checkpoint → IR-2 恢复 ──────→ IR-7 故障切换 ──→ IR-8 恢复
人力B: IR-3 升级态 ────→ IR-5 多副本写入 ──→ IR-10 均衡读取 → IR-9 老化
人力C: IR-11 ZMQ Pool → IR-13 URMA Pool ──→ IR-12 Jetty ────→ IR-14 治理
                                        ↑
                              第4周 集成测试窗口
```

## RFC1: 滚动升级原地恢复 (3人×5周)

| IR | SR | 可并行 AR | 人 | 依赖 | 周 |
|----|-----|----------|----|------|:--:|
| IR-1 | CheckpointManager | AR-1.1 RocksDB Snapshot 封装 / AR-1.2 Manifest 格式 / AR-1.3 空间管理 | A | 无 | 1-2 |
| IR-2 | 快速恢复 | AR-2.1 恢复流程 / AR-2.2 增量对账 | A | IR-1 | 3 |
| IR-3 | HashRing UPGRADING | AR-3.1 状态机 / AR-3.2 迁移跳过 / AR-3.3 回滚 | B | 无 | 1-2 |
| IR-4 | 版本兼容 | AR-4.1 Checkpoint 版本检测 / AR-4.2 混合集群通信 | A | IR-1 | 4 |

**IR-1 详细 AR 拆解:**
| AR | 描述 | 验证 | 人天 |
|----|------|------|:--:|
| 1.1.1 | 封装 `rocksdb::Checkpoint` → `CheckpointManager::Create()` | 单节点创建成功，文件存在 | 1d |
| 1.1.2 | 实现 Checkpoint Manifest 格式 (JSON + CRC32) | Manifest 可解析，CRC 校验通过 | 1d |
| 1.1.3 | 实现 `RestoreFromCheckpoint()` → 恢复 metadata | 从 Checkpoint 恢复到 RUNNING <3s | 2d |
| 1.1.4 | 实现周期 Checkpoint 线程 + gflags | 10s 周期稳定运行 | 1d |
| 1.1.5 | 实现 `VerifyCheckpoint()` + 损坏检测 | 手工损坏 → 自动回退 | 1d |
| 1.1.6 | 实现 `PruneOldCheckpoints()` + 空间预检 | 保留最近3个，自动清理 | 1d |

## RFC2+3: 多副本 (3人×7周)

| IR | SR | 可并行 AR | 人 | 依赖 | 周 |
|----|-----|----------|----|------|:--:|
| IR-5 | 写入多副本 | AR-5.1 PutStart/PutEnd 协议 / AR-5.2 异步复制 / AR-5.3 副本计数配置 | A,B | 无 | 1-2 |
| IR-6 | 反亲和 | AR-6.1 HashRing 扩展 / AR-6.2 Rack/Zone 感知 | B | HashRing 接口 | 2-3 |
| IR-7 | 故障切换 | AR-7.1 故障检测 / AR-7.2 Promote / AR-7.3 Client 重路由 | A | IR-5 | 4-5 |
| IR-8 | 数据恢复 | AR-8.1 增量对账 / AR-8.2 全量恢复 | B | IR-7 | 5 |
| IR-9 | 一致性老化 | AR-9.1 TTL 同步 / AR-9.2 SeqNo 对账 | C | IR-5 | 6 |
| IR-10 | 均衡读取+NUMA | AR-10.1 副本评分 / AR-10.2 NUMA 偏好 / AR-10.3 性能验收 | B,C | IR-5,IR-6 | 6-7 |

**IR-5 详细 AR 拆解:**
| AR | 描述 | 验证 | 人天 |
|----|------|------|:--:|
| 5.1.1 | ReplicaManager::CreateReplicas() 核心逻辑 | 2副本创建成功，数据一致 | 3d |
| 5.1.2 | PutStart/PutEnd 两阶段协议实现 | PutEnd 前 Get 返回 NOT_FOUND | 2d |
| 5.1.3 | ReplicateChunk 异步传输 (URMA RDMA) | 备副本 1s 内 COMPLETE | 2d |
| 5.1.4 | min_synced_seqno 维护 + ACK 机制 | SeqNo 单调递增 | 1d |
| 5.1.5 | replica_count 配置 + best-effort 降级 | N=3，空间不足时 N=2 | 1d |

## RFC4: 建链优化 (2人×5周)

| IR | SR | 可并行 AR | 人 | 依赖 | 周 |
|----|-----|----------|----|------|:--:|
| IR-11 | ZMQ连接池 | AR-11.1 Pool 数据结构 / AR-11.2 健康检查 / AR-11.3 预热 | C | 无 | 1-2 |
| IR-12 | Jetty复用 | AR-12.1 JettyManager 分配 / AR-12.2 负载均衡 | C | URMA API | 3-4 |
| IR-13 | URMA QP池 | AR-13.1 QP 预建 / AR-13.2 QP 复用 / AR-13.3 回收 | C | 无 | 2-3 |
| IR-14 | 连接治理 | AR-14.1 空闲回收 / AR-14.2 统计指标 / AR-14.3 内存优化 | C | IR-11,IR-13 | 5 |

---

## 每周里程碑

| 周 | 里程碑 | 产出 |
|:--:|--------|------|
| W1 | CheckpointManager 可用 | 单节点 Checkpoint 创建/恢复通过 |
| W2 | HashRing UPGRADING + ZMQ Pool | 状态机测试 + 连接池压测通过 |
| W3 | 多副本写入可用 | PutStart/PutEnd 端到端测试通过 |
| W4 | **集成测试窗口** | 三 RFC 联调，回归测试 |
| W5 | 故障切换 + URMA Pool | Promote 1000 次 P99.99<5ms |
| W6 | Jetty 复用 + 均衡读取 | 海思环境 Jetty Cache miss<1% |
| W7 | 全部 AR 完成 | 全量测试 + 文档 |
| W8 | Bug 修复 + 性能调优 | 交付 |

## 风险与缓冲

| 风险 | 概率 | 影响 | 缓冲 |
|------|:--:|------|------|
| Checkpoint 与 RocksDB Replica 冲突 | 中 | 恢复数据不一致 | +1w |
| URMA Jetty 海思驱动 bug | 高 | Jetty 复用不可用 | +2w |
| 多副本性能不达标 | 中 | P99 > 3ms | +1w |
| 1024 节点测试环境不可用 | 高 | 无法验收 | 先 100 节点验证逻辑 |
