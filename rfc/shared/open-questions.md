# 待讨论问题清单

> 2026-05-24 午休前记录，下午逐一讨论

---

## 一、Lease 与故障检测

| # | 问题 | 当前设计 | 待讨论 |
|---|------|---------|--------|
| Q1 | Lease 软超时 3s 是否合理？ | 3s 软超时 / 10s 硬超时 | 1s 心跳间隔, 3 次丢失=3s→ 网络毛刺是否会误触发? |
| Q2 | 旧 Primary 恢复后 seqno 比新 Primary 高，怎么处理？ | seqno 108 > 107 → 旧 P 数据更新 → 新 P 降级 | 降级过程数据怎么迁？会有一致性窗口吗？ |
| Q3 | 网络分区时 Client 连不上 Meta Owner 但能连 Primary | Client 用缓存 ReplicaSetPb | 缓存过期时间多长？分区持续超时怎么办？ |

## 二、元数据

| # | 问题 | 当前设计 | 待讨论 |
|---|------|---------|--------|
| Q4 | metadata 异步推的延迟窗口多大？ | 后台推, 指数退避重试 | 100ms 够吗？500ms？需要定量评估 |
| Q5 | Meta Owner Handoff: HashRing 更新后旧 Meta Owner 上的数据怎么处理？ | 旧 Meta Owner 标记过时 | 新 Meta Owner 如何从旧 Meta Owner 获取初始数据？ |
| Q6 | metadata 副本数是否应该独立于 data 副本数？ | 当前统一 N=2 | 是否 metadata 应该 N=3 (更可靠), data N=2 (省资源)？ |
| Q7 | 一致性哈希变更(Meta Owner切换)时, 正在进行的写入怎么处理？ | — | 未讨论 |

## 三、故障与恢复

| # | 问题 | 当前设计 | 待讨论 |
|---|------|---------|--------|
| Q8 | Primary 在 Phase2 (SyncReplicate) 中途崩溃 —— Backup 上的数据 state 是什么？ | Backup data(seqno=N) 但 meta 可能是旧版本 | Backup 上数据是否可用？怎么区分 "半写入" vs "完整写入"？ |
| Q9 | 连续发出 3 个 Promote（W1→W2→W3→W1恢复） —— LiveLock 怎么防？ | — | 需要 Promote 冷却期？ |
| Q10 | 数据 Rebalance 迁移 Primary 时, Client 如何感知？ | — | 未讨论 |

## 四、性能

| # | 问题 | 当前设计 | 待讨论 |
|---|------|---------|--------|
| Q11 | SyncReplicate 的 URMA 写入已有可靠的低延迟保障(jfr polling 重试)，但网络拥塞时是否会自动降级到 TCP？ | 当前: 无自动降级 | 是否需要加 TCP fallback？ |
| Q12 | Snapshot 创建 pause 窗口 500us —— 对 P99 写入的影响多大？ | 500us 暂停 | 实测数据？是否可以增量 snapshot(只序列化变更)？ |

## 五、构建与验证

| # | 问题 | 待讨论 |
|---|------|--------|
| Q13 | 如何构建 datasystem whl 包 (含 dscli + dsbench) | 需要看 build.sh + agent-bench build guide |
| Q14 | 性能验证环境: 最小需要多少节点？ | 100 节点验证逻辑, 1024 节点验收规格 |
| Q15 | 故障注入测试框架 —— 是否有现成的 chaos testing 工具？ | — |

## 六、设计缺口

| # | 缺口 | 优先级 |
|---|------|:--:|
| G1 | Data Primary 迁移 (Rebalance) 的详细流程未设计 | P2 |
| G2 | Cross-AZ 副本放置策略未细化 | P1 |
| G3 | 客户端 SDK 返回 ReplicaSetPb 后的缓存策略未定义 TTL | P1 |
| G4 | 安全: SyncReplicate 的认证 / URMA MR 隔离已在 dfx-analysis 中讨论 | P2 |
