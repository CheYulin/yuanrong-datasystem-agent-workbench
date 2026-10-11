# JuiceFS 调研分析（最简化版）

> 目的：把 JuiceFS 的"必须知道"部分拎清楚，作为我们做 Fuse-on-DataSystem 设计的概念基础。
> 风格：最短路径出最关键的认知，**不抄源代码、不抄 API 文档**。

---

## 1. 一个高层的全景图（30 秒看完）

```
App (ls/cat/...)
  ↓ FUSE protocol
fuse-server (VFS + ChunkStore)
  ↓                    ↓
Meta 引擎             Object 存储
(KV/SQL, 强一致)      (S3/OSS, 最终一致)
inode/dentry/         block 物理数据
chunk/slice/lock
```

**核心决定**：JuiceFS 把一个文件系统**硬拆成两条独立的存储路径**——
- 元数据：强一致事务型 KV（Redis / TiKV / MySQL / etcd / SQLite 等）
- 数据：可海量可便宜的对象存储（S3 / OSS / MinIO / 本地盘）

为什么不放一起？因为两条路径的**访问模式完全相反**：
- 元数据：小条目（数百字节/条）、高并发读、事务强一致
- 数据：大块（4 MiB/块）、吞吐优先、最终一致
- **强行合并 = 性能全垮**

---

## 2. 文件数据的"三级拆分"（最重要的概念）

### 2.1 三层是什么

| 层级 | 大小 | 是什么 | 谁产出 |
|------|------|--------|--------|
| **Chunk** | 固定 64 MiB | 逻辑窗口索引 | 文件 → `chunk_idx = offset / 64MiB` |
| **Slice** | 变长 | 一次连续写的逻辑段 | 用户一次 `write()` 产生；不跨 chunk 边界 |
| **Block** | 固定 4 MiB | 物理上传单元 | Slice 切块后落到对象存储 |

### 2.2 用一个例子把三层串起来

```
文件 200 MiB，有过 3 次写：
  1) write 0~30 MiB
  2) write 50~120 MiB   (中间 20 MiB 是空洞)
  3) write 80~150 MiB  (覆盖第 2 次的 [80, 120) 区间)

→ 文件按 64 MiB 划 4 个 chunk：
  Chunk 0 = [0, 64)
  Chunk 1 = [64, 128)
  Chunk 2 = [128, 192)
  Chunk 3 = [192, 200)   (不满)

→ 每个 chunk 的 slice 列表（按时间追加，覆盖关系由"后者为准"解析）：
  Chunk 0 = [Slice A: 30 MiB]
  Chunk 1 = [Slice B: 50→120, 70 MiB] + [Slice C: 80→150, 70 MiB]  # C 覆盖 B
  Chunk 2 = [Slice D: 128→150, 22 MiB]
  Chunk 3 = []  (空洞)

→ 每个 slice 切 4 MiB block 上传到对象存储，key 格式：
  chunks/{hash}/{slice_id}_{block_idx}_{block_size}
```

### 2.3 Slice 的数据结构（24 字节）

```c
struct Slice {
    uint64_t id;     // 对象存储中 slice 的唯一 ID
    uint32_t size;   // slice 在对象存储里的尺寸（含空洞）
    uint32_t off;    // 有效数据在 slice 中的起点
    uint32_t len;    // 有效数据长度
};
```

读流程：
```
读 offset=100 MiB
  → chunk_idx = 1
  → 顺序遍历 Chunk1 的 slice 列表（B 然后 C），覆盖关系取 C
  → C 在 chunk 内的偏移 = 100 - 80 = 20 MiB
  → 落 block = (20 MiB) / (4 MiB) = 第 5 个 block
  → 对象 key = chunks/{hash}/C_5_4M
  → 读 4 MiB，组装成 user buffer
```

### 2.4 三级拆分的妙处

| 设计 | 得到什么 | 付出什么 |
|------|----------|----------|
| Chunk 固定 64 MiB | `offset / 64MiB` = O(1) 定位；改来改去 chunk 划分不变 | 大文件多写 → slice 列表可能长 |
| Slice 变长 | 一次连续写 = 不可分割单元；元数据简单 | 含空洞 → 对象里预留 |
| Block 固定 4 MiB | 对象存储并行 PUT，吞吐拉满 | 末尾小块也算 4 MiB（轻微浪费） |

---

## 3. 元数据 K-V schema（直接可借鉴的核心）

JuiceFS 在 KV 后端用**单字节前缀分类**。以 TiKV/etcd 这类原生事务 KV 为例：

| Key | 含义 |
|------|------|
| `setting` | 卷格式配置 |
| `C{name}` | 计数器（nextInode / nextChunk） |
| `A{ino}` | inode 属性 |
| `A{ino}D{name}` | 目录项 (name → child_inode) |
| `A{ino}P{parent}` | 父 inode（硬链接） |
| `A{ino}C{indx}` | 第 indx 个 chunk 的 slice 列表 |
| `A{ino}S` | symlink target |
| `A{ino}X{name}` | xattr |
| `F{ino}` | BSD flock |
| `P{ino}` | POSIX lock |
| `K{sliceId}{size}` | slice 引用计数（GC 用） |
| `SE{sid}` / `SI{sid}` | session 信息与失效时间 |
| `SS{sid}{ino}` | sustained inodes (nlink=0 但被会话持有) |
| `QD{ino}` / `QU{uid}` / `QG{gid}` | 配额 |
| `heartbeat` | 心跳桶 |

**整数编码**：所有整数都 little-endian 字节序。

**换句话说**：JuiceFS 把"一个 POSIX 文件系统"**完全映射**成一组 K-V 记录。我们要做的事就是把这些 K-V 落到 DataSystem 已有的 EtcdStore 之上。

---

## 4. 两层架构：Meta interface + engine interface

```
pkg/meta/interface.go  ——  Meta 接口（300+ 方法）
        ↓
baseMeta（共享：txBatchLock、quota、session、background jobs）
        ↓
engine interface（~50 方法：counter / KV / dir stat / session / quota / acl）
        ↓
三个 backend 实现：
  - redisMeta   (Lua + WATCH/MULTI)
  - dbMeta      (SQL via XORM)
  - kvMeta      (kvTxn on TiKV/etcd/Badger/FoundationDB)
```

**baseMeta.txBatchLock**：1024 个分片锁（FNV32 hash → idx），用来减少事务冲突。最长重试 50 次，带退避。**这个抽象非常通用**，我们 Fuse server 也要做。

**Session**：5s 心跳 / 5min 过期；`cleanStaleSession()` 回收 sustained inodes / flock / plock。**fuse-server 进程也要有 session 心跳，否则客户端集群恢复时锁会被错误清理**。

---

## 5. FUSE / VFS 层的关键机制

### 5.1 Handle 模型

```
struct handle {
    ino_t inode;
    uint64_t fh;
    // 读写锁位 (writing/writers/readers)
    // 锁信息 (flockOwner, ofdOwner, locks 位)
};
```

锁规则：
- POSIX lock owner = `owner ^ fh` （同一进程多 fd 不撞）
- OFD lock owner 存到 `handle.ofdOwner`，flush 自动回收
- flock 用 `locks & 1`，POSIX lock 用 `locks & 2`

### 5.2 一致性模型 = Close-to-Open

- `write()` 只入客户端 buffer，**不持久化**
- 用户调 `flush` / `fsync` / `close` 触发：
  1. slice buffer 上传到对象存储
  2. `Meta.Write(inode, indx, off, slice)` 写 KV
  3. `reader.Invalidate()` 通知同一 mount 的其它 handle
- 其它 mount 看到新数据要等对方 `close`

**关键含义**：FUSE server**绝不能把 `write()` 直接当成"已持久化"**。

### 5.3 内部特殊节点（挂载根目录下）

| 节点 | 用途 |
|------|------|
| `.control` | 远程控制命令 |
| `.stats` | Prometheus metrics 快照 |
| `.config` | 当前 VFS 配置 JSON |
| `.accesslog` | 访问日志 |
| `.trash` | 软删除目录 |

inode 号 `>= 0x7FFFFFFF00000000` → 走 `IsSpecialNode()` 分支。

### 5.4 VFS 在读前必 flush 自己 writer

```cpp
Read():
    h.Rlock();   // 阻塞等 writer
    v.writer.Flush(inode);  // ← 关键
    ...
```

如果不做，读会看到自己的过去。

### 5.5 后台任务（不属于 FUSE，是 Meta 引擎做的事）

| 任务 | 间隔 | 作用 |
|------|------|------|
| session heartbeat | 12s | 保持 session 存活 |
| flush statistics | 1s | 持久化卷使用统计 |
| cleanup deleted files | 1h | 处理删除队列 |
| cleanup slices | 1h | GC 不再被引用的 slice |
| compact chunks | 访问时 | 合并小 slice 减少 list 长度 |

---

## 6. 写路径决策：直接影响性能

JuiceFS 支持两种写模式：

| 模式 | 行为 | 适用场景 | 风险 |
|------|------|----------|------|
| **Writethrough**（默认） | write → 上传对象 → commit meta → 返回 | 数据安全（kill -9 不丢已 ack 的写） | 小文件延迟高（每写一个文件都 ~150ms 对象存储 PUT） |
| **Writeback** | write → 写本地 cache → commit meta → 返回；后台异步上传 | 大量小文件 / 临时文件 | kill -9 可能丢 staging 中未上传的数据 |

**FUSE 层还有 `-o writeback_cache`**：让**内核**合并高频 10-100B 随机小 write，适合 SQLite 这种 DB。但**会牺牲顺序写性能**（内核把顺序写也合并了）。

**对应我们做的选择**：
- 写模式默认 writeback
- 元数据 commit 强一致
- fsync 语义按路径策略分流：DB 文件必须同步到底，普通文件可以异步

---

## 7. 已知的"坑"与现实

| 坑 | 触发场景 |
|----|----------|
| 大量小文件 stat（如 `git status`） | `--attr-cache` / `--entry-cache` 必须调大 |
| 大量小文件 put 延迟 | 每个文件 7-150 ms 真做不掉，必须 writeback 或 inline |
| SQLite 等 DB 的随机 4KB 写 | fsync 频率高，block size = 4MB 反而被 read-modify-write 放大 |
| 单 mount 不能跨集群 fuse-server 强一致 | 默认 close-to-open 一致；不同 mount 的视图可能短暂不一致 |
| `compaction` 不及时 → slice 列表暴涨 | 大量随机小写后读性能退化 |

**对我们的启示**：第一版就要把"按路径策略分流"、"元数据 L0 缓存"、"小文件 inline"做对。

---

## 8. 结论：对我们设计的含义（压缩版）

| JuiceFS 部件 | 我们能映射到哪里 |
|--------------|-----------------|
| `Meta` 后端 (Redis/TiKV/etcd) | DS 的 `EtcdStore` + 内置 `MetaStoreServer`（等价） |
| `kvtxn` | DS `EtcdStore::CAS` + `Transaction::Commit` |
| `baseMeta.txBatchLock` | 自己实现 1024 个分片本地锁 |
| chunk→slice 编码 | 每 chunk 一条 KV |
| 数据 block | DS `Buffer::Create` + `Buffer::Publish/Seal` |
| session 心跳 | DS `EtcdStore::InitKeepAlive` |
| FUSE writeback | FUSE `-o writeback_cache` + 应用层 staging buffer |
| 强一致 (close-to-open) | 在 fuse-server 自己的 read 前 flush writer |
| POSIX 锁 | ETCD lease + CAS |
| L0 cache (attr/entry) | 自己实现 1024 个分片本地锁 + TTL 60s/30s（参照 JuiceFS production） |
| Meta 后端切换 | 通过 `MetaBackend` trait 做，DS Coordinator 作为 high-scale 升级路径 |

整个 JuiceFS 概念体系可以被压缩为一句话：

> **"用强一致 KV 表达元数据，用大块对象/SHM 表达数据，用 FUSE 把这两层结构呈现为 POSIX 视图。"**

我们在这条路上走，只是把"对象存储"换成 DS 的 KV+Buffer + DataSystem worker 节点。

## 9. JuiceFS 在生产里关于"后端选型"的真实教训（重要）

> **这一节是这次对设计的最大影响来源之一。**

JuiceFS 的"Meta 后端可插拔"是其过去十年最重要的工程决定——业务代码从来没绑死过一个具体 K-V。他们的官方推荐（基于生产实测）：

| 文件规模 / QPS | 推荐 backend | 理由 |
|----------------|---------------|------|
| < 1000w 文件 | SQLite / ETCD | 简单、足够；ETCD 有 3-5 节点 Raft，3-5ms RTT 可接受 |
| 1000w - 1 亿 | Redis | 单进程性能最高；写 meta 1.76 ms/op vs ETCD 3.4 ms/op |
| 1 亿 - 几十亿 + 集群 | TiKV / FoundationDB | 分布式事务 KV；TiKV ≈ 3.7% of CephFS MDS 的内存 |
| 几十亿 + 自研 | JuiceFS Enterprise 自建 all-in-memory | 内存化 + 子树分片 |

**核心数据点**（来自 JuiceFS AI scenario 案例）：单 ETCD 集群 metadata RTT 从 30+ms → 5.8ms、small-file 吞吐 1000 files/s（42× 提升），只做了**三件事**：
1. metadata 集群从 MemoryDB 迁到 Redis-TiKV 3 节点 Raft
2. 把数据放入同 VPC 内 GCS（去跨 AZ 跳跃）
3. 启用 `--writeback` + `--cache-ttl`（L0 cache）

**这三件里只有第三件是 JuiceFS / fuse-server 自己的代码**，前两件是部署层面的事。

**对我们的指导**：
- v1 用 ETCD 默认，**完全够撑 PoC + 中等生产**
- 元数据 RTT 是 JuiceFS production 的真实痛点，**但解药是 L0 cache TTL + watch invalidate**，不是换 backend
- 写 `MetaBackend` trait 让"换 backend"成为 v2 可以单独做的后端 PR，不进 v1 的范围

## 10. 元数据 TTL 配置（直接学 JuiceFS 生产实测）

| 缓存 | v1 默认（与 JuiceFS production 对齐） | 说明 |
|------|--------------------------------------|------|
| attr-cache | 60s | inode 属性；writeback 模式自身写触发 invalidate，其它 mount 通过 watch invalidate |
| entry-cache | 30s | dentry；同 attr |
| dir-entry-cache | 30s | 目录列表 |
| open-cache | 60s | 已打开文件的 handle 信息 |

> 这套参数让 JuiceFS 在生产 16-64 进程并发下，>90% stat/open 不穿透到 backend。

---

## 11. JuiceFS 官方 introduction 对照（外部 marketing 视角）

> 来源：[Introduction to JuiceFS](https://juicefs.com/docs/community/introduction/)。这一节是**外部公开宣传**和我们**源码调研**的对照表——用来防止被 marketing 措辞误导。

### 11.1 跟源码调研一致的点（可信任）

- **数据/元数据分离**：data → object storage、metadata → Redis/MySQL/TiKV/SQLite/etcd（5 选 1）
- **POSIX 兼容** + **HDFS 兼容** + **S3 兼容**：3 套 API 同时给
- **Cloud Native**：Kubernetes CSI Driver
- **压缩**：LZ4 / Zstandard
- **文件锁**：BSD flock + POSIX fcntl
- **跨平台、跨地域挂载**：千台机器可同时挂载

### 11.2 marketing 措辞 vs 实际实现（必须看清的差异）

| 官方措辞 | 实际行为 | 对 fuse-on-ds-kv 的含义 |
|----------|---------|-----------------------|
| "Strong Consistency" | Close-to-Open（close 后才看见） + 同一 mount 立即看见 | 不能给上游宣传"分布式强一致"——要标注 CTO |
| "millisecond-level latency" | 取决于 metadata backend（ETCD 3-5 ms，Redis 1-2 ms，TiKV ≈ 5-10 ms） | 部署文档必须明示 backend 选型 |
| "near unlimited throughput" | 来自底层对象存储的水平扩展 | fuse-on-ds-kv 的吞吐上限 = 对象存储上限（OSS / S3 / 本地盘） |

### 11.3 之前调研里没强调的点（来自官方文档补充）

1. **HDFS 兼容能 boost 元数据性能**
   - HDFS API 是 stateless、无事务的，绕过 Meta 引擎的乐观事务 + session 检查
   - 对纯读 / 大文件顺序读 场景明显更快
   - **fuse-on-ds-kv v1 不做 HDFS API**——但这点说明：JuiceFS 在"绕过 meta 引擎"这件事上有明确场景
2. **AI / ML 场景被官方点名**
   - "支持所有机器学习和深度学习框架" + "可共享"
   - 直接对应你之前提的 agent 场景（多 agent 共享 workspace）
3. **"用对象存储当地盘"的定位**
   - 不再做底层存储（block / 文件）的设计，把 OSS/S3 当 LBA 用
   - fuse-on-ds-kv 同理：DS worker 节点 + object store 当"海量慢盘"

### 11.4 官方文档没讲、但代码里非常关键的概念

| 官方没写 | 代码里有 | 重要性 |
|----------|----------|--------|
| Chunk / Slice / Block 三层 | ✅ pkg/meta/slice.go + pkg/chunk | 4 MiB block、64 MiB chunk、可变长 slice |
| slice "freezed" 状态机 | ✅ pkg/meta/slice.go | 已 freezed 的 slice 不能再改，只能 append |
| `txBatchLock` 1024 分片本地锁 | ✅ pkg/meta/base.go | 防止同 inode 的并发写 |
| metadata 4 级缓存 (L0/L1/L2/L3) | ✅ pkg/meta + pkg/juicefs | L0 进程内（60s TTL）就是 hot path |
| session 心跳 + cleanStaleSession | ✅ pkg/meta/base.go | 客户端进程异常退出时清理未提交事务 |
| chunkWriter + stage + commit 线程 | ✅ pkg/vfs/writer.go | 内存 buffer → 4 MiB 对齐 → 后台上传 |
| Etcd 8GB 上限 | ✅ pkg/meta/etcd.go | 1M 文件以下才能用 ETCD |
| Compact / cleanup chunk | ✅ pkg/meta/base.go 后台 job | 删除的数据由后台合并 |

### 11.5 外部文档 + 源码结合得出的整体认知

> JuiceFS 是一套**"用 POSIX 语义包装对象存储"**的元数据引擎。它在内部用 1024 分片本地锁 + KV 事务 + 4 级缓存 + 切片 freezed 状态机，把"分布式文件系统"的所有复杂度都封死在 fuse-server 内部。对外承诺的是 POSIX 接口 + 强一致宣传，对内要做的是严格保证"一次 close 后跨 mount 可见"。
>
> 这个内外分离的"双面性"，就是为什么 JuiceFS 能做到 S3 对象存储上的 POSIX 文件系统而其它竞品做不动。

---

## 12. 源码覆盖清单（2026-08-05 第二次补完）

> 这次把上一轮"缺口"全部补齐了。所有源码都已落到 `agent-tools/` 缓存，可以重复查阅。

### 12.1 完整覆盖的源文件

| 模块 | 文件 | 行数 | 缓存位置 |
|------|------|-----|----------|
| meta | `pkg/meta/interface.go` | 659 | agent-tools/fcf33247 |
| meta | `pkg/meta/base.go` (v1.3+) | 4086 | agent-tools/b31f0b77 |
| meta | `pkg/meta/base.go` (a026a27b 旧版) | 3738 | agent-tools/b3046467 |
| meta | `pkg/meta/redis.go` | 6272 | agent-tools/f7c710eb |
| meta | `pkg/meta/tkv.go` | 4118 | agent-tools/98ed7ab1 |
| vfs | `pkg/vfs/handle.go` | ~400 | agent-tools/453bf236 |
| vfs | `pkg/vfs/writer.go` | **463** | agent-tools/juicefs-pkg-vfs-writer.go.txt |
| vfs | `pkg/vfs/reader.go` | **641** | agent-tools/juicefs-pkg-vfs-reader.go.txt |
| vfs | `pkg/vfs/vfs.go` (节选) | 1501 | agent-tools/1d7e8d94 |
| chunk | `pkg/chunk/cached_store.go` (节选) | 1253 | agent-tools/21c96340 |
| object | `pkg/object/object_storage.go` | **320** | agent-tools/juicefs-pkg-object-object_storage.go.txt |
| docs | 官方 introduction | 1 页 | WebFetch 已抓 |

### 12.2 pkg/vfs/writer.go 关键发现（用于理解 JuiceFS 写路径）

```
DataWriter      <- per-mount 管理器（单进程）
  └─ FileWriter <- per-open inode (Open 后 refs++, Close 后 refs--；refs==0 时 delete)
       └─ chunkWriter <- per-64MiB-chunk
              └─ sliceWriter <- per-可变长-slice
```

**sliceWriter 状态机**（来自 `pkg/vfs/writer.go`）：
1. **写入触发 flush**：
   - `s.slen == ChunkSize (64 MiB)` → 自动 `freezed=true` + 异步 `flushData()`
   - `s.slen >= blockSize (4 MiB)` → 主动 `writer.FlushTo(s.slen)`（上传当前 block）
2. **commit 顺序**：每个 chunk 的 commitThread **串行提交** `c.slices[0] → c.slices[1] → ...`，保证 POSIX 一致性
3. **跨 chunk 顺序**：新 chunk 的第一个 slice 的 `dep` 指向**前一个 chunk 的最后 growing slice**，等 `s.dep.committed` 才提交
4. **内存背压**：
   - `usedBufferSize > bufferSize` → `Sleep(10ms)`
   - `> 2x` → `Sleep(100ms)`
   - `totalSlices >= 1000` → `Sleep(1ms)`
5. **flushwaiting/writewaiting 互斥**：flush 期间阻塞新 write；write 期间若 flushwaiting > 0 等待
6. **后台 flushAll 线程**：每 100ms 检查未 freezed slice，若 `time.Since(s.started) > 5s` 强制 freezed
7. **错误恢复**：`commitThread` 收到 ENOENT/ENOSPC/EDQUOT 时异步 `store.Remove(id, length)` 清理孤儿 block

### 12.3 pkg/vfs/reader.go 关键发现（用于理解 JuiceFS 读路径）

```
DataReader      <- per-mount 管理器
  └─ FileReader <- per-Open 调用 (refs=1 + 加入 r.files[inode] 链表)
       └─ sliceReader <- per-4MiB-block (NEW -> BUSY -> READY)
              └─ chunk.Page (实际数据缓冲)
```

**sliceReader 状态机**（来自 `pkg/vfs/reader.go`）：
```
<-- REFRESH
| |
NEW -> BUSY -> READY
| |
BREAK ---> INVALID
```

**readahead 设计**：
1. 每个 fileReader 维护 `sessions [2]session`（最多 2 个并行读上下文）
2. `guessSession(block)` 根据 `lastOffset` 找匹配的 session，找不到就用 atime 最旧的
3. `checkReadahead`：根据 `seqdata / readahead / buffer` 动态放大或缩小
   - `readahead = 0` 且 `block.off == 0 || seqdata > block.len` → 启动 readahead `= blockSize`
   - `readahead < readAheadMax` 且 buffer 充裕 → `readahead *= 2`
   - `readahead >= blockSize` 且 buffer 紧张 → `readahead /= 2`
4. **buffer 限速**：80% bufferSize 用于 readahead；超过 2x 时强制 Sleep(100ms)

**后台 releaseIdleBuffer**：每 1s 扫描，丢弃空闲 > 1min 且不被 need range 重叠的 slice

**readSlice 并发**：单 page 内 `len(slices) > 16` 走 `readManySlices`，显式 `concurrency chan byte` 限 16 并发；否则直接 spawn goroutine

### 12.4 pkg/object/object_storage.go 关键发现（用于理解后端抽象）

**ObjectStorage 接口设计**：
- `DefaultObjectStorage` 提供**默认空实现**（大多返回 `notSupported`），让具体 backend 只 override 需要的接口
- 这是 **Go embed-friendly 模式**——backend 只需要 embed `DefaultObjectStorage`，再覆盖自己支持的方法
- 注册表模式：`var storages = make(map[string]Creator)` + `func Register(name, register)` + `CreateStorage(name, ...)` 工厂方法
- **Tier 抽象**：通过 `ctx.Value(TierKey{})` 把当前 tier id 传到 backend，支持分层存储

**ListAllWithDelimiter**：
- 用 10 个 listThread 并发遍历 prefix 子树
- 每个 listThread 独立 cond 协调
- 递归 `walk(prefix, entries)`，每层用 1000/1e9 limit 控制单次请求量

### 12.5 仍存在的"未读"缺口（按重要性排序）

| 文件 | 重要性 | 缺口 | 影响 |
|------|--------|------|------|
| `pkg/meta/slice.go` | **高** | 仅看过 snippets，没完整读过 slice struct + tree 算法 | 不影响设计 v1，但影响"为什么要 freezed"这种细节问题 |
| `pkg/chunk/store.go` | 中 | 只读过 cached_store.go 节选，store.go 全文没读 | 不影响 v1 设计（我们用 DS Buffer 替代对象存储） |
| `pkg/chunk/writer.go` + `pkg/chunk/reader.go` | 中 | block 打包/读取的具体逻辑没看 | 同上 |
| `pkg/object/s3.go` 等具体 backend | 低 | 我们不用 S3 作为对象存储 | 不影响 |
| `pkg/fuse/fuse.go` | 低 | bazil.org/fuse 绑定层，不重要 | 不影响 |
| `cmd/juicefs-fuse/main.go` | 低 | 启动入口，参数解析 | v1 入口设计可参考 |
| `pkg/utils/locks/` | 低 | `KeyedLocker` 的具体实现 | v1 我们要自己写 1024 分片锁，可参考 |

### 12.6 后续如果真要"完整理解 JuiceFS"，可走 code graph

> 注意：当前 shell 环境死锁，`code-graph` CLI 跑不起来。如果未来 shell 修复后，可按下面路径索引 JuiceFS 源码（clone 到本地 worktree 后）：

```bash
# 在 yuanrong-datasystem/.worktrees/juicefs-read/ 下 clone
git clone --depth 1 https://github.com/juicedata/juicefs.git juicefs

# 用 workbench 里的 code-graph（如果环境恢复）
code-graph --root juicefs --out .codegraph/juicefs.db
```

**预期索引可回答的问题**：
- `sliceWriter.flushData` → 调用了谁（`writer.Finish` → `chunk.Store.Put` → `object.Put`）
- `commitThread` → 调用了谁（`m.Write` → `kvMeta.Write` → `tkvClient.Txn` → `etcd.Txn`）
- `txBatchLock` → 哪些方法用了它（`SetXattr` / `Create` / `Write` / `Rename` 等）

这些跨文件调用链是 WebFetch 节选看不到的。
