# 滚动升级原地恢复 — 执行计划

## 时间窗口: 5月24日 03:00 ~ 09:00 (6h)

## 阶段划分

### Phase 1: 竞品调研 (03:00-04:00)
| 任务 | 产出 |
|------|------|
| Mooncake 滚动升级/原地恢复方案调研 | `research/mooncake-rolling-upgrade.md` |
| Mooncake 数据多副本方案调研 | `research/mooncake-multi-replica.md` |
| Mooncake 大规模节点 (1K+) 设计 | `research/mooncake-large-scale.md` |
| 其他竞品补充 (InfiniCache, DICE等) | `research/competitive-analysis.md` |

### Phase 2: datasystem 代码深读 (04:00-05:30)
| 任务 | 代码路径 |
|------|---------|
| Worker 启动/恢复流程 | `worker/worker_oc_server.cpp`, `worker/object_cache/service/` |
| KV 写入/读取路径 | `client/kv_cache/kv_client.cpp`, `worker/.../worker_oc_service_*` |
| Hash ring 分布与副本 | `common/metastore/`, `worker/.../hash_ring*` |
| ZMQ RPC 连接管理 | `common/rpc/zmq/zmq_stub_impl.cpp`, `zmq_service.cpp` |
| URMA 传输层 | `common/rdma/urma_manager.cpp`, `urma_resource.cpp` |
| 持久化/存储层 | `common/object_cache/`, RocksDB 相关 |
| TTL/淘汰 | `worker/.../ttl*`, `eviction*` |
| 扩容/缩容 | `worker/.../scale*`, `master/` |
| Jetty/CTP 连接 | 搜索 Jetty, CTP 相关代码 |
| 现有 repo_context 文档 | `.repo_context/modules/` 全部读完 |

### Phase 3: 设计方案 (05:30-07:30)
| RFC | 产出 |
|-----|------|
| 滚动升级原地恢复 | `design.md`, `4+1-view.md`, `interface-changes.md` |
| 数据多副本 | `design.md`, `4+1-view.md`, `interface-changes.md` |
| 1K节点建链优化 | `design.md`, `connection-model.md` |

### Phase 4: 输出整理 (07:30-08:30)
| 任务 | 产出 |
|------|------|
| 整体 4+1 视图 | 逻辑/进程/物理/开发/场景 五视图 |
| 接口变更汇总 | 跨三个 RFC 的 API 兼容性分析 |
| 风险清单 | 技术风险 + 工期风险 |
| git commit | 所有内容提交 |

## 产出目录结构
```
rfc/
├── 2026-05-rolling-upgrade-inplace-recovery/
│   ├── README.md
│   ├── design.md
│   ├── research/
│   │   ├── mooncake-rolling-upgrade.md
│   │   └── competitive-analysis.md
│   └── 4+1-view.md
├── 2026-05-multi-replica/
│   ├── README.md
│   ├── design.md
│   ├── research/
│   │   ├── mooncake-multi-replica.md
│   │   └── competitive-analysis.md
│   └── interface-changes.md
├── 2026-05-1k-node-connection-opt/
│   ├── README.md
│   ├── design.md
│   ├── research/
│   │   └── mooncake-large-scale.md
│   └── connection-model.md
└── shared/
    ├── 4+1-overall-view.md
    └── interface-impact-summary.md
```
