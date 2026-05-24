# 词汇表 (Glossary)

| 术语 | 缩写 | 定义 |
|------|------|------|
| **Datasystem** | DS | 分布式 KV Cache 系统，v0.8.1，用于大模型推理场景 |
| **Worker** | W | 数据节点，负责存储对象数据和元数据 |
| **Master** | M | 元数据管理节点，管理 key→location 映射、HashRing、扩容缩容 |
| **KV Client** | KC | 客户端 SDK，提供 Put/Get/Del 接口 |
| **Object** | Obj | 数据对象，由 key + value 组成，最大 8MB |
| **Shared Memory** | SHM | 共享内存，Worker 内部的数据存储介质 |
| **Hash Ring** | HR | 一致性哈希环，确定 key 归属的 Worker |
| **etcd** | etcd | 分布式 KV 存储，用于集群元数据和配置管理 |
| **RocksDB** | RDB | 本地持久化引擎，用于元数据和 WAL |
| **Sequence Number** | SeqNo | 单调递增序列号，用于副本一致性锚点 |
| **Quorum** | Q | N/2+1 确认数，同步写入的最低 ACK 数 |
| **Primary** | P | 主副本，写入入口 |
| **Backup** | B | 备副本，从主副本同步数据 |
| **SyncReplicate** | SR | 同步复制 RPC，Primary 向 Backup 推送数据 |
| **Promote** | — | 故障切换操作，将 Backup 提升为 Primary |
| **Checkpoint** | CP | 内存状态快照，用于快速恢复 |
| **StateSnapshot** | SS | 轻量级文件快照，序列化 Worker Meta/State 到本地 NVMe |
| **Slot Recovery** | SR | Slot 恢复框架，ETCD 协调的跨 Worker 恢复 |
| **ZeroMQ** | ZMQ | 消息队列库，用于 RPC 控制面通信 |
| **Universal RDMA Access** | URMA | RDMA 传输层，用于数据面零拷贝传输 |
| **Jetty** | J | URMA 的 Queue Pair (QP)，负责 RDMA 发送/接收 |
| **Jetty Flow Send** | JFS | 发送端 Jetty |
| **Jetty Flow Receive** | JFR | 接收端 Jetty 的缓冲队列 |
| **Jetty Flow Completion** | JFC | 完成队列，轮询获取 URMA 操作结果 |
| **Remote Jetty Flow Receive** | rJFR | 导入的远端 JFR 句柄 |
| **CTP** | CTP | Chip Transport Path，海思芯片上的传输路径 |
| **HCCS** | HCCS | Huawei Cache Coherent System，海思片上互连总线 |
| **NUMA** | NUMA | Non-Uniform Memory Access，非一致性内存访问架构 |
| **Connection Pool** | CP | 连接池，复用 socket/QP 减少创建开销 |
| **Service Requirement** | SR | 系统需求，可独立测试验收 |
| **Atomic Requirement** | AR | 原子任务，可独立并行开发 |
| **DFX** | DFX | Design for X，面向可靠性/安全/性能/可服务性的设计 |
| **RPO** | RPO | Recovery Point Objective，最大数据丢失窗口 |
| **RTO** | RTO | Recovery Time Objective，最大恢复时间 |
| **P50/P90/P99/P99.99** | — | 延迟百分位数，如 P99=100us 表示 99% 请求 <100us |
| **gRPC** | gRPC | Google RPC，用于 Client↔Worker↔Master 通信 |
| **Protobuf** | PB | Protocol Buffers，序列化格式 |
| **FDS** | FDS | Functional Design Specification，功能设计说明书 |
