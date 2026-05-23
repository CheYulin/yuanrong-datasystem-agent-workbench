# 技术方案汇报 PPT 内容框架

## Slide 1: 封面
- 标题: datasystem v0.8.1 三大特性技术方案
- 副标题: 滚动升级原地恢复 · 数据多副本 · 1K节点建链优化
- 作者: 车煜林
- 日期: 2026-05-25

## Slide 2: 议程
1. 需求背景与业务价值
2. 竞品分析 (Mooncake)
3. 总体方案概览
4. 特性一: 滚动升级原地恢复
5. 特性二: 数据多副本 (可靠性+性能)
6. 特性三: 1K节点建链优化
7. 工作量与里程碑
8. 风险与应对

## Slide 3: 需求全景
- 四个需求 → 三个 RFC
  - 滚动升级 → RFC1 ✓
  - 多副本可靠性 + 均衡访问 → RFC2+3 (合并)
  - 1K节点建链优化 → RFC4
- 目标: 京东广告 KV Cache 场景 (1写10读, 8MB KV)
- 核心指标: 恢复<3s, 切换<5ms, QPS+30%

## Slide 4: 竞品对比 (Mooncake vs datasystem)
| 能力 | Mooncake | datasystem 当前 | datasystem 目标 |
|------|:--:|:--:|:--:|
| 本地持久化 | SHM/NVMe ✅ | ❌ RocksDB WAL only | ✅ 直接文件快照 |
| 滚动升级 | RBG InPlace ✅ | ❌ | ✅ UPGRADING 态 |
| 多副本 | Put/Copy/Move ✅ | ❌ Pull-on-read only | ✅ Push-on-write |
| 故障切换 | 秒级 ✅ | 分钟级 | P99.99<5ms |
| NUMA 亲和 | 自动发现 ✅ | FLAGS 已有 | 接入副本选择 |
| 1K节点 | 产线验证 ✅ | 未验证 | 目标 |

## Slide 5: 总体架构变化
- Mermaid: 逻辑视图 (Worker + CheckpointMgr + ReplicaMgr + Pool)
- 新增 7 个 .h/.cpp, 修改 18 个文件
- 3人×8周, 100人天

## Slide 6: RFC1 滚动升级 — 问题
- 现状: Worker 重启 → 全量从 etcd/peer 重建 → 分钟级
- 问题: 升级时 Worker 退出 → 内存数据丢失 → cache miss
- 目标: 重启恢复 <3s, 零数据迁移

## Slide 7: RFC1 滚动升级 — 方案
- 参考 Mooncake SHM/NVMe 快照
- StateSnapshot: 直接内存序列化 → 本地 NVMe
- write() + fdatasync → 落盘 <100ms
- 恢复: read() + protobuf 反序列化 → <3s
- HashRing 新增 UPGRADING 态

## Slide 8: RFC1 滚动升级 — 流程
- Mermaid: 升级流程 (Master → UPGRADING → Checkpoint → Restart → Restore → RUNNING)
- Mermaid: 回滚流程 (异常检测 → 自动回退)

## Slide 9: RFC2+3 多副本 — 问题
- 现状: 单副本, 故障后分钟级恢复
- 问题: Worker 故障 → 数据不可用 → 精排成功率下降
- 目标: 故障切换 P99.99<5ms, QPS+30%

## Slide 10: RFC2+3 多副本 — 方案
- Primary + N×Backup (N=2 default)
- 写入: PutStart → Write(Primary) → Replicate(async) → PutEnd
- 故障切换: Master Promote → Client 无感重路由 <5ms
- 均衡读取: score(副本) = NUMA+Health+Load-RTT

## Slide 11: RFC2+3 多副本 — 反亲和+NUMA
- 反亲和: Object 级, 跨 Rack 分布 (N=3 时)
- NUMA: Primary 与 Client 同 NUMA node, 不跨 HCCS
- 一致性: TTL 同步 + 60s 对账

## Slide 12: RFC2+3 多副本 — 元数据可靠性
- L1: etcd 持久化 (已有)
- L2: Client 缓存 + 版本号 (新增)
- L3: Worker 自检 → 重定向 (兜底)
- L4: Quorum 写入保证一致性
- RPO=0, RTO<5s (Master 重启)

## Slide 13: RFC4 建链优化 — 问题
- 现状: O(N²) 全连接, 1024节点 → ~1M 连接
- 问题: 海思 Jetty Cache 不足 → 性能下降
- 目标: 连接数 <10K/Worker, 扩容毛刺 <10ms

## Slide 14: RFC4 建链优化 — 方案
- ZMQ Pool: 10 conns/peer, 复用 + 健康检查
- URMA QP Pool: 预建 + 复用, 扩容预热
- Jetty 复用: 单 Jetty 8 CTP, 负载均衡
- Mermaid: 连接架构演进 (N×N → Pool)

## Slide 15: 工作量与里程碑
- Mermaid Gantt / ECharts: 8周计划
- W1-2: Checkpoint + ZMQ Pool
- W3-4: 多副本写入 + 集成测试
- W5-6: 故障切换 + Jetty
- W7-8: 全量测试 + 性能调优

## Slide 16: 风险
| 风险 | 概率 | 影响 | 缓解 |
|------|:--:|------|------|
| URMA Jetty 驱动 bug | 高 | Jetty 不可用 | +2w 缓冲 |
| 1024 节点环境不可用 | 高 | 无法验收 | 100节点验证逻辑 |
| 多副本性能不达标 | 中 | P99>3ms | 异步复制 + 批量 |

## Slide 17: 总结
- 三个特性, 100人天, 3人×8周
- 参考 Mooncake 但不照搬, 自主解决海思特有挑战
- 代码分析确认: 5项基础设施可直接复用
- 下一步: 启动 IR-1 Checkpoint, IR-5 多副本写入, IR-11 ZMQ Pool
