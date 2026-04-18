# 2026-04-19 Worker 共享内存 OOM 问题定位

- **现象**：`kv2-jingpai-1` worker 的 `resource.log` 在 100 秒内 `shm.memUsage` 从 3.58 GB 涨到 37.5 GB（`rate=0.999`）触发 OOM；同期 `OBJECT_COUNT` 从 438 降到 37。
- **核心怀疑**：
  1. TTL 未生效。
  2. Object 已删除但底层 buffer 没释放。
  3. 内存泄漏。
- **结论（先放）**：#2 直接成立 → #3 是 #2 累积；#1 是 #2 造成的"看起来像 TTL 没删干净"的表象，TTL 路径本身在跑、但被 `MasterAsyncTask` 池排队拖慢，且即使删完元数据也释放不了 shm。

---

## 1. 日志字段对齐（按 `res_metrics.def` 顺序）

资源日志字段定义在 `yuanrong-datasystem/src/datasystem/common/metrics/res_metrics.def`，注册在 `worker_oc_server.cpp:993-1031`：

```
SHARED_MEMORY                 = memUsage / physMemUsage / totalLimit / rate / scMemUsage / scMemLimit
SPILL_HARD_DISK               = spaceUsage / physSpaceUsage / totalLimit / rate
ACTIVE_CLIENT_COUNT           = clientNum
OBJECT_COUNT                  = objectTable_->GetSize()           ← 元数据条目数
OBJECT_SIZE                   = objectMemoryStats_->Usage()       ← 累积分配字节数
WORKER_OC_SERVICE_THREAD_POOL = idle/cur/max/waiting/rate
WORKER_WORKER_OC_SERVICE_THREAD_POOL
MASTER_WORKER_OC_SERVICE_THREAD_POOL
MASTER_OC_SERVICE_THREAD_POOL
ETCD_QUEUE                    = current/limit/rate
ETCD_REQUEST_SUCCESS_RATE
OBS_REQUEST_SUCCESS_RATE
MASTER_ASYNC_TASKS_THREAD_POOL = idle/cur/max/waiting/rate        ← TTL/批量删除走这里
STREAM_COUNT
WORKER_SC_SERVICE_THREAD_POOL ... MASTER_SC_SERVICE_THREAD_POOL
STREAM_REMOTE_SEND_SUCCESS_RATE
SHARED_DISK
SC_LOCAL_CACHE
OC_HIT_NUM                    = memHit/diskHit/l2Hit/remoteHit/miss
```

关键采集源：

- `OBJECT_COUNT`：`WorkerOCServiceImpl::GetTotalObjectCount() = objectTable_->GetSize()`（`worker_oc_service_impl.h:532-535`）。
- `OBJECT_SIZE`：`GetTotalObjectSize() = stat.objectMemoryUsage = objectMemoryStats_->Usage()`（`worker_oc_service_impl.cpp:397-402` + `allocator.cpp:411`）。
- `SHARED_MEMORY`：`Allocator::GetMemoryStatistics()`（`allocator.h:331-344`），`memUsage` 是 `objectMemoryStats_->RealUsage() + streamMemoryStats_->RealUsage()`。
- `OBJECT_SIZE` / `objectMemoryStats_->Usage()` 只在 `Allocator::FreeMemory` 里 `stats->SubUsage(bytesFree)` 时减（`allocator.cpp:369`）。

## 2. 现场指纹

| 时间 | objCount | shm.memUsage / OBJECT_SIZE | 平均对象大小 |
|---|---|---|---|
| 23:16:01 | 438 | 3.58 GB | ~8 MB |
| 23:16:11 | 360 | 4.35 GB | ~12 MB |
| 23:16:21 | 311 | 8.65 GB | ~28 MB |
| 23:16:31 | 309 | 13.37 GB | ~43 MB |
| 23:16:41 | 307 | 18.07 GB | ~59 MB |
| 23:16:51 | 305 | 22.79 GB | ~75 MB |
| 23:17:01 | 306 | 27.49 GB | ~90 MB |
| 23:17:11 | 311 | 32.17 GB | ~104 MB |
| 23:17:21 | 111 | 36.75 GB | ~331 MB |
| 23:17:31 | **37** | **37.50 GB** | **~1.01 GB** |
| 23:17:41 | 51 | 37.50 GB | ~735 MB |

强信号：

1. **`OBJECT_COUNT` 与 `OBJECT_SIZE` 反向**：元数据条目持续被擦掉（`objectTable_->Erase` 在跑），但 `objectMemoryStats_->Usage()` 单调上涨 → `Allocator::FreeMemory` 没被调用 → 物理 shm 没还。
2. **首拍 `MASTER_ASYNC_TASKS_THREAD_POOL = 0/5/5/10/1.000`**：`MasterAsyncTask` 池满载 + 10 个排队任务，TTL 异步删除控制面已经堵了。后续该字段为空（仅 master 本地侧才输出，本节点切到 worker-only 视图）。
3. **`OC_HIT_NUM = 8601→14749 / miss=15`**：miss 几乎不增 → 不是 LRU 失效压力，问题在释放链。
4. **写入速率约 +5 GB / 10s ≈ 500 MB/s 持续**，最终 `rate=0.999` 打满 `totalLimit=37.58 GB`。

## 2.1 测试负载确认（关键修订）

业务侧 `set_usr_pin_8m` 的写循环（用户提供）：

```cpp
SetParam param;
param.writeMode = WriteMode::NONE_L2_CACHE;
param.ttlSecond = ttlSecond;

for (int i = 0; i < value_num_cw; i++) {
    string key = base_key + "_" + to_string(i);
    int value_size = random_int_jd(0, 100);
    if (value_size == 1) {  // 约 1% 走 300KB 单值 Set
        string value_base = utils::RandomData().randomString(300 * 1024);
        ...
        client->Set(key, value, param);
    } else {                // 约 99% 走 8MB user-pin / zero-copy 路径
        value = key + "|" + value_8m;
        shared_ptr<Buffer> buffer;
        client->Create(key, value.size(), param, buffer);
        buffer->MemoryCopy(value.data(), value.size());
        client->Set(buffer);
    }
}
```

要点：

- **绝大多数对象是 8 MB**，单对象 `> 1 MB = batchLimitSingleSize`（见 `obj_cache_shm_unit.cpp:283`），**不会进入 `AggregateAllocate` / `ShmOwner` 聚合分配**。所以原文档第 3.2 节里 (b) `ShmOwner` 整批锁死的假设对当前 case **不是主因**——它只解释偶发的 300KB 分支。
- 8 MB 对象走的是 worker `WorkerOcServiceCreateImpl::Create` → `memoryRefTable_->AddShmUnit(clientId, shmUnit, ...)` 这条**显式 user-pin** 路径（`worker_oc_service_create_impl.cpp:102-104`）。Worker 端 shm 是否能释放，**完全取决于这条 ref 何时被拿掉**。
- 两个 `value_num_cw` 写完后只做 `redis_lpush_JD(base_key)`，**没有**调用 `buffer->InvalidateBuffer()`、也**没有**显式 `Delete`。释放 100% 依赖 `shared_ptr<Buffer> buffer` 在循环作用域结束时析构。
- `param.ttlSecond` 被设上了，所以业务侧期望"即便忘记 Delete，TTL 到点也会释放 shm"——但下面会看到，**TTL 路径不会清 `memoryRefTable_`**。

把这条信息加进 OBJECT_COUNT/OBJECT_SIZE 反向曲线一起看：8 MB 单对象 + 增量 ~5 GB / 10s ⇒ 每 10 秒约 640 个 8 MB 对象写入，但 `OBJECT_COUNT` 几乎不变（说明删除/TTL 也在跑）。`OBJECT_SIZE` 单调上涨意味着这些 shm **块都还挂在 `memoryRefTable_` 上**，元数据被 `objectTable_->Erase` 拿掉了，物理 shm 没有 `Allocator::FreeMemory`。

## 3. 代码路径与三个怀疑点的对应

### 3.1 TTL 未生效（症状，不是根因）

`worker_oc_service_expire_impl.cpp:58-121`：worker 端 `Expire` 仅把 `ttl_second` 透传给对应 master，本地不做任何释放。

```77:101:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_expire_impl.cpp
    uint32_t ttlSeconds = req.ttl_second();
    std::vector<std::future<Status>> futures;
    std::string traceID = Trace::Instance().GetTraceID();
    Status rc;
    size_t threadNum = std::min<size_t>(objKeysGrpByMaster.size(), FLAGS_rpc_thread_num);
    auto batchExpireThreadPool_ = std::make_unique<ThreadPool>(1, threadNum, "BatchExpireMeta");
    futures.reserve(objKeysGrpByMaster.size());
    for (auto &item : objKeysGrpByMaster) {
        futures.emplace_back(batchExpireThreadPool_->Submit([&, item, timer]() {
            ...
            rc = ExpireFromMaster(currentIds, workerAddr, ttlSeconds, absentObjectKeys, objKeysExpireFailed, rsp);
```

真正的"到点删除"在 master：`master/object_cache/expired_object_manager.cpp:318-341` 的 `Run()` 单线程扫描 `timedObj_`，到期就把任务交给 `MasterAsyncTask` 线程池跑 `AsyncDelete → NotifyDeleteAndClearMeta → 通知 worker → DeleteCopyNotification → ClearObject`。

TTL 看起来"不生效"的常见路径：

- **TTL=0 是"不改 TTL"，不是"立即过期"**：

  ```140:148:yuanrong-datasystem/src/datasystem/master/object_cache/expired_object_manager.cpp
  Status ExpiredObjectManager::InsertObject(const std::string &objectKey, const uint64_t version,
                                            const uint32_t ttlSecond, bool acceptZero)
  {
      // If objectKey in timeObj_ and we insert the same objectKey again, it means the object is being updated.
      // If ttl is not zero, we should remove the object key and old expire time, and insert with new expire time again.
      if (!acceptZero && ttlSecond == 0) {
          return Status::OK();
      }
  ```

- **过期时间相对 createTime，不是相对当前时间**：

  ```119:126:yuanrong-datasystem/src/datasystem/master/object_cache/expired_object_manager.cpp
  uint64_t ExpiredObjectManager::CalcExpireTime(const uint64_t version, const uint64_t ttlSecond) const
  {
      if (UINT64_MAX / (ttlSecond * TIME_UNIT_CONVERSION * TIME_UNIT_CONVERSION) < 1) {
          return UINT64_MAX;
      }
      uint64_t ttlUs = ttlSecond * TIME_UNIT_CONVERSION * TIME_UNIT_CONVERSION;
      return (UINT64_MAX - ttlUs < version) ? UINT64_MAX : (version + ttlUs);
  }
  ```

  后置 `Expire` 时若 `createTime` 已经很旧，业务以为"还有 N 秒"会是错觉。

- **删除失败的指数退避**：`AddFailedObject` 用 `RETRY_WAIT_TIME * failures + 1` 秒重排（`expired_object_manager.cpp:216-235`），失败次数越多重试越慢。
- **`MasterAsyncTask` 池堵塞**：日志第 1 条 `0/5/5/10/1.000` 是直接证据。
- **关键**：即便 `ClearMeta` 成功、worker `ClearObject` 成功，也只是 `objectTable_->Erase` + `evictionManager_->Erase`，**不会**立刻 `Allocator::FreeMemory`，所以业务侧观察"OBJECT_COUNT 下来了，shm 没下来"，会得出"TTL 删了又像没删"的错觉。

### 3.2 Object 删除 ≠ buffer 释放（直接成立）

所有删除路径汇合在 `ClearObject`：

```258:285:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_crud_common_api.cpp
Status WorkerOcServiceCrudCommonApi::ClearObject(ObjectKV &objectKV)
{
    ...
    INJECT_POINT("worker.ClearObject.BeforeErase");
    RETURN_IF_NOT_OK_APPEND_MSG(objectTable_->Erase(objectKey, entry),
                                FormatString("Failed to erase object %s from object table", objectKey));
    evictionManager_->Erase(objectKey);
    return Status::OK();
}
```

这里**没有显式 `entry->FreeResources()`**。eviction 的 `Action::DELETE` 也明确这么写：

```232:235:yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_eviction_manager.cpp
        // No need to call FreeResources as destructor will free the resources.
        RETURN_IF_NOT_OK(objectTable_->Erase(objectKey, entry));
        point.Record();
        VLOG(1) << FormatString("[ObjectKey %s] Object delete success", objectKey);
```

注释**乐观地假设** `entry` 是 `shared_ptr<ObjectInterface>` 的最后持有者。下面三处会同时持有 `shared_ptr<ShmUnit>`，造成 `~ShmUnit → FreeMemory → Allocator::FreeMemory` 链断掉：

#### (a) `SharedMemoryRefTable` 钉住的客户端引用

写路径在 worker 上把 `shared_ptr<ShmUnit>` 入表：

```102:104:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_create_impl.cpp
    IndexUuidGenerator(shmIdCounter.fetch_add(1), shmUnitId);
    shmUnit->id = ShmKey::Intern(shmUnitId);
    memoryRefTable_->AddShmUnit(clientId, shmUnit, requestTimeoutMs);
```

`MultiCreate` 同样：`worker_oc_service_create_impl.cpp:205-208` 走 `AddShmUnits`。

只有三种方式能拿掉：
- `MultiPublish` 的 `auto_release_memory_ref` 分支（`worker_oc_service_impl.cpp:436-448`）；客户端没设这个 flag、或者 key 落在 `failedSet` 里被 skip，引用就留下来。
- 客户端显式 `DecreaseMemoryRef` / `RemoveShmUnit`（`worker_oc_service_impl.cpp:1410-1422`）。
- 客户端断连后 `RemoveClient(clientId)`（`worker_oc_service_impl.cpp:1156`），依赖心跳/断连回调可靠触发。

→ 任一情况 `memoryRefTable_` 还有 entry，`objectTable_->Erase` 之后 `ShmUnit` 析构不会触发，`Allocator::FreeMemory` 不会调，`OBJECT_SIZE` 与 `shm.memUsage` 都不下降。**当前日志的形态正是如此。**

#### (b) `ShmOwner` 聚合分配 —— 一格不释放，一整批不释放（仅对 < 1 MB 的对象生效）

> **本 case 不是主因**：业务对象 8 MB > `batchLimitSingleSize = 1 MB`，不进聚合路径。仅在 `set_usr_pin_8m` 中约 1% 走 300KB `client->Set(key, value, param)` 的小对象分支会触发；写入量占比小，可作为次要因素。

`AggregateAllocate`（`obj_cache_shm_unit.cpp:273-330`）按 `oc_worker_aggregate_merge_size`（默认 2 MB，见 `worker_oc_server.cpp:171`）把 < 1 MB 的对象打包到一块 `ShmOwner` 上，每个对象的 `ShmUnit` 通过 `DistributeMemory` 切片：

```109:127:yuanrong-datasystem/src/datasystem/common/shared_memory/shm_unit.cpp
Status ShmOwner::DistributeMemory(uint64_t shmSize, ShmUnit &shmUnit)
{
    ...
    shmUnit.shmOwner_ = shared_from_this();
    return Status::OK();
}
```

切片释放的实现：

```69:93:yuanrong-datasystem/src/datasystem/common/shared_memory/shm_unit.cpp
Status ShmUnit::FreeMemory()
{
    RETURN_OK_IF_TRUE(pointer == nullptr);
    // If shm owner exists, the memory will be freed together at shmOwner destruction.
    if (shmOwner_) {
        shmOwner_.reset();
        return Status::OK();
    }
    ...
    return datasystem::memory::Allocator::Instance()->FreeMemory(tenantId_, pointer, serviceType_, cacheType_);
}
```

→ **同一 batch 中只要有一个 slice 仍被持有**（来自 (a) 的 `memoryRefTable_`、未消费完的 GetRequest、URMA pipeline 等），整块 `ShmOwner` 都释放不了。这能解释"平均对象大小"从 8 MB 一路涨到 1.01 GB（37.5 GB / 37）。

#### (c) Get/Read 路径上正在飞的 `shared_ptr<ShmUnit>`

`GetShmUnit` 在 zero-copy 读流程中持有 `shared_ptr<ShmUnit>`：

```1147:1153:yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_service_impl.cpp
    memoryRefTable_->GetClientRefIds(clientId, shmIds);
    for (const auto &shmId : shmIds) {
        std::shared_ptr<ShmUnit> shmUnit;
        auto stat = memoryRefTable_->GetShmUnit(shmId, shmUnit);
        if (stat.IsOk()) {
            TryUnlatch(shmUnit->pointer, lockId);
        }
```

客户端长尾 read、读端 latch 没还、或 `TryUnlatch`/`TryUnShmQueueLatch` 没有走到（client 中途超时、worker 进入 reconciliation），引用都会延后释放。

#### (d) 单 `Publish` 路径不带 `auto_release_memory_ref`（**当前 case 的主因**）

`KVClient::Set(buffer)` → `KVClientImpl::Set(buffer)` → `Buffer::Publish()` → `ObjectClientImpl::Publish` → `ClientWorkerRemoteApi::Publish` → worker 的 `WorkerOCServiceImpl::Publish`。这条链上有两个**不对称**：

**对称表**（与 `MultiPublish` 对比）：

| 阶段 | 单 `Publish` | `MultiPublish` |
|---|---|---|
| client 构造 req | `PreparePublishReq`，**不设** `auto_release_memory_ref` | `client_worker_remote_api.cpp:413` `req.set_auto_release_memory_ref(!bufferInfo[0]->shmId.Empty())` |
| worker 处理 | `worker_oc_service_impl.cpp:409-422`，**没有** `DecreaseMemoryRef` 分支 | `worker_oc_service_impl.cpp:436-448`，成功时 `DecreaseMemoryRef(clientId, shmIds)` |
| client 端 buffer 状态 | `Buffer::Publish` 仅 `SetVisibility(true)`，`isReleased_` 仍为 false（`buffer.cpp:248-249`） | 走 `HandleShmRefCountAfterMultiPublish` 同步标记 |

```cpp
// yuanrong-datasystem/src/datasystem/client/object_cache/client_worker_api/client_worker_base_api.cpp:39-68
Status ClientWorkerBaseApi::PreparePublishReq(...)
{
    ...
    req.set_data_size(bufferInfo->dataSize);
    req.set_write_mode(...);
    req.set_consistency_type(...);
    req.set_cache_type(...);
    req.set_is_seal(isSeal);
    req.set_shm_id(bufferInfo->shmId);
    return Status::OK();   // ← 没有 set_auto_release_memory_ref
}
```

```cpp
// yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_service_impl.cpp:409-422
Status WorkerOCServiceImpl::Publish(const PublishReqPb &req, PublishRspPb &resp, std::vector<RpcMessage> payloads)
{
    ...
    Status rc = publishProc_->Publish(req, resp, payloads);
    if (rc.IsOk()) {
        METRIC_ADD(metrics::KvMetricId::WORKER_FROM_CLIENT_TOTAL_BYTES, payloadBytes);
        UpdateWorkerObjectGauge(objectTable_);
    }
    return rc;             // ← 没有 DecreaseMemoryRef 分支
}
```

**结果**：8 MB 单对象 `Set(buffer)` 成功之后，worker 端 `memoryRefTable_` 上的 `(clientId, shmId, shared_ptr<ShmUnit>)` **不会**被自动拿掉。释放完全靠 client 那侧 `~Buffer → Release → DecreaseReferenceCnt` 这条**异步链**：

```cpp
// yuanrong-datasystem/src/datasystem/common/object_cache/buffer.cpp:188-191
Buffer::~Buffer()
{
    Release();
}
```

```156:186:yuanrong-datasystem/src/datasystem/common/object_cache/buffer.cpp
void Buffer::Release(object_cache::ObjectClientImpl *clientPtr)
{
    ...
    do {
        if (isReleased_) {
            break;
        }
        if (clientPtr) {
            clientPtr->DecreaseReferenceCnt(bufferInfo_->shmId, isShm_, bufferInfo_->version);
            break;
        }
        auto clientImpl = clientImpl_.lock();
        if (clientImpl != nullptr) {
            clientImpl->DecreaseReferenceCnt(bufferInfo_->shmId, isShm_, bufferInfo_->version);
        }
    } while (false);
    ...
}
```

```1492:1526:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
void ObjectClientImpl::DecreaseReferenceCnt(const ShmKey &shmId, bool isShm, uint32_t version)
{
    std::shared_lock<std::shared_timed_mutex> lck(shutdownMux_);
    if (asyncReleasePool_ == nullptr || shmId.Empty()) {
        return;
    }
    bool async = true;
    INJECT_POINT("client.DecreaseReferenceCnt", [&async](bool value) { async = value; });
    if (async) {
        asyncReleasePool_->Execute([this, shmId, isShm, version] {
            LOG_IF_ERROR(DecreaseReferenceCntImpl(shmId, isShm, version), "DecreaseReferenceCntImpl failed");
        });
    } else {
        LOG_IF_ERROR(DecreaseReferenceCntImpl(shmId, isShm, version), "DecreaseReferenceCntImpl failed");
    }
}

Status ObjectClientImpl::DecreaseReferenceCntImpl(const ShmKey &shmId, bool isShm, uint32_t version)
{
    bool needDecreaseWorkerRef = memoryRefCount_.DecreaseRef(shmId);
    ...
    if (!needDecreaseWorkerRef) {
        return Status::OK();
    }
    if (isShm && !IsBufferAlive(version)) {
        return Status::OK();   // ← 连接死了 / 版本不匹配 直接 return，不发 RPC
    }
    RETURN_IF_NOT_OK(CheckConnection());
    ...
    RETURN_IF_NOT_OK_PRINT_ERROR_MSG(workerApi_[LOCAL_WORKER]->DecreaseShmRef(shmId, checkFunc, shutdownMux_),
                                     "DecreaseShmRef failed.");
    return Status::OK();
}
```

```1270:1273:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
bool ObjectClientImpl::IsBufferAlive(uint32_t version)
{
    return CheckConnection().IsOk() && GetWorkerVersion() == version;
}
```

这条链上有 **5 个实战易踩坑点**，任意一个生效就会让 worker 端 ref 永久泄漏：

1. **异步释放池只有 1 个线程**：`asyncReleasePool_ = std::make_shared<ThreadPool>(0, 1, "async_release_buffer")`（`object_client_impl.cpp:344`）。业务线程高频 `Create + MemoryCopy + Set + ~Buffer`，单线程释放队列**必然滞后**于产生速率，worker 端 shm 一直被钉着；这也直接解释了"对象数下降，shm 持续上涨"。
2. **`IsBufferAlive` 静默吞掉 RPC**：`CheckConnection` 失败、或 `GetWorkerVersion() != version`（worker 重启过、reconciliation 后 version 跳变），`DecreaseReferenceCntImpl` **直接 return，不会发 `DecreaseShmRef`**。这一段没有补偿/告警，是典型的"越死的 client 越泄漏"。
3. **客户端进程在异步释放完成前退出**：`~ObjectClientImpl` 在 `object_client_impl.cpp:244-251` 把 `asyncReleasePool_ = nullptr`。如果 shutdown 时还有未投递/未跑完的 `Execute` 回调，要么被 drop、要么因为 `asyncReleasePool_` 已 null 而进入 `if (asyncReleasePool_ == nullptr) return`。
4. **client 自定义 `clientId`、跨进程复用 shmId**：`memoryRefCount_.DecreaseRef(shmId)` 是 client 本地引用计数，必须归 0 才发 RPC。如果业务用 dup buffer / 子句柄等扩展玩法导致 client 本地 ref 永远不为 0，worker 端永远收不到 release。
5. **TTL 删除**不感知 `memoryRefTable_`：master 通过 `DeleteCopyNotification` 让 worker 走 `ClearObject`，仅 `objectTable_->Erase + evictionManager_->Erase`，**完全没碰** `memoryRefTable_`。所以业务期望"忘记 release，TTL 兜底"是错觉——TTL 只能删元数据，shm 仍卡在 ref 表上（这正是用户怀疑 #1 的来源）。

**对照官方测试模式**：`tests/st/client/object_cache/object_client_test.cpp:397-398` 的标准用法是 `Publish() → InvalidateBuffer()` 配对调用。`InvalidateBuffer` 是同步 RPC，但它清理的是 worker 端**对象元数据 / 写句柄**，**也不直接清 `memoryRefTable_`** —— 释放仍然要靠 `Buffer` 析构走 (d) 的异步链。所以即使加上 `InvalidateBuffer`，前面 5 个坑仍然存在；它能减小窗口、避免一些状态机错乱，但不是消除泄漏的根本手段。

### 3.3 被动缩容 + Client 切远端 worker（强放大器）

测试侧反馈"当前存在 worker 被动缩容、client 切远端 worker"。这正好踩中 (a) + (d) 中所有缝隙的最坏组合。完整链路：

#### Step A：worker 被动缩容触发 client 隔离

被动缩容流程（参见 `src/datasystem/worker/cluster_manager/etcd_cluster_manager.cpp` 与 `vibe-coding-files/docs/observable/06-dependencies/etcd.md`）：

- `t = node_timeout_s`：etcd lease 到期，**故障隔离生效**（`CheckConnection` 开始 `IsTimedOut()`、客户端心跳失败）。
- `t = node_dead_timeout_s`：master 写 `del_node_info`，节点 SIGKILL / 自杀。
- 中间窗口（`node_dead_timeout_s − node_timeout_s`）worker 进程**通常仍在跑**（可能在做 voluntary scale-down 的迁数据，或仅是 etcd 失联，进程内 `memoryRefTable_` 上的 `shared_ptr<ShmUnit>` 都还在）。

#### Step B：Client 心跳失败 → `ProcessWorkerLost` → `ProcessWorkerTimeout`

```500:511:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
void ObjectClientImpl::ProcessWorkerTimeout()
{
    if (clientStateManager_->GetState() & (uint16_t)ClientState::EXITED) {
        return;
    }
    auto &workerApi = workerApi_[LOCAL_WORKER];
    (void)workerApi->CleanUpForDecreaseShmRefAfterWorkerLost();
    mmapManager_->CleanInvalidMmapTable();
    // Only shm object would record reference count, and they are
    // unrecoverable after timeout until worker reconnects, so clear them directly.
    memoryRefCount_.Clear();
}
```

注释一句话直接承认了泄漏：**"unrecoverable after timeout until worker reconnects, so clear them directly"**。client 把本地 `memoryRefCount_` 全部清空，对原 LOCAL_WORKER 不再发任何 `DecreaseShmRef`。

`RediscoverLocalWorker`（`object_client_impl.cpp:728-731`）在 ip 切换/重发现的成功路径里也是同样动作：`CleanUpForDecreaseShmRefAfterWorkerLost` + `CleanInvalidMmapTable` + `memoryRefCount_.Clear()`。

#### Step C：`SwitchToStandbyWorkerImpl` 切到远端 standby

```588:654:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
bool ObjectClientImpl::SwitchToStandbyWorkerImpl(const std::shared_ptr<IClientWorkerApi> &currentApi, WorkerNode next)
{
    ...
    workerApi_[next] = currentApi->CloneWith(standbyWorker, ...);
    workerApi_[next]->isUseStandbyWorker_ = true;
    Status rc = workerApi_[next]->Init(...);
    ...
    listenWorker_[next] = std::make_unique<client::ListenWorker>(workerApi_[next], heartbeatType, next, asyncSwitchWorkerPool_.get());
    ...
    currentNode_ = next;
    ...
}
```

切换过程**只**做了：
- 与 standby worker 建立新连接（新的 clientId 注册、新的 ListenWorker、新的心跳）。
- `currentNode_ = STANDBY1_WORKER` / `STANDBY2_WORKER`。

切换过程**完全没有**：
- 通知原 LOCAL_WORKER "我切走了，请清掉我留下的 `memoryRefTable_` 项"。
- 对 STANDBY 推送 LOCAL_WORKER 上残留的 shmId / 业务对象元数据（shm 是物理隔离的，根本推不过去）。

#### Step D：业务侧旧 `Buffer` 析构时已无路可走

回到 §3.2 (d) 的释放链：

```1509:1525:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
Status ObjectClientImpl::DecreaseReferenceCntImpl(const ShmKey &shmId, bool isShm, uint32_t version)
{
    bool needDecreaseWorkerRef = memoryRefCount_.DecreaseRef(shmId);
    ...
    if (!needDecreaseWorkerRef) {
        return Status::OK();        // ← 本地表已 Clear()，DecreaseRef 必然返回 false 直接 return
    }
    if (isShm && !IsBufferAlive(version)) {
        return Status::OK();        // ← 即便绕过上面，IsBufferAlive 用 LOCAL_WORKER 的 version 校验也已失效
    }
    ...
    RETURN_IF_NOT_OK_PRINT_ERROR_MSG(workerApi_[LOCAL_WORKER]->DecreaseShmRef(shmId, checkFunc, shutdownMux_),
                                     "DecreaseShmRef failed.");   // ← 写死 LOCAL_WORKER，发不到 STANDBY
    return Status::OK();
}
```

- `memoryRefCount_` 已被 `Clear()`，`DecreaseRef(shmId)` 必返回 false → 直接 `return Status::OK()`，**完全不发 RPC**。
- 即便强行让本地表保留一份"待补偿"列表，`workerApi_[LOCAL_WORKER]->DecreaseShmRef(...)` 是**写死 LOCAL_WORKER**，发往 standby 也无意义（standby 上不存在 LOCAL_WORKER 的 shmId / clientId entry）。
- `IsBufferAlive` 检查 `CheckConnection().IsOk() && GetWorkerVersion() == version`。LOCAL_WORKER 死了 / 重启过 / 重发现成新地址，version 跳变，`IsBufferAlive == false`，再次拦掉。

#### Step E：原 LOCAL_WORKER 进程内的 `memoryRefTable_` 何时被清？

只有以下几种情况会清掉，且**都不是必然发生**：

1. **`SIGKILL` 后进程退出** —— 进程级共享内存（mmap）随进程析构释放，但：
   - 缩容窗口期（`node_dead_timeout_s − node_timeout_s`，默认 300s vs 60s = 240s）内 worker 仍跑，shm 一直占着。
   - 如果配置成 voluntary 优先（`VoluntaryToPassiveScaleDown`，见 `tests/st/worker/object_cache/slot_end2end_test.cpp:1136`），voluntary 阶段慢，泄漏窗口可达分钟级。
2. **Worker 端检测到 client 心跳超时 → 触发 `RemoveClient`**：依赖 `client_dead_timeout_s` / `node_timeout_s` 这条独立超时链，且要求 client 真的"死"了——但当前场景 client 没死，只是切到了另一个 worker，原 worker 一侧的心跳判定可能更晚甚至卡在 `ListenWorker::CleanUpForDecreaseShmRefAfterWorkerLost` 已唤醒但 RPC 队列已 wake 的状态。
3. **`ReconcileShmRef`（`worker_oc_service_impl.cpp:1456-1482`）**：依赖 client 主动发 reconcile 请求，且 client 端 `RecordMaybeExpiredShm` / `FlushMaybeExpiredQueue` 触发条件较严格；切到 standby 后客户端的 reconcile 线程仍指向 `currentNode_ = STANDBY`，**不会回头给 LOCAL_WORKER 发 reconcile**。

#### Step F：master 侧 TTL 同样救不了

即便 master 端 `ExpiredObjectManager` 在窗口期把 LOCAL_WORKER 上对应的 object 元数据通过 `DeleteCopyNotification` 发过去，worker 走 `ClearObject` —— 仍然是 §3.2 (d) 末尾说的：**`ClearObject` 不感知 `memoryRefTable_`**。元数据从 `objectTable_` 拿掉，物理 shm 还挂在 ref 表上。`OBJECT_COUNT` 掉了但 `OBJECT_SIZE` 不掉，正好对应日志现象。

#### 影响放大与触发率

- 在被动缩容期间，业务侧通常会**叠加 retry**：`Create + Set(buffer)` 失败 → 重发 → 新 shm 在 standby 分配 + 新 ref；如果重试又失败再切回 LOCAL_WORKER，又压一波 ref。
- 写循环里的 `shared_ptr<Buffer>` 在切换瞬间析构，正好命中 D 的全部三道关卡 → **整批 shm 100% 漏在原 LOCAL_WORKER**。
- 用户场景的 8 MB user-pin 写：每秒数百 buffer × 240s 窗口 ≈ 数千个 8 MB shm 泄漏；与日志里看到的 "37 个对象 × 平均 1 GB" 这种"假象"高度匹配——OBJECT_COUNT 是当前 worker 的，OBJECT_SIZE 是 process 视角的物理 shm，差额几乎全部来自切走前留下的孤儿 ref。

#### 一句话定性

> 被动缩容 + 切 standby 是**当前 case 的真正放大器**：3.2 (d) 的释放链在切换瞬间被 `memoryRefCount_.Clear()` 主动 + `IsBufferAlive` 拦截 + `workerApi_[LOCAL_WORKER]` 写死三连击全部断开，旧 worker 上 8MB user-pin 的 `memoryRefTable_` 项**完全无人回收**，只能等进程被 SIGKILL 才会随 mmap 释放，这段窗口正好是被动缩容设计上必然存在的（`node_dead_timeout_s − node_timeout_s`）。

### 3.4 切到远端 workerB 后 **workerB 自己** OOM（接管端的另一组机制）

测试反馈：workerA 故障，A 上的 client 切到 workerB，**workerB 出现 OOM**（不是 workerA）。3.3 解释的是"workerA 上残留的 user-pin shm 漏掉"，这条只解释**原 worker** 的 shm 不下降；**接管端 workerB 的 OOM 必须是另一个机制**。把 client 端切换路径与 worker 端接管路径对齐后能分出 5 条候选，按可能性排序：

#### (i) Client 切到 standby 后**所有写都退化为 non-shm RPC payload 直发**（最大放大器）

```326:330:yuanrong-datasystem/src/datasystem/client/client_worker_common_api.cpp
    CloseSocketFd();

    shmEnableType_ = isUseStandbyWorker_ ? ShmEnableType::NONE : shmEnableType;
    socketFd_ = socketFd;
```

切到 standby 时直接把 `shmEnableType_` 写成 `NONE`，于是 `IsShmEnable() == false`，`ShmCreateable(size) = IsShmEnable() && size >= shmThreshold_ == false`（`client_worker_common_api.h:75-78`）。

```1307:1376:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
Status ObjectClientImpl::Create(const std::string &objectKey, uint64_t dataSize, ...)
{
    ...
    if (workerApi->ShmCreateable(dataSize) || IsUrmaEnabled()) {
        ...                                       // 切走前：worker 上分配 shm + client mmap，user-pin
    } else {
        // 切走后：client 本地 malloc 8MB，bufferInfo->shmId 留空
    }
```

后面 `KVClient::Set(buffer)` → `Buffer::Publish` → `ClientWorkerRemoteApi::Publish` 发现 `isShm == false` 时直接把 8 MB 数据塞进 RPC payload：

```360:395:yuanrong-datasystem/src/datasystem/client/object_cache/client_worker_api/client_worker_remote_api.cpp
    std::vector<MemView> payloads;
    if (!isShm && !bufferInfo->ubDataSentByMemoryCopy) {
        payloads.emplace_back(bufferInfo->pointer, bufferInfo->dataSize);
    }
    ...
    Status s = stub_->Publish(opts, req, rsp, payloads);
```

workerB 收到后走 `WorkerOcServicePublishImpl::Publish` → `SaveBinaryObjectToMemory`：

```403:442:yuanrong-datasystem/src/datasystem/worker/object_cache/obj_cache_shm_unit.cpp
Status SaveBinaryObjectToMemory(ObjectKV &objectKV, const std::vector<RpcMessage> &payloads, ...)
{
    ...
    if (szChanged) {
        auto shmUnit = std::make_shared<ShmUnit>();
        RETURN_IF_NOT_OK(AllocateMemoryForObject(objectKey, payloadSz, metaSz, false, evictionManager, *shmUnit, ...));
        ...
        entry->SetShmUnit(shmUnit);
    }
    ...
    Status status = entry->GetShmUnit()->MemoryCopy(payloadData, threadPool, metaSz);
```

后果：

- workerB 上**每个 8 MB 写都是 worker 自分配 shm + RPC payload memcpy**，瞬时内存峰值 ≈ 2× 8 MB（payload buffer + shm，反序列化窗口内并存）。
- 这条路径 **不会** 把 shmUnit 加进 `memoryRefTable_`（不是 user-pin），所以**正常情况下** 3.2 (d) 的 ref 泄漏在 workerB 上不存在。但 workerB 必须靠自己的 eviction / TTL / Delete 把这些对象自洽地清掉——而下面 (iv) 揭示这条自洽链在当前 case 下是断的。
- ZMQ payload 高速持续输入会让 `WORKER_OC_SERVICE_THREAD_POOL`（即 `WorkerOCService` 线程池）满载，日志里 `8/8/16/1/0.000` 已经出现 1 个 waiting；切走后 workerB 实际还要承接原来 workerA 的全部业务流量，瞬时 N 倍。

#### (ii) Retry + 多 client 集中切 → workerB 瞬时 N 倍负载

`ClientWorkerRemoteApi::Publish` 的 `RetryOnError` 集合允许 `K_SCALING` / `K_OUT_OF_MEMORY` / `K_RPC_UNAVAILABLE` 等重试（`vibe-coding-files/docs/observable/workbook/sheet1-call-chain.md:117`）。切换瞬间业务侧的 Publish 大概率失败 → retry：

- 每次 retry 都会把同一份 8 MB payload **重发一次**到 workerB，workerB 端按 `bool szChanged` 决定是否复用旧 ShmUnit；如果中间发生过 `ClearObject` / version 跳变，重新分配；
- workerA 上原本由 N 个 client 分担的写入，故障后**同一时刻**全部砸到同一台 standby workerB（standby 是 worker 注册时 master 选出来的，集群里 standby 通常 1～2 台），瞬时负载 N×；
- 业务侧的 `set_usr_pin_8m` 是 tight loop，循环里没有限流。

→ 落在 workerB 上的实际写入速率 ≫ 单 worker 设计容量；shm 容量在切换瞬间被打满。

#### (iii) `WriteMode::NONE_L2_CACHE` → eviction 只能 DELETE，不能 SPILL

业务侧 `param.writeMode = WriteMode::NONE_L2_CACHE`（看用户代码）。`WorkerOcEvictionManager::EvictObject` 的几种 `Action`：

- `Action::SPILL` —— 需要支持 L2 / 共享磁盘，`NONE_L2_CACHE` 模式下不会被选；
- `Action::FREE_MEMORY` / `Action::END_LIFE` —— 仅特定生命周期，不是高水位时的主路径；
- `Action::DELETE` —— 主路径，但**必须**先通过 `IsObjectEvictable`（`globalRefCount==0` 且未被 reserve）才能选中。

→ 一旦哪些对象被外部 pin 住（见下面 iv），workerB 的 eviction 就退化成"只能删未 pin 的、前两秒刚写进来的对象"，跟不上写入速率，shm 单调上涨。

#### (iv) `RecoveryClient` 把 client 的 `globalRefCount_` 全量重放到 workerB → 接管对象不可 evict

切 standby 时 client 重新 `RegisterClient` 时携带 extension（`req.extend()`）。worker 端 `WorkerServiceImpl::RegisterClient`（`worker_service_impl.cpp:284-287`）在 `remainClient` 时调 `ProcessServerReboot`：

```1396:1402:yuanrong-datasystem/src/datasystem/worker/worker_oc_server.cpp
Status WorkerOCServer::ProcessServerReboot(const ClientKey &clientId, ...)
{
    RETURN_OK_IF_TRUE(!EnableOCService());
    return objCacheClientWorkerSvc_->RecoveryClient(clientId, tenantId, reqToken, msg);
}
```

```1608:1633:yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_service_impl.cpp
Status WorkerOCServiceImpl::RecoveryClient(const ClientKey &clientId, ...)
{
    ...
    for (const ::google::protobuf::Any &ext : req) {
        if (ext.Is<GRefRecoveryPb>()) {
            GRefRecoveryPb gRefInfos;
            ext.UnpackTo(&gRefInfos);
            ...
            RETURN_IF_NOT_OK(globalRefTable_->GIncreaseRef(clientId, objectKeys, failedIncIds, firstIncIds));
        }
    }
```

效果：client 切到 workerB 时，把它本地 `globalRefCount_` 里所有对象（之前从 workerA / 其它 worker get 出来过的对象）**一次性在 workerB 上 GIncreaseRef**。这些对象在 workerB 上 `globalRefCount > 0`，**不可被 eviction 命中**，直到 client 显式 `g_decrease_ref` 或断连后 `ReleaseGRefs`。

> 用户的 `set_usr_pin_8m` 写循环本身没显式调 `g_increase_ref`，但**业务的其他线程或下游消费方（脚本里 `redis_lpush_JD(base_key)` 之后的消费者）**只要做过 `Get` 并启用 keep / 全局引用，就会把这些对象 push 进 `globalRefCount_`。所以这条要确认业务的 g_increase_ref 用法，但很可能命中。

#### (v) Slot recovery / 接管对象 preload 占用一部分 workerB shm

如果集群启用 distributed master + slot 接管，workerB 接管 workerA 的 slot 时会触发 preload（从其他副本拉对象数据到本机）。社区有一个直接命名为 `RecoveryPreloadOomKeepsReceiverData` 的测试（`tests/st/worker/object_cache/slot_end2end_test.cpp:1199-1202`）：

```1199:1202:yuanrong-datasystem/tests/st/worker/object_cache/slot_end2end_test.cpp
TEST_F(SlotEndToEndPassiveScaleDownTest, RecoveryPreloadOomKeepsReceiverData)
{
    LOG(INFO) << "Scenario: worker1 is near high water, worker0 fails, and slot recovery preload should stop "
                 "without evicting worker1's own WRITE_BACK_L2_CACHE_EVICT data.";
```

该路径的守卫在 `slot_recovery_manager.cpp:194-204` 的 `CheckPreloadMemoryAvailable`：

```194:204:yuanrong-datasystem/src/datasystem/worker/object_cache/slot_recovery/slot_recovery_manager.cpp
Status CheckPreloadMemoryAvailable(const SlotPreloadMeta &meta)
{
    const auto estimatedSize = EstimatePreloadMemory(meta.size);
    const auto availableSize = memory::Allocator::Instance()->GetMemoryAvailToHighWater();
    CHECK_FAIL_RETURN_STATUS_PRINT_ERROR(
        estimatedSize <= availableSize, K_OUT_OF_MEMORY,
        FormatString("[ObjectKey %s] Slot recovery preload memory exceeds high water. data_size=%lu, "
                     "estimated_size=%lu, available_to_high_water=%lu",
                     meta.objectKey, meta.size, estimatedSize, availableSize));
    return Status::OK();
}
```

守卫**只挡"接下来要拉的"，对正在 in-flight 的 preload 没法刹车**。当业务实时写入与 preload 并发时，两条 shm 申请来源没共享配额：业务写 (i) + preload 在窗口期累计就能跨过高水位。

#### 5 条机制的相互作用

```
切到 workerB 瞬间:
    (i)  client 强制 non-shm payload  ─┐
    (ii) 多 client 集中 + retry 翻倍   ─┼─→ workerB 写入速率瞬时 ×N
                                       │
    (iv) RecoveryClient 重放 GIncRef ─→ 部分对象不可 evict
    (v)  slot preload 抢占 high water ─→ 留给业务的余量缩水
                                       │
    (iii)WriteMode=NONE_L2_CACHE      ─┘ eviction 只能 DELETE，跟不上 (i)+(ii)
                       ↓
                  workerB OOM
```

#### 验证日志关键字（建议在 workerB 端 grep）

| 检查点 | 日志 / 字段 | 含义 |
|---|---|---|
| (i) | `WORKER_OC_SERVICE_THREAD_POOL` 的 `waiting` 持续 > 0；access 日志里大量 `DS_OBJECT_POSIX` Publish 带 `payload_size≈8MB` | non-shm payload 路径在跑 |
| (ii) | client 端 access 日志里同 objectKey 的 Publish 反复出现 + `K_SCALING/K_OUT_OF_MEMORY/K_RPC_UNAVAILABLE` | retry 在叠加 |
| (iii) | worker INFO `EvictionList size before evict:` 持续大；同时 `AllocateMemoryForObject` 出现 `K_OUT_OF_MEMORY` retry | eviction 跟不上 |
| (iv) | `RecoveryClient` 日志 + 对应 `GIncreaseRef` 大批量；之后 `IsObjectEvictable return false` | 重放 ref 把对象钉住 |
| (v) | `Slot recovery preload memory exceeds high water` | preload 守卫触发，说明 preload 与业务写在抢容量 |

#### 验证手段

1. **临时降级业务到 MSet**：把 `client->Set(buffer)` 改 `client->MSet({buffer})`。MSet 会让 worker 端走聚合 publish 与 `auto_release_memory_ref`，并降低 ZMQ payload 调用频率（一次 RPC 多对象），缓解 (i)。
2. **业务侧主动限流**：感知到 `K_SCALING` / `K_OUT_OF_MEMORY` 时不要立刻 retry，加 backoff；缓解 (ii)。
3. **改写 mode 看趋势**：临时把 `WriteMode` 从 `NONE_L2_CACHE` 改成 `WRITE_BACK_L2_CACHE_EVICT`，观察 workerB shm 是否能稳定（让 SPILL 路径打开）；判定 (iii)。
4. **日志统计 g_increase_ref / g_decrease_ref 调用对**：确认业务是否有泄漏的 globalRefCount_，覆盖 (iv)。
5. **观测 `MEMORY_USAGE − OBJECT_SIZE`**：在 workerB 上**这两个值应该同步增长同步下降**（不像 workerA 上是反向）。如果出现 `MEMORY_USAGE` 大幅高于 `OBJECT_SIZE`，说明也有 ref 表 / preload buffer 没归账。

### 3.4.1 接管端独立漏洞：`MultiCreate` 在 non-shm 模式下加 ref 不释放

这是一个**独立于 3.4 (i)–(v) 的真实代码漏洞**，由测试反馈"client 切到远端 workerB 是否 Create 加了 ref 但没 decrease"反向追溯出来。触发条件：

1. client 端 `IsShmEnable() == false`（典型来源：切到 standby、跨节点连接）；
2. 业务调用 `MSet(... MSetParam{.existence=ExistenceOpt::NX} ...)` 或任何 `MultiCreate(skipCheckExistence=false)`；
3. （或 `URMA` 启用且 client `!ClientShmEnabled(clientId)`，参见 `worker_oc_service_create_impl.cpp:115-119`）。

#### 漏点链（5 处缝隙）

| # | 位置 | 行为 |
|---|---|---|
| ① | `object_client_impl.cpp:1399-1408` | `canUseShm=false` 但 `!skipCheckExistence` → **仍发 MultiCreate RPC** |
| ② | `worker_oc_service_create_impl.cpp:155-208` | worker **不感知 client shm 状态**，对每个不存在的 key `AllocateMemoryForObject + AddShmUnits` 一并执行 |
| ③ | `client_worker_base_api.cpp:368-398` | `IsShmEnable()=false` → `useShmTransfer=false` → `PostMultiCreate` **直接 return，丢弃 worker 返回的 shm_id** |
| ④ | `client_worker_remote_api.cpp:413` + `worker_oc_service_impl.cpp:436-448` | client 后续 `MSet`：`bufferInfo.shmId.Empty()` → `auto_release_memory_ref=false` → worker 不走 `DecreaseMemoryRef` |
| ⑤ | `buffer.cpp:188-191` + `object_client_impl.cpp:1495-1497` | `~Buffer` → `DecreaseReferenceCnt` 看到 `shmId.Empty()` 直接 return |

#### 完整漏出链路

```
client 切 standby (IsShmEnable=false)
    ↓
业务 KVClient::MSet(... ExistenceOpt::NX ...) / MCreate
    ↓
ObjectClientImpl::MultiCreate(skipCheckExistence=false)
    ↓ canUseShm=false 但 !skipCheckExistence → 仍发 RPC
ClientWorkerRemoteApi::MultiCreate → workerB
    ↓
WorkerOcServiceCreateImpl::MultiCreateImpl
    ├─ AllocateMemoryForObject(8MB)         ← 真分配 shm
    ├─ shmUnit->id = ShmKey::Intern(shmUnitId)
    └─ memoryRefTable_->AddShmUnits(...)     ← 真加 ref
    ↓ 返回 shm_id 给 client
client 端 PostMultiCreate
    ↓ useShmTransfer=false → return（丢弃 shm_id）   ← 第一次失踪
    ↓
业务 client->MSet(buffers)（buffer 是 client 本地 malloc，shmId 为空）
    ↓ auto_release_memory_ref=false
worker 端 MultiPublish 不走 DecreaseMemoryRef        ← 第二次失踪
    ↓
SaveBinaryObjectToMemory 又分配一份 shm（business object）
    ↓
~Buffer → DecreaseReferenceCnt 看到 shmId.Empty() return  ← 第三次失踪
        ↓
workerB 上 memoryRefTable_ 永久挂第一次 MultiCreate 分配的 8MB shm
直到 client RemoveClient 或 worker 重启
```

#### 三个失踪点合并的后果

- 同一 objectKey 在 workerB 上**实际占用 2 份 shm**：第一份是 MultiCreate 分配的孤儿（永久泄漏），第二份是 MultiPublish 的 `SaveBinaryObjectToMemory` 分配的业务数据；
- workerB 的 `OBJECT_SIZE` 看似只统计了一份（业务那份），但 `MEMORY_USAGE` 反映的是物理总占用——可能出现 `MEMORY_USAGE ≈ 2 × OBJECT_SIZE` 的诡异比；
- 比 (3.2 d) 严重的是：**没有任何路径**能让 client 重新拿到第一份 shmId（`PostMultiCreate` 已经丢了），所以连主动补救都做不到，只能靠 worker 端 `RemoveClient`/`ReconcileShmRef` 兜底。

#### 触发概率确认

- **`SetParam`（单 Set）默认 `existence=NONE`**（`include/datasystem/kv_client.h:52`），不命中；
- **`MSetParam` 的 `existence` 没有默认值**（`include/datasystem/kv_client.h:60`：`ExistenceOpt existence; // There is not default value`），业务必须显式传，且 `MSetNx` 语义上一定传 `NX` —— **常见业务路径必命中**；
- 用户当前 `set_usr_pin_8m` 用的是单 `Create`，**当前测试本身不命中**；但如果业务有任何 `MSet(... NX ...)` 路径同时切到 standby，这条漏洞会和 3.4 (i)–(v) 叠加。

#### 修复点（合并到 §5）

加进 §5 的新增条目：

> **P1''** — `WorkerOcServiceCreateImpl::MultiCreateImpl` 的 `createMeta` 内，在 `AllocateMemoryForObject` 之前先 `if (!ClientShmEnabled(clientId) && !IsUrmaEnabled()) continue;`，让 worker 端 non-shm 路径走"只 check existence、不分配 shm"模式（如同 3.4 (i) 里说的，client 后续是 RPC payload 直发，shm 会在 `Publish` 阶段重新分配）。
>
> 配套：`MultiCreateRspPb` 中的 `shm_id` 在 non-shm 模式下不再下发；client 端 `PostMultiCreate` 当前丢弃逻辑保持。

### 3.4.2 USE_URMA 模式下的 ref 不对称释放（**强烈疑似命中**当前测试）

**适用条件**：
- 编译期 `USE_URMA` 启用，运行时 `IsUrmaEnabled() == true`；
- client 端 `IsShmEnable() == false`（典型来源：跨节点 / 切到 standby / shm 通道不可用）；
- 业务用单 `Create + MemoryCopy + Set(buffer)`（即用户的 `set_usr_pin_8m` 主路径）。

注意：这条比 3.4.1 严重，因为它直接命中**单 Create**，不需要业务用 NX MSet。

#### 完整路径与 7 个关键节点

```text
Step 1  client Create  ──┐
Step 2  worker AddShmUnit│
Step 3  Buffer::Init     │  ──→ isShm_ = false（看到 ubUrmaDataInfo）
Step 4  client Publish   │
Step 5  worker RemoveShmUnit  ──→ worker 端 ref ✓ 被清掉
Step 6  client isReleased_ = true
Step 7  ~Buffer → Release：if (isReleased_) break  ──→ client 本地 ref 不 Decrease ❌
```

##### Step 1：Client `Create` 进入"shm/urma 共用"分支

```1322:1344:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
    if (workerApi->ShmCreateable(dataSize) || IsUrmaEnabled()) {
        uint64_t metadataSize = 0;
        auto shmBuf = std::make_shared<ShmUnitInfo>();
        std::shared_ptr<UrmaRemoteAddrPb> urmaDataInfo = nullptr;
        RETURN_IF_NOT_OK(
            workerApi->Create(objectKey, dataSize, version, metadataSize, shmBuf, urmaDataInfo, param.cacheType));
        std::shared_ptr<ObjectBufferInfo> bufferInfo = nullptr;
        std::shared_ptr<client::IMmapTableEntry> mmapEntry = nullptr;
        if (!urmaDataInfo) {
            ...                                 // 普通 shm 路径
        } else {
            bufferInfo = MakeObjectBufferInfo(objectKey, nullptr, dataSize, 0, param, false, version, shmBuf->id);
        }
        bufferInfo->ubUrmaDataInfo = urmaDataInfo;
        memoryRefCount_.IncreaseRef(shmBuf->id);     // ← client 本地 ref +1
        RETURN_IF_NOT_OK(Buffer::CreateBuffer(std::move(bufferInfo), shared_from_this(), newBuffer));
    }
```

`ShmCreateable=false` 但 `IsUrmaEnabled()=true` → 仍进入 `if`，**发 Create RPC 给 workerB**，并 `memoryRefCount_.IncreaseRef(shmBuf->id)`。

##### Step 2：Worker 端 `CreateImpl` 真分配 + AddShmUnit + 填 URMA info

```95:119:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_create_impl.cpp
auto shmUnit = std::make_shared<ShmUnit>();
auto metadataSize = GetMetadataSize();
RETURN_IF_NOT_OK_PRINT_ERROR_MSG(
    AllocateMemoryForObject(objectKey, dataSize, metadataSize, true, evictionManager_, *shmUnit, cacheType),
    "worker allocate memory failed");

std::string shmUnitId;
IndexUuidGenerator(shmIdCounter.fetch_add(1), shmUnitId);
shmUnit->id = ShmKey::Intern(shmUnitId);
memoryRefTable_->AddShmUnit(clientId, shmUnit, requestTimeoutMs);   // ← worker 端 ref +1

resp.set_store_fd(shmUnit->GetFd());
...
resp.set_shm_id(shmUnit->GetId());
resp.set_metadata_size(metadataSize);

#ifdef USE_URMA
if (!ClientShmEnabled(clientId) && IsUrmaEnabled()) {
    RETURN_IF_NOT_OK(
        FillRequestUrmaInfo(localAddress_, shmUnit->GetPointer(), shmUnit->GetOffset(), metadataSize, resp));
}
#endif
```

##### Step 3：`Buffer::Init` 因 `ubUrmaDataInfo` 而 `isShm_ = false`

```65:84:yuanrong-datasystem/src/datasystem/common/object_cache/buffer.cpp
    // Special check for Remote H2D or client UB.
    // ... Or if the urma info exists, then the data is in the direct worker's shm.
    if (bufferInfo_->remoteHostInfo != nullptr || bufferInfo_->ubUrmaDataInfo != nullptr) {
        bufferInfo_->pointer = nullptr;
        isShm_ = false;                               // ← 这里
        latch_ = std::make_shared<object_cache::CommonLock>();
    } else if (bufferInfo_->pointer == nullptr ...) {
        ...
    } else {
        isShm_ = true;
        ...
    }
```

##### Step 4 + 5：`Buffer::Publish` 走 non-shm RPC，worker 端按 URMA 配对清 ref

```226:255:yuanrong-datasystem/src/datasystem/common/object_cache/buffer.cpp
Status Buffer::Publish(const std::unordered_set<std::string> &nestedKeys)
{
    ...
    if (bufferInfo_->ubUrmaDataInfo && !bufferInfo_->ubDataSentByMemoryCopy) {
        ...                                  // 没拷过就 SendBufferViaUb
    }
    Status status = clientImplSharedPtr->Publish(bufferInfo_, nestedKeys, isShm_);
    if (isShm_) {
        SetVisibility(status.IsOk());
    } else {
        // worker already release shmUnit for this case.
        isReleased_ = !bufferInfo_->shmId.Empty() && status.IsOk();   // ← 标记 client 端不再发 Decrease
    }
    return status;
}
```

worker 端在 `WorkerOcServicePublishImpl::PublishImpl` 末尾会按"client 不带 shm 但带 shm_id"的特征自动清理 ref：

```368:377:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_publish_impl.cpp
    Status rc =
        RetryWhenDeadlock([this, &namespaceUri, &req, &clientId, &shmUnitId, &nestedObjectKeys, &payloads, &future] {
            return PublishObjectWithLock(namespaceUri, req, clientId, shmUnitId, nestedObjectKeys, payloads, future);
        });

    // If worker diable shared-memory transfer but the request still carries shmUnitId, client-to-worker data transfer
    // is through UB; we need clean memoryRefTable to avoid memory leak.
    if (!shmUnitId.Empty() && !(ShmEnable() && ClientShmEnabled(clientId))) {
        memoryRefTable_->RemoveShmUnit(clientId, shmUnitId);
    }
```

→ 在**正常 Publish 成功路径**下，worker 端 ref 是**会被清掉的**（注释也明确写了 `"worker already release shmUnit for this case"`）。

##### Step 6 + 7：Client `~Buffer` 因 `isReleased_=true` 跳过 Decrease

```168:180:yuanrong-datasystem/src/datasystem/common/object_cache/buffer.cpp
    do {
        if (isReleased_) {
            break;                            // ← URMA 路径下这里 break，下面的 Decrease 不跑
        }
        if (clientPtr) {
            clientPtr->DecreaseReferenceCnt(bufferInfo_->shmId, isShm_, bufferInfo_->version);
            break;
        }
        auto clientImpl = clientImpl_.lock();
        if (clientImpl != nullptr) {
            clientImpl->DecreaseReferenceCnt(bufferInfo_->shmId, isShm_, bufferInfo_->version);
        }
    } while (false);
```

→ client 端 `memoryRefCount_` 上的 +1（Step 1 加的）**永远不被 Decrease**。

#### 三个失踪点总结

| # | 位置 | 行为 |
|---|---|---|
| (a) | client Step 1 `IncreaseRef` ↔ Step 7 跳过 Decrease | **client 本地 `memoryRefCount_` 永久 +1**（不会 leak shm，但会污染本地表） |
| (b) | worker Step 5 在 `PublishImpl` 内部清 ref，**早期失败路径不覆盖** | 如果 `Authenticate` / `CheckShmUnitByTenantId` 失败，line 375-377 不会执行 → worker 端 ref **真泄漏** |
| (c) | client Step 4 RPC 失败 ↔ `isReleased_=false` ↔ `DecreaseReferenceCntImpl` | 走兜底分支时 `workerApi_[LOCAL_WORKER]->DecreaseShmRef(...)` **写死 LOCAL_WORKER**；切到 standby 后 `LOCAL_WORKER` 是已死/失联的 workerA，DecreaseShmRef 发到错误目的地，workerB 上 ref 永久漏 |

#### (a) 后果：连锁污染后续的 Get 路径

虽然 `memoryRefCount_` 残留本身不直接占 worker shm，但它会**永久阻止后续同 shmId 的 Get 路径释放 worker ref**：

```1509:1519:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
Status ObjectClientImpl::DecreaseReferenceCntImpl(const ShmKey &shmId, bool isShm, uint32_t version)
{
    bool needDecreaseWorkerRef = memoryRefCount_.DecreaseRef(shmId);
    VLOG(1) << ...;
    if (!needDecreaseWorkerRef) {
        return Status::OK();
    }
    ...
}
```

`ClientMemoryRefTable::DecreaseRef` 是简单计数：必须减到 0 才返回 true。Step 1 残留的 +1 会让所有后续 Get 路径加的 ref 减不到 0：

- 业务 `client->Get(key)` 拿同一 objectKey → worker 端 GetRequest 路径再次 `AddShmUnit` → worker ref +1；
- client 端 `memoryRefCount_.IncreaseRef(shmId)` → 本地 ref 从 1 → 2；
- 业务释放 Get buffer → `~Buffer → DecreaseReferenceCnt` → `memoryRefCount_.DecreaseRef(shmId)` 从 2 → 1（仍 > 0）→ 返回 false → **不发 `DecreaseShmRef` RPC**；
- → **worker 端 Get 路径加的那个 ref 永久漏**。

如果业务对同一 objectKey 反复 Get，每次都会在 worker 上累积一份 ref，永远不被清。

#### (b)/(c) 后果：直接 worker 端 ref 漏

(b) 是低概率（认证 / 校验失败少见），(c) 是高概率—— **被动缩容窗口期内业务还在写**，client 切换还没完成时发出去的 Publish 必然 RPC 失败，正好砸在写死 `workerApi_[LOCAL_WORKER]` 的兜底路径。

#### 触发频率与用户当前测试的关系

- 用户的 `set_usr_pin_8m` 用的是单 `Create + Set(buffer)`，且**很可能开了 USE_URMA**（compose 标准跨节点配置）；
- 切到 standby 后 `IsShmEnable()=false` 但 `IsUrmaEnabled()=true` → **完全命中本节路径**；
- 即使每次 Publish 成功，client 端 `memoryRefCount_` 也每次残留 +1；如果业务有 Get（即使是别的客户端 `redis_lpush_JD(base_key)` 之后下游消费方的 Get），worker 端 ref 就会按对象单调累加；
- workerB 端 `OBJECT_COUNT` 与 `OBJECT_SIZE` 同步增长，但**额外**还在 `memoryRefTable_` 上累积"被 Get 钉住的 8 MB shm"——这就是 6.2 末尾说的 `MEMORY_USAGE ≈ 2 × OBJECT_SIZE` 的可能来源。

#### 修复方向（合并到 §5 P1''')

> **P1'''** — `Buffer::Publish` 在 URMA 路径（`isShm_=false && shmId 非空 && status.IsOk()`）下设 `isReleased_=true` 之前，**先调一次** `clientImpl->memoryRefCount_.DecreaseRef(shmId)`（无视返回值，仅是清掉 Step 1 加的那个 +1），保证 client 本地表与 worker 端清理对称。
>
> **P1'''-2** — `DecreaseReferenceCntImpl` 把 `workerApi_[LOCAL_WORKER]` 改成 `workerApi_[currentNode_]`（或者按 shmId 维度记录"这条 ref 是哪台 worker 给的"，定向发 RPC）。这样切到 standby 后 RPC 失败补偿才能发到正确的 worker。
>
> **P1'''-3** — `WorkerOcServicePublishImpl::PublishImpl` 把 line 375-377 的 `RemoveShmUnit` 提前到 `Authenticate` 失败/早期校验失败路径之前，或者用 RAII 保证一定会跑（覆盖 (b)）。

### 3.5 内存泄漏 / OOM（即 3.2 + 3.3 + 3.4 + 3.4.1 + 3.4.2 的累积）

把 3.1 + 3.2 + 3.3 + 3.4 串起来就闭环了。需要先**对齐"OOM 发生在哪一台 worker"再选证据**：

- **故障 worker（原 LOCAL_WORKER，例如 workerA）的 shm 不下降**：主因是 3.2 (d) + 3.3，旧 user-pin shm 在 `memoryRefTable_` 上漏掉，`memoryRefCount_.Clear()` 后再无人 release。
- **接管 worker（standby，例如 workerB）的 OOM**：主因是 3.4 的 (i)+(ii)+(iii)，client 强制 non-shm payload + retry 翻倍 + `NONE_L2_CACHE` 禁 SPILL；如果业务用了 globalRef 还会叠加 (iv)。

回到日志现场（`kv2-jingpai-1`）：

- 第一拍 `23:16:01`：`MasterAsyncTask = 0/5/5/10/1.000` —— 控制面已经堵了。后续 10s 内 OBJECT_COUNT 略减、`OBJECT_SIZE +0.77 GB`。
- 后续每 10s `+~5 GB` 而 OBJECT_COUNT 仅缓降 → `Allocator::FreeMemory` 几乎没发生。
- `23:17:21 / 23:17:31`：OBJECT_COUNT 骤降到 111/37，但 `OBJECT_SIZE` 反冲到 36.7/37.5 GB → `objectTable_->Erase` 在跑（删元数据），底下 `ShmUnit` 还被 `memoryRefTable_` 或同 batch 其他 slice 钉着，物理 shm 不释放。
- `OC_HIT_NUM` 中 miss 不涨 → 不是缓存命中问题。
- 最终 `rate=0.999` 触发 OOM。

## 4. 下一步排查（按代价从低到高）

1. **打开 worker `--v=1`** 关注：
   - `Allocator::FreeMemory` 调用频率 vs 写入频率（`shm_unit.cpp:78` 的 `[ShmUnit] Arena FreeMemory`）。
   - `~ShmUnit` 析构日志（`shm_unit.cpp:47`）是否对得上 OBJECT_COUNT 的下降量。
   - `ClearObject` / `DeleteObject` / `EvictObject Action::DELETE` 次数。
   - 三者 mismatch 越大，越确认是 `memoryRefTable_` 或 `ShmOwner` 钉住。

2. **加一次性诊断打印**：
   - `memoryRefTable_->shmRefTable_.size()` / `clientRefTable_.size()` 是否单调上涨。
   - `objectMemoryStats_->Usage()` 与 `objectTable_->GetSize()` 平均比值随时间的变化。

3. **三个 case 验证**：
   - **判定 (d) 主因**：把 `set_usr_pin_8m` 中的 `client->Set(buffer)` 改为批量 `client->MSet({buffer})`，观察 `OBJECT_SIZE` 是否随 `OBJECT_COUNT` 同步下降。`MSet` 走 `auto_release_memory_ref`，理论上能完全消除单 `Publish` 的 ref 泄漏。
   - **判定 (d) 异步释放窗口**：通过 inject point `client.DecreaseReferenceCnt` 把 `async` 设为 false（参考 `object_client_impl.cpp:1499`），让释放变成同步，看 shm 增长是否消失或显著缓解。
   - **判定 (d) `IsBufferAlive` 吞 RPC**：在 client 与 worker 之间制造一次短连接抖动后再观察 `OBJECT_SIZE` 走势，看是否出现"抖动后 ref 永久泄漏"的台阶。
   - **判定 (b)（次要）**：把 `oc_worker_aggregate_merge_size` 临时调到很小或关闭，复跑同样负载，看 300KB 分支带来的小幅泄漏是否消失。
   - 客户端"突然 kill"场景下，确认 worker 心跳/断连回调真的走了 `RemoveClient`（`worker_oc_service_impl.cpp:1156`），而不是只关连接。

4. **被动缩容 + 切 standby 专项验证（3.3 链路）**：
   - 跑 `set_usr_pin_8m` 同时对 LOCAL_WORKER 注入 `passive scale-down`（参考 `tests/st/worker/object_cache/slot_end2end_test.cpp` 里的 `PassiveScaleDownRecovers*` 系列）：
     - 期望对照 LOCAL_WORKER 的 `OBJECT_SIZE`/`shm.memUsage` 是否在切换瞬间出现一个**台阶式上涨且不下降**；
     - 同时看 standby 那台 worker 上 `OBJECT_SIZE` 是否同步上涨（说明业务流量切走了），但旧 LOCAL_WORKER 的差额一直挂着。
   - 验证 LOCAL_WORKER 进程 `ps`/`/proc/<pid>/status` 的 `VmRSS` 和 `shm` 在 `node_timeout_s → node_dead_timeout_s` 窗口期是否单调上涨；SIGKILL 后是否瞬间归零（确认 OS 层最终能回收，问题仅限于窗口期）。
   - 在 client 端打开 `--v=1`，搜 `[Reconnect] Clear meta and try reconnect to`、`[Switch] Switch worker to` 与 `Try decrease ref count for shmId ... needDecreaseWorkerRef 0` —— 后者大量出现就是被 `memoryRefCount_.Clear()` 吃掉的证据。
   - 切到 standby 后再给 LOCAL_WORKER 发 `ReconcileShmRef`（手工 RPC 或 inject）观察是否能补救清理。

4. **TTL 这条线**：
   - master 端开 `--v=1`，看 `ExpiredObjectManager::Run()` 的 `Insert/Get/AsyncDelete` 日志和 `failedObjects_` 大小，确认到期事件是否在 `MasterAsyncTask` 池堆积。
   - 业务侧若用 `Expire(keys, 0)` 表达"立即过期"，应改为 `Delete`（`expired_object_manager.cpp:145-147` 不会执行立即删除）。

## 5. 修复方向（待评审）

按优先级排序（针对当前 8 MB user-pin 写循环这个 case）：

**P0 — 单 `Publish` 的 ref 自释放对齐 `MultiPublish`**
- 在 `ClientWorkerBaseApi::PreparePublishReq` 中加 `req.set_auto_release_memory_ref(!bufferInfo->shmId.Empty())`（与 `MultiPublish` 对齐），并在 `WorkerOCServiceImpl::Publish` 走完 `publishProc_->Publish` 成功路径后，按 `auto_release_memory_ref` 调一次 `DecreaseMemoryRef(clientId, {shmId})`。这样单 buffer Set 也能在 RPC 内同步释放，不再依赖 client 异步链。
- 影响面较窄（PublishReqPb 已有 `auto_release_memory_ref` 字段，只是单 Publish 路径没用）。
- **同时根治 3.3 场景**：因为 ref 在 `Publish` RPC 同步内释放，被动缩容/切 standby 时根本没有"待异步释放"的 ref 留在 LOCAL_WORKER 上。

**P0'' — 切到 standby 后保持 / 重建 shm 通道**
- `client_worker_common_api.cpp:329` 现在切 standby 时一刀切把 `shmEnableType_` 设成 `NONE`，让 8 MB 写**全部**退化为 RPC payload；这是接管端 (3.4 (i)) OOM 的核心放大器。
- 修复方向二选一：
  - 远端 standby 同样支持 shm 通道（client/standby 在同机时通过 UDS 传 fd，建立 mmap）；只有真正跨机才退化。
  - 跨机时即使非 shm，也加一道**写入端流控**：worker 端按 `physicalMemoryStats_->RealUsage()` 与 high water 的差值动态返回 `K_TRY_AGAIN` / `K_OUT_OF_MEMORY`，让 client retry 退避而不是继续灌 8 MB payload。
- 配套：client 端 `Publish/MultiPublish` 的 `RetryOnError` 集合在收到 `K_OUT_OF_MEMORY` 时**强制 backoff**（当前是几乎立即重发），避免 (3.4 (ii)) 翻倍。

**P0' — 切 worker 前对原 worker 强制 release**
- 在 `SwitchToStandbyWorkerImpl` 进入新 worker 之前，对 `currentApi`（原 LOCAL_WORKER）做一次"批量 release 所有未释放 shmId"的 RPC（带超时，best-effort，不阻塞切换）：
  - 客户端侧从 `memoryRefCount_` 拿当前所有 shmId 列表 → 一次 `DecreaseShmRef` 批量 RPC → 然后再走 `Clear()`。
  - 如果原 worker 已不可达，至少把这批 shmId 写入"待补偿队列"，等 LOCAL_WORKER 恢复 / 重连后回补一次（参考 `RecordMaybeExpiredShm` 思路扩展）。
- `ProcessWorkerTimeout` 当前的 `memoryRefCount_.Clear()` 应在尝试 release 之后执行，并且 LOG WARNING 出可能漏掉的条数 + shmId 摘要，便于观测和事后人工补救。

**P1 — TTL 删除链补上 `memoryRefTable_` 清理**
- 在 `WorkerOcServiceCrudCommonApi::ClearObject`（即 TTL/eviction 都会汇合的位置）追加：根据 `entry` 的 `shmId` 反查 `memoryRefTable_`，对所有持有该 shmId 的 client 强制 `RemoveShmUnit`（带告警/审计日志，标注是 TTL 或 eviction 触发的强释放）。
- 这样即使业务忘记 release（或者 client 已经被 `memoryRefCount_.Clear()` 切走），TTL 至少能兜底；同时直接覆盖用户怀疑 #1 与 3.3 的孤儿 ref。

**P0''' — USE_URMA + non-shm 模式下 `Buffer::Publish` 应清掉 client 本地 ref**（针对 3.4.2 的 (a) + 后续 Get 链）
- `Buffer::Publish` 在设 `isReleased_=true` 之前**先调一次** `clientImpl->memoryRefCount_.DecreaseRef(shmId)`（无视返回值），消除 Step 1 加的 +1，与 worker 端 `RemoveShmUnit` 对齐。
- 同时把 `DecreaseReferenceCntImpl` 中的 `workerApi_[LOCAL_WORKER]` 改成按 shmId 维度记录的"原始 worker"或 `workerApi_[currentNode_]`（看业务语义），避免切 standby 后兜底 RPC 发到错误目标。
- 把 `WorkerOcServicePublishImpl::PublishImpl` 第 375-377 行的 `RemoveShmUnit` 用 RAII / `Raii([&]{ ... })` 包住或提前到早期校验之前，覆盖 `Authenticate` / `CheckShmUnitByTenantId` 失败的窗口。

**P1'' — `MultiCreate` 在 non-shm 模式下不应分配 shm**（针对 3.4.1）
- `WorkerOcServiceCreateImpl::MultiCreateImpl` 的 `createMeta` 内，在 `AllocateMemoryForObject`/`DistributeMemoryForObject` 之前增加 `if (!ClientShmEnabled(clientId) && !IsUrmaEnabled())` 跳过分配；只填回 `subRsp[i].set_shm_id("")`、`set_metadata_size(0)`，让 client 端继续走"丢 shm_id、纯 RPC payload 直发"路径。
- 配套：`memoryRefTable_->AddShmUnits` 跳过这些空 unit；如果保险起见，对该 client 的 RPC 请求**强制**走 `skip_check_existence=true` + 仅返回 existence 信息（不带 shm 元数据）。
- 这条修复直接消除"client 切 standby 后 NX MSet 在 workerB 上每个对象漏一份 8 MB shm"。

**P1' — Worker 端"客户端切走"主动 GC**
- Worker 端在以下三个事件之一发生时，对该 clientId 走一次 `memoryRefTable_->RemoveClient(clientId)`：
  1. 心跳长时间未收到（已存在 `client_dead_timeout_s` 路径，但当前似乎只清部分状态）；
  2. 收到 `RegisterClient` 且 `remainClient = false`（client 是新连接，意味着旧实例不再回来）；
  3. 收到来自同 clientId 的 `RegisterClient` 并且 `workerStartId` 与本地不一致（worker 重启后旧 ref 必失效）。
- 这相当于把"client 切走 / 重启"一并视为旧 ref 全部过期，防止被动缩容窗口期内的累积。

**P2 — `IsBufferAlive` 吞 RPC 的可观测与兜底**
- `DecreaseReferenceCntImpl` 在 `IsBufferAlive` 失败时增加 `LOG(WARNING)` + 计数指标（如 `client_dec_ref_skipped_due_to_dead_buffer`）。
- 客户端侧维护一份"待补偿 release 列表"，连接恢复后批量重发；或者在重连握手时让 worker 把该 clientId 的 ref 全部清掉（如果 client 认为不再持有）。

**P3 — Worker 端 stale ref 周期 GC**
- 现有 `RecordMaybeExpiredShm` / `FlushMaybeExpiredQueue` / `ReconcileShmRef` 已经是这条思路（`object_ref_info.h:514-543`），但只针对"可能过期的 shm"。建议把扫描周期/阈值做成可调，并在 `res_metric` 里增加 `memoryRefTable_ size` 与 `maybeExpired queue size` 字段。

**P4 — 异步释放池规模可调 / 关键路径同步化**
- `asyncReleasePool_` 固定 1 线程（`object_client_impl.cpp:344`）；高并发写场景应允许配置 `(min, max)`，避免单线程成为瓶颈。
- 提供环境变量/参数让业务可以选"同步释放"模式（利用 `client.DecreaseReferenceCnt` inject 已有逻辑），便于性能测试和泄漏排查。

**P5 — 可观测性补强**
- 新增 `res_metric` 字段：`memoryRefTable_` 客户端数 / 总 ref 数 / 平均每客户端 ref 数；`OBJECT_SIZE - sum(objectTable_ entries' shm size)` 的差值（直接量化"游离在 ref 表上的 shm"）。
- master 侧 `MASTER_ASYNC_TASKS_THREAD_POOL` 排队长度做告警，避免"删除已完成但 shm 没释放"的错觉再次发生。

**业务侧立即缓解**（不依赖代码修复）：

- 把 `client->Set(buffer)` 改为 `client->MSet({buffer})`，直接走 `MultiPublish` 的 `auto_release_memory_ref` 路径（推荐）。
- 或在 `client->Set(buffer)` 之后**显式调用** `buffer->InvalidateBuffer()`，再让 `buffer.reset()` 立即触发 `~Buffer`；并把 `client.DecreaseReferenceCnt` 的 `async` 在测试场景下关掉（同步释放），把异步窗口压到最小。
- 不要依赖 TTL 释放 shm；写完不用就 `Delete`。
- **针对 3.3 被动缩容**：
  - 把 `node_dead_timeout_s` 调小到与 `node_timeout_s` 接近（前提：能容忍误杀的代价），缩短"已切 standby 但 LOCAL_WORKER 仍占 shm"的窗口期。
  - 监控告警里加一条 `OBJECT_SIZE - ∑(objectTable_ entries 的 shm size)`，被动缩容窗口期一旦触发立即报警。
  - 业务侧重试逻辑：在收到 `K_SCALE_DOWN` / 切 standby 之后，**不要复用旧 buffer / 旧 shmId**，让旧 `shared_ptr<Buffer>` 立即析构（早 release 比晚 release 好；即便 RPC 发不出去，本地结构释放也有助 mmap 立即 unmap，配合 P1 的 worker 侧 GC）。

## 6. 一句话结论（区分"故障端"与"接管端"两台 worker）

日志最强的指纹是 **`OBJECT_COUNT` 与 `OBJECT_SIZE` 反向**。要按 OOM 实际发生的 worker 区分原因：

### 6.1 故障端（原 LOCAL_WORKER，例如 workerA）

- **主因 = 3.2 (d) 单 `Publish` 路径不带 `auto_release_memory_ref`**：业务用 `Create + MemoryCopy + Set(buffer)` 写 8 MB 对象，worker 上 `memoryRefTable_` 加 ref 后无 RPC 自释放，全靠 client `~Buffer` 异步链。
- **强放大器 = 3.3 被动缩容 + 切 standby**：`ProcessWorkerTimeout` 主动 `memoryRefCount_.Clear()`、`SwitchToStandbyWorkerImpl` 不通知原 worker、`DecreaseReferenceCntImpl` 写死 `workerApi_[LOCAL_WORKER]`，三连击让 8 MB user-pin ref **完全无人回收**，必须等 SIGKILL 随进程释放。窗口期 = `node_dead_timeout_s − node_timeout_s`（默认 240s）。
- **#1 TTL 未生效是表象**：TTL 路径在跑，删完元数据但 ref 表上的 shm 仍在。
- **#2 object 删 buffer 没删直接成立**：`ClearObject` 不感知 `memoryRefTable_`。

### 6.2 接管端（standby，例如 workerB）

- **主因（用户 8MB 单 Create 路径强烈疑似命中）= 3.4.2 USE_URMA + non-shm 模式下 ref 不对称**：worker 端 `memoryRefTable_` 在正常 Publish 成功路径下确实被 `RemoveShmUnit` 清掉，但 (b) 早期失败、(c) RPC 失败 + `workerApi_[LOCAL_WORKER]` 写死，都会让 workerB 上 ref 漏；client 本地 `memoryRefCount_` 因 `isReleased_=true` **每次 Create+Set 都残留 +1**，连锁污染后续 Get 路径，让 Get 加的 worker ref 永远释放不掉。
- **放大器 = 3.4 (i) Client 切 standby 后强制 non-shm payload**：`client_worker_common_api.cpp:329` 一刀切 `shmEnableType_=NONE`，8 MB 全部退化成 RPC payload 直发（USE_URMA 不开时）。workerB 端必须自己 `AllocateMemoryForObject(8MB)` + memcpy，瞬时峰值 ≈ 2× 8 MB。
- **放大器 = 3.4 (ii) 多 client 集中 + retry 翻倍**：standby 通常仅 1～2 台，所有切走的流量集中到 workerB；retry 把同一对象多次入栈。
- **独立 ref 漏洞 = 3.4.1 `MultiCreate` non-shm 加 ref 不释**：业务用 `MSet(... NX ...)` 时 worker 端为每个对象分配 8 MB shm + AddShmUnit，client 端 `PostMultiCreate` 直接丢弃 shm_id，永久泄漏。仅当业务路径包含 NX MSet/MCreate 时命中。
- **不可释放的 pin = 3.4 (iv) `RecoveryClient` 重放 globalRef**：业务的 `globalRefCount_` 在 workerB 上被一次性 `GIncreaseRef`，这些对象 `IsObjectEvictable=false`。
- **eviction 路径退化 = 3.4 (iii) `WriteMode::NONE_L2_CACHE`**：eviction 只能 DELETE 不能 SPILL，跟不上写入速率。
- **额外贡献 = 3.4 (v) slot recovery preload**：与业务实时写抢 high water。

接管端的判别特征：`OBJECT_COUNT` 与 `OBJECT_SIZE` 通常**同步增长**（非 user-pin 路径），增长速度异常快、`WORKER_OC_SERVICE_THREAD_POOL` 持续高 waiting；与 6.1 故障端的"反向曲线"形成对照。如果出现 `MEMORY_USAGE ≈ 2 × OBJECT_SIZE` 的诡异比，强烈指向 3.4.1/3.4.2 的双份 shm 或 ref 钉住。

### 6.3 #3（OOM）= 6.1 + 6.2 共同累积

- 故障端因为 3.3 漏掉旧 user-pin shm，物理 shm 在 240s 内一直占着；
- 接管端因为 3.4 (i)+(ii)+(iii) 在切走瞬间承接 N 倍写入压力，eviction 跟不上；
- 哪台先打满高水位就先 OOM；当前测试反馈是 workerB 先爆，主因在 6.2。

**次要因素 (b) `ShmOwner` 聚合分配**：仅对 1% 的 300KB 分支生效，量级远小于主路径，不是当前 OOM 的主因。

---

## 引用代码位置

- 资源日志采集
  - `yuanrong-datasystem/src/datasystem/common/metrics/res_metrics.def`
  - `yuanrong-datasystem/src/datasystem/common/metrics/res_metric_collector.cpp`
  - `yuanrong-datasystem/src/datasystem/worker/worker_oc_server.cpp`（`RegisterCollectHandler`）
- Worker 写 / 删 / TTL / 释放
  - `yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_service_impl.{h,cpp}`（`Publish` / `MultiPublish` / `DecreaseMemoryRef` / `RemoveClient`）
  - `yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_eviction_manager.cpp`
  - `yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_crud_common_api.cpp`（`ClearObject`）
  - `yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_create_impl.cpp`（`AddShmUnit`）
  - `yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_expire_impl.cpp`
  - `yuanrong-datasystem/src/datasystem/worker/object_cache/obj_cache_shm_unit.{h,cpp}`
- 共享内存 / 引用表
  - `yuanrong-datasystem/src/datasystem/common/shared_memory/shm_unit.{h,cpp}`
  - `yuanrong-datasystem/src/datasystem/common/shared_memory/allocator.{h,cpp}`
  - `yuanrong-datasystem/src/datasystem/common/object_cache/object_ref_info.{h,cpp}`（`SharedMemoryRefTable`）
- Master TTL
  - `yuanrong-datasystem/src/datasystem/master/object_cache/expired_object_manager.cpp`
- Client 单 Publish / 异步释放链
  - `yuanrong-datasystem/src/datasystem/client/kv_cache/kv_client.cpp`（`KVClient::Set(buffer)`）
  - `yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp`（`Publish` / `DecreaseReferenceCnt(Impl)` / `IsBufferAlive` / `asyncReleasePool_` / `ProcessWorkerLost` / `ProcessWorkerTimeout` / `SwitchToStandbyWorkerImpl`）
  - `yuanrong-datasystem/src/datasystem/client/object_cache/client_worker_api/client_worker_base_api.cpp`（`PreparePublishReq`，**关键缺失点**）
  - `yuanrong-datasystem/src/datasystem/client/object_cache/client_worker_api/client_worker_remote_api.cpp`（`Publish` vs `MultiPublish`）
  - `yuanrong-datasystem/src/datasystem/common/object_cache/buffer.cpp`（`~Buffer` / `Release` / `Publish`）
  - `yuanrong-datasystem/src/datasystem/protos/object_posix.proto`（`PublishReqPb.auto_release_memory_ref`）
- 切 standby / 接管端
  - `yuanrong-datasystem/src/datasystem/client/client_worker_common_api.cpp`（`shmEnableType_ = isUseStandbyWorker_ ? NONE : ...`）
  - `yuanrong-datasystem/src/datasystem/worker/worker_service_impl.cpp`（`RegisterClient` → `ProcessServerReboot`）
  - `yuanrong-datasystem/src/datasystem/worker/worker_oc_server.cpp`（`ProcessServerReboot` → `RecoveryClient`）
  - `yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`（`RecoveryClient` 中的 `GIncreaseRef`）
  - `yuanrong-datasystem/src/datasystem/worker/object_cache/slot_recovery/slot_recovery_manager.cpp`（`CheckPreloadMemoryAvailable` / `TakeOverPendingFromSourceIncident`）
  - `yuanrong-datasystem/tests/st/worker/object_cache/slot_end2end_test.cpp`（`PassiveScaleDownRecovers*` / `RecoveryPreloadOomKeepsReceiverData` 用例参考）
