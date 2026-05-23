# 写入路径端到端故障分析

## 当前架构: Pull-on-Read 副本，不是 Push-on-Write

### 写入路径 (Client → Worker)

```
Client → Worker-A: Create RPC (gRPC)  → SHM 分配
Client → Worker-A: MemoryCopy         → 数据写入 SHM
Client → Worker-A: Publish RPC (gRPC) → Worker-A 执行:
  ├── PublishObjectWithLock()
  │   ├── PrepareForPublish()          → 设置 ObjectEntry
  │   └── PublishObject()
  │       ├── RequestingToMaster()    → CreateMeta RPC → Master
  │       │   └── Master: CreateMetaFirstTime()
  │       │       ├── metaTable_.insert(key, meta)
  │       │       ├── locations[address] = AckState::ACK
  │       │       └── objectStore_->CreateOrUpdateMeta() → RocksDB
  │       ├── SaveBinaryObjectToMemory() → 数据在 SHM
  │       └── SetPrimaryCopy(true), SetCacheInvalid(false)
```

**关键发现: 数据只写到一个 Worker。没有跨 Worker 复制。**

### 读取路径 (Worker → Worker, 这才是 URMA 发生的地方)

```
Worker-A (需要数据) → Worker-B (有数据): GetObjectRemote RPC
  Worker-B → Worker-A: 数据通过 URMA/UCP/TCP 发送
  优先级: URMA Write → UCP Put → TCP fallback
```

**FLAGS_enable_data_replication**: Worker-A 拉取数据后，如果 flag=true，向 Master 注册为副本位置 (CreateCopyMeta)。
这是**按需拉取**副本，不是写入时推送。

---

## 五种关键故障场景

### F1: Client→Worker 写入路径上的 Publish 失败

```
Client → Worker-A: Publish(key=X, data)
Worker-A → Master: CreateMeta(key=X)  ← 这一步失败
```

**可能原因**: Master 不可达、网络超时、RocksDB 写入失败
**后果**:
- Worker-A 上: `safeObj->FreeResources()` 被调用 (SHM 释放)
- Master: 不知道 key=X (如果在内存表中插入之前失败)
- Client: 收到 `K_RPC_DEADLINE_EXCEEDED` 或 `K_RUNTIME_ERROR`
- **可重试**: Create 之后 Publish 之前，SHM 已被分配但不可见。后台 `ReconcileShmRef` 会清理孤立 SHM

### F2: URMA Write 成功但 Master 更新失败

```
Worker-B: UrmaWritePayload() → 数据成功到达 Worker-A
Worker-B → Master: CreateCopyMeta()  ← 失败
```

**后果**: 数据存在于 Worker-A 内存，但 Master 不知道此位置
- Worker-A 上的数据是**孤儿**：对其他客户端不可见
- 没有自动恢复机制
- 如果 FLAGS_enable_data_replication=false，Worker-A 上的副本是临时的 (SetNeedToDelete=true)

### F3: Worker-A 在 Publish 后但 L2 持久化前崩溃

```
Worker-A: PublishObject() {
  RequestingToMaster() → OK (Master 已更新)
  SaveBinaryObjectToPersistence() → 进行中... ← CRASH
}
```

**后果**:
- Master: 元数据存在 (primary_address=Worker-A)
- Worker-A 的 SHM: 随进程丢失
- **Client Get 会失败**: Master 指向 Worker-A，但 Worker-A 已崩溃
- 恢复依赖 Master 的 TTL/eviction 或 Worker 重启后的恢复

### F4: 2PC MultiPublish 阶段 1 成功后阶段 2 失败

```
Worker-A → Master1,2,3: CreateMetaPhaseOne() → PreCommit (PENDING, 60s TTL) ✓
Worker-A → Master1,2,3: CreateMetaPhaseTwo() → 部分失败 (只有 M2 收到) ✗
```

**后果**:
- M1,M3 上: multiSetState=PENDING，60 秒 TTL
- M2 上: multiSetState=IDLE, version=V
- 不一致窗口 = 60 秒 (TTL 过期后 PENDING 可被覆盖)
- 在此期间，其他发布者收到 `K_TRY_AGAIN`

### F5: Worker-B URMA 写入 Worker-A 中途崩溃

```
Worker-B: UrmaWriteImpl() {
  PostJettyRw(chunk1) ✓
  PostJettyRw(chunk2)  ← Worker-B 崩溃
}
```

**后果**:
- Worker-A 上: URMA 轮询线程收到 `URMA_CR_WR_FLUSH_ERR_DONE`
- Worker-A 内存中可能有 ch1 的部分数据 (不是事务性的)
- 如果有 TCP 回退 (`TrackUrmaFallbackTcp`): 数据通过 TCP 重新发送 → 完整性保证
- 如果无回退: 调用者得到 `K_URMA_ERROR`

---

## 对多副本设计的启示

### 当前架构的限制

| 问题 | 后果 | 多副本修复 |
|------|------|----------|
| 写入只到单 Worker | Worker 崩溃 → 数据丢失 | **Push-on-write 复制到 N 个 Worker** |
| Pull-on-read 副本 | 副本只在第一次读时创建 | 写入时同步创建副本 |
| 没有写入 Quorum | CreateMeta 单 Master | N/2+1 Quorum 确认 |
| 2PC PENDING 60s 窗口 | 不一致窗口太长 | 缩短到 5s 或立即提交 |
| URMA 部分写入不事务化 | 数据可能损坏 | 写入前 CRC + 写入后验证 |

### 需要新增的语义

1. **Push-on-Write 复制**:
   ```
   Worker-A (Primary): PublishObject()
     ├── 写入本地 SHM ✓
     ├── ReplicateToBackup(Worker-B, data) ← NEW
     ├── ReplicateToBackup(Worker-C, data) ← NEW
     ├── 等待 Quorum ACK (N/2+1)
     └── Master: CreateMeta(key, locations=[A,B,C])
   ```

2. **故障切换语义**:
   - 如果 Primary 在复制到 B 之后但在复制到 C 之前崩溃:
     - B 有数据，C 没有
     - Master: location A(PRIMARY, failed), B(BACKUP, seqno=N), C(无)
     - 恢复: B Promote to PRIMARY，C 从 B 恢复

3. **元数据一致性保证**:
   - 写入 Quorum 确认后才在 Master 注册
   - Master 更新在 RocksDB 持久化后才返回 (已有)
   - Client 缓存 ReplicaSetPb + 版本号 (新增)

### 需要修改的 StatusCode

| 场景 | 新增 Code |
|------|----------|
| 复制到 Quorum 失败 | `K_OC_REPLICA_QUORUM_FAILED` |
| 特定副本不可用 | `K_OC_REPLICA_NOT_AVAILABLE` |
| 副本版本不匹配 | `K_OC_REPLICA_VERSION_MISMATCH` |
| 副本写入超时 | `K_OC_REPLICA_WRITE_TIMEOUT` |
