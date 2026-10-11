# Fuse on DataSystem KV RFC

| Attribute | Value |
|---|---|
| Created | 2026-08-04 |
| Status | **Draft** |
| Scope | 基于 yuanrong-datasystem 的 KV/Buffer/Etcd 接口，实现一个 JuiceFS-like 的 FUSE 文件系统服务器 |

## Files

| File | Purpose |
|---|---|
| `juicefs-investigation.md` | JuiceFS 概念模型与模块调研分析（最简化版） |
| `design.md` | Fuse-on-DataSystem-KV 的子模块详细设计（参照 `ds-design` skill 的 detailed 模板） |

## 范围

本 RFC 包含：
1. **调研**：JuiceFS 的概念模型如何拆分（chunk/slice/block）、元数据 K-V schema、强一致模型
2. **设计**：基于 DataSystem KV/Buffer/Etcd 接口做最简化 FUSE server，子模块划分、关键交互、约束、落地步骤、测试

## 决策摘要（详见 design.md）

- **数据路径**：共享内存 `Buffer`（DS `KVClient::Create` + `Buffer::Publish/Seal`）
- **chunk→slice 编码**：每 chunk 一条 KV（`A{ino}C{idx}` → `[]Slice` 二进制）
- **锁**：ETCD lease + CAS
- **写模式**：writeback 默认 + 按路径策略分流（DB 路径走同步 fsync）
- **部署**：嵌入式 `InitEmbedded` + 独立 fuse 客户端两种
- **小文件 inline**：≤ 256 KiB inline 到元数据 KV
- **mmap**：第一版只支持 MAP_PRIVATE 读
- **元数据缓存**：L0 进程内（attr/dentry，TTL = 60s/30s，参照 JuiceFS production 实测参数）
- **后端抽象**：v1 同时出 `MetaBackend` trait + `DsEtcdBackend` 实现。v2 可挂 `DsCoordinatorBackend`（走 DS 内置 braft Raft）作为 ETCD 高密度升级路径

## 关键应对：高并发 metadata 场景

ETCD 单集群在 metadata QPS / RTT 上有结构性上限。本 RFC 的应对策略：
1. **L0 cache 激进 TTL（60s/30s）** + watch 精确 invalidate → 把 90% 的 stat/open 收敛在 fuse-server 进程内
2. **`MetaBackend` trait 解耦**：v1 不绑死 ETCD，未来可加 Coordinator 模式或 TiKV 模式
3. **v1 部署推荐**：DS worker + ETCD 同节点部署，规避 cross-host RPC RTT

## 落地分阶段

详见 `design.md §7 落地步骤`。
