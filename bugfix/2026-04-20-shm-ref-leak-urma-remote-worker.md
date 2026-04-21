# SHM 引用计数泄漏 — USE_URMA + 客户端连远端 Worker 场景

> 范围：`USE_URMA` 编译开启；客户端通过 UB 连接 **远端 Worker**（`ClientShmEnabled(clientId) == false`，走 RPC + URMA write 路径，单条对象 8 MB，`set_usr_pin_8m` 复现）。
> 目标：定位 `worker_oc_service_*` 与 `client/object_cache/*` 中导致 `shm.memUsage` 持续上涨而 `OBJECT_COUNT` 同期下降的引用计数泄漏点，并给出修改方案；本阶段不改代码。

---

## 0. 现象快速对账

| 观察                                                          | 含义                                                                                               |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `shm.memUsage` 3.58 GB → 37.5 GB；`OBJECT_COUNT` 438 → 37     | `objectTable_` 已经把 entry 删掉，但 `ShmUnit` 还被人持有 ⇒ `Allocator::FreeMemory` 不触发           |
| `worker_err.log` 大量 `Out of memory` / `Shared memory no space in arena` | 物理 shm 被占满，新的 `Create` / 远端 Get 的 `AllocateMemoryForObject` 直接 OOM                    |
| `client_err.log` 持续 `Send GetObjMetaInfo failed [RPC unavailable]` + `Try again. queue is empty within 50 ms` | 客户端层面 RPC 进入限流/超时 — 触发心跳超时 → `ProcessWorkerTimeout` → `Clear()` 引用表（详见 §3）  |
| `client_access.log` 8 380 486 B 的 `DS_KV_CLIENT_CREATE` 反复出现 | 复现路径就是 `Create + MemoryCopy + Set/Publish`；单 Publish 路径（非 `MultiPublish`）             |

唯一能解释「entry 已删 / shm 不释放」的对象只有 **`memoryRefTable_` 里残留的 `shared_ptr<ShmUnit>`**——它独占持有 `ShmUnit`，不释放则物理内存永不还给 arena（`ShmUnit::~ShmUnit` 调 `Allocator::FreeMemory`）。下面把所有把它「加进去但没拿出来」的路径全部列出来。

---

## 1. 故障 Worker（A，原 LOCAL）vs. 接管 Worker（B，client switch 后的新 LOCAL）

OOM 实际发生在 **B 上**（kv2-jingpai-1），但 **A 上的泄漏量是 B 被压垮的根因之一**。两边都有独立漏点，必须分别处理。下文用 A / B 区分。

---

## 2. Worker 侧硬伤 #1 — 单条 `Create` 在 URMA 分支没有回滚（A & B 都中招）

### 2.1 代码

```95:120:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_create_impl.cpp
auto shmUnit = std::make_shared<ShmUnit>();
auto metadataSize = GetMetadataSize();
RETURN_IF_NOT_OK_PRINT_ERROR_MSG(
    AllocateMemoryForObject(objectKey, dataSize, metadataSize, true, evictionManager_, *shmUnit, cacheType),
    "worker allocate memory failed");

std::string shmUnitId;
IndexUuidGenerator(shmIdCounter.fetch_add(1), shmUnitId);
shmUnit->id = ShmKey::Intern(shmUnitId);
memoryRefTable_->AddShmUnit(clientId, shmUnit, requestTimeoutMs);   // ← (a) ref +1
// ... fill resp.shm_id / fd / offset ...
#ifdef USE_URMA
if (!ClientShmEnabled(clientId) && IsUrmaEnabled()) {
    RETURN_IF_NOT_OK(
        FillRequestUrmaInfo(localAddress_, shmUnit->GetPointer(), shmUnit->GetOffset(), metadataSize, resp));
        // ← (b) 失败直接 return；(a) 不回滚
}
#endif
```

### 2.2 漏点

- (a) 之后 (b) 任意一步报错（`FillRequestUrmaInfo` 在 URMA 资源紧张/句柄耗尽时是会失败的），直接 `return`，**没有 `RemoveShmUnit`**。
- 对照 `MultiCreate` 已经写了完整回滚：

  ```250:276:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_create_impl.cpp
  // ... MultiCreate 顶层
  if (rc.IsError()) {
      for (auto &subResp : resp.results()) {
          memoryRefTable_->RemoveShmUnit(clientId, ShmKey::Intern(subResp.shm_id()));
      }
  }
  ```

  单条 `Create` 顶层（同一文件里）没有这层 `Raii`/回滚。

- 还有一种 worker 侧无法察觉的泄漏：**`Create` RPC 已经 `AddShmUnit` 并把 resp 写完，但 ZMQ 把响应回写到客户端时 `RPC deadline exceeded`**——客户端从未拿到 `shm_id`，永远不会调用 `DecreaseShmRef`；worker 侧 ref 永久残留，只能等 `node_dead_timeout_s` 把整个 client 过期清表。

### 2.3 修改方案

1. **在 `CreateImpl` 顶部建立 `Raii` 回滚**（与 `MultiCreate` 风格一致）：

   ```pseudo
   bool committed = false;
   Raii rollback([&] {
       if (committed) return;
       memoryRefTable_->RemoveShmUnit(clientId, shmUnit->id);
   });
   ... AllocateMemoryForObject ...
   memoryRefTable_->AddShmUnit(clientId, shmUnit, requestTimeoutMs);
   ... fill resp ...
   #ifdef USE_URMA
   if (!ClientShmEnabled(clientId) && IsUrmaEnabled()) {
       RETURN_IF_NOT_OK(FillRequestUrmaInfo(...));   // 失败时 Raii 自动回滚
   }
   #endif
   committed = true;
   ```

2. **`Create` / `MultiCreate` 顶层**包一层「RPC 写响应失败 → 回滚」：在 `WorkerOCServiceImpl::Create(...)` 的 `serverApi->SendResponse(...)` 检查返回，如果是 `K_RPC_*` 类错误就回收 `memoryRefTable_` 中刚加的 entry。当前 `Create` 路径没有这一层（搜索 `worker_oc_service_impl.cpp` 的 `Create`/`Publish` 都没有针对响应发送失败的回收逻辑）。

3. 短期兜底：给 `memoryRefTable_->AddShmUnit(...)` 增加一个**短 TTL**（≤ `request_timeout * 2`），如果在 TTL 内既没收到 `Publish` 也没收到 `DecreaseShmRef`，主动回收。这相当于 worker 侧自带 reconcile，可以彻底兜住 (b) / SendResponse 失败 / client 提前 crash 三种情况。

---

## 3. Client 侧硬伤 #1 — 单条 `Create` 没有 Raii 回滚（B 上累计）

### 3.1 代码

```1308:1355:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
Status ObjectClientImpl::Create(const std::string &objectKey, uint64_t dataSize, ...)
{
    ...
    if (workerApi->ShmCreateable(dataSize) || IsUrmaEnabled()) {
        ...
        RETURN_IF_NOT_OK(workerApi->Create(...));               // ← worker 已 AddShmUnit
        std::shared_ptr<ObjectBufferInfo> bufferInfo = nullptr;
        std::shared_ptr<client::IMmapTableEntry> mmapEntry = nullptr;
        if (!urmaDataInfo) {
            RETURN_IF_NOT_OK(mmapManager_->LookupUnitsAndMmapFd("", shmBuf));   // ← 可失败
            mmapEntry = mmapManager_->GetMmapEntryByFd(shmBuf->fd);
            CHECK_FAIL_RETURN_STATUS(mmapEntry != nullptr, ...);                 // ← 可失败
            bufferInfo = MakeObjectBufferInfo(...);
        } else {
            bufferInfo = MakeObjectBufferInfo(objectKey, nullptr, dataSize, 0, param, false, version, shmBuf->id);
        }
        bufferInfo->ubUrmaDataInfo = urmaDataInfo;
        memoryRefCount_.IncreaseRef(shmBuf->id);                 // ← client 本地 ref +1
        RETURN_IF_NOT_OK(Buffer::CreateBuffer(...));             // ← 可失败
    }
    ...
}
```

### 3.2 漏点（按失败位置逐条枚举）

| 失败位置                                | client 本地 `memoryRefCount_` | client `Buffer` 是否构造 | worker 侧 `memoryRefTable_` 是否回收  |
| --------------------------------------- | ------------------------------ | ----------------------- | ------------------------------------- |
| `LookupUnitsAndMmapFd` / `mmapEntry == nullptr` | 未加                           | 未构造                  | **永久残留** ⇒ worker 泄漏           |
| `Buffer::CreateBuffer` 失败             | **+1（已加）**                | 未构造（无 `~Buffer`）  | **永久残留**（无 `~Buffer` 触发释放）⇒ 双侧泄漏 |
| 整个 `Create` 成功后用户路径下游崩溃    | +1                             | 已构造，最终 `~Buffer` 释放 | OK |

对比 `MultiCreate` 早就写了 Raii：

```1434:1445:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
Raii handlerCreateFailed([&isInactive, &bufferList, this]() {
    if (isInactive) return;
    for (const auto &buffer : bufferList) {
        if (buffer == nullptr) continue;
        (void)memoryRefCount_.DecreaseRef(buffer->bufferInfo_->shmId);
    }
    bufferList.clear();
});
```

但单条 `Create` **完全没有**对应的回滚。

### 3.3 修改方案

- 在 `ObjectClientImpl::Create` 把 `workerApi->Create` 之后到 `Buffer` 构造完成之前的全部步骤，用一个 `Raii` 包住：失败时 (1) `memoryRefCount_.DecreaseRef(shmBuf->id)`，(2) 主动发一次 `workerApi->DecreaseShmRef({shmBuf->id})`（**注意是 `workerApi`，不是 `workerApi_[LOCAL_WORKER]`，下文 §6 会展开**）。
- `mmap` 失败的分支同样要清 worker 侧；建议把回收抽成 `RollbackCreatedShmRef(workerApi, shmBuf->id)`。

---

## 4. 单条 `Publish` 路径没有 `auto_release_memory_ref`（A 上累计）

### 4.1 代码

`MultiPublish` 客户端：

```413:413:yuanrong-datasystem/src/datasystem/client/object_cache/client_worker_api/client_worker_remote_api.cpp
req.set_auto_release_memory_ref(!bufferInfo[0]->shmId.Empty());
```

`MultiPublish` 服务端：

```436:448:yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_service_impl.cpp
if (req.auto_release_memory_ref()) {
    ...
    return DecreaseMemoryRef(clientId, shmIds);
}
```

但单条 `Publish` 路径：

- 客户端 `client_worker_remote_api.cpp` `Publish(...)` 与 `client_worker_local_api.cpp` `Publish(...)` 都**没有** `set_auto_release_memory_ref(true)` —— 只有 `MultiPublish` 设了。
- 服务端 `WorkerOCServiceImpl::Publish` 也**完全没有** `auto_release_memory_ref` 分支：

  ```409:422:yuanrong-datasystem/src/datasystem/worker/object_cache/worker_oc_service_impl.cpp
  Status WorkerOCServiceImpl::Publish(const PublishReqPb &req, PublishRspPb &resp, std::vector<RpcMessage> payloads)
  {
      ...
      Status rc = publishProc_->Publish(req, resp, payloads);
      if (rc.IsOk()) { ... }
      return rc;     // ← 没有 DecreaseMemoryRef 分支
  }
  ```

### 4.2 当前的「应急机制」与它的脆弱点

服务端只有在 `PublishImpl` 内部、URMA + 非 shm 客户端路径上才会清：

```373:377:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_publish_impl.cpp
if (!shmUnitId.Empty() && !(ShmEnable() && ClientShmEnabled(clientId))) {
    memoryRefTable_->RemoveShmUnit(clientId, shmUnitId);
}
```

注意它的位置 — **在 `Authenticate` 与 `CheckShmUnitByTenantId` 之后才到这里**。所以以下都会跳过清理：

- `Authenticate` 失败（token 过期、签名失效）⇒ 直接 `RETURN_IF_NOT_OK_PRINT_ERROR_MSG`
- `CheckShmUnitByTenantId` 失败（worker 已经被切换、`memoryRefTable_` 表项找不到对应 tenant）⇒ 直接 `RETURN_IF_NOT_OK`
- `PublishImpl` 整段函数没被调用（即 RPC 没到 worker）

并且对 **shm-enabled 客户端**（A 是本地 client，但又复现是连本地 worker 的话）这段清理是不会跑的——它依赖客户端 `~Buffer` 走 `DecreaseShmRef`。

### 4.3 修改方案

1. **客户端单 `Publish` 也要 `set_auto_release_memory_ref(true)`**（`local_api` 与 `remote_api` 两个文件各一处）。这样语义与 `MultiPublish` 对齐：worker 在 `Publish` 处理成功后立即清 `memoryRefTable_`，不再依赖 `~Buffer`。
2. **服务端 `WorkerOCServiceImpl::Publish` 增加 `auto_release_memory_ref` 分支**，复用 `DecreaseMemoryRef`。即把 `MultiPublish` 那段 if 拷过来，单 shm 版本。
3. 把 `PublishImpl` 里 `Authenticate` / `CheckShmUnitByTenantId` 失败的早返回路径也加上：「若 `shmUnitId` 非空，且 client 持有这个 ref，则 `RemoveShmUnit`」。最稳妥的写法是把这段清理用一个 `Raii` 包住，无论 `RETURN_IF_NOT_OK` 从哪里返回都能跑。

---

## 5. URMA + 非 shm 路径下 `Buffer::Publish` 让 `~Buffer` 不再补 `DecreaseRef`（B 上局部泄漏 + 客户端表污染）

### 5.1 代码

```226:255:yuanrong-datasystem/src/datasystem/common/object_cache/buffer.cpp
Status status = clientImplSharedPtr->Publish(bufferInfo_, nestedKeys, isShm_);
if (isShm_) {
    SetVisibility(status.IsOk());
} else {
    // worker already release shmUnit for this case.
    isReleased_ = !bufferInfo_->shmId.Empty() && status.IsOk();   // ← 标记 client 端不再发 Decrease
}
return status;
```

```168:186:yuanrong-datasystem/src/datasystem/common/object_cache/buffer.cpp
do {
    if (isReleased_) {
        break;     // ← URMA 路径 status.IsOk() 时这里 break，下面的 DecreaseReferenceCnt 不跑
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

### 5.2 漏点

- 在 URMA + 非 shm 路径，`Create` 时 `memoryRefCount_.IncreaseRef(shmBuf->id)` 已经 +1（§3 代码 1344 行）。
- `Publish` 成功后 `isReleased_ = true`，`~Buffer` 跳过 `DecreaseReferenceCnt` ⇒ **client 端 `memoryRefCount_` 表里这条记录永远 +1，永远不归零**。
- 后果 1：client 内存表越积越多（小内存泄漏）。
- 后果 2：**对同一 `shmId` 的下一次 `Get`/`Decrease` 经过 `IsBufferAlive` 与 `memoryRefCount_.DecreaseRef` 两道判断时，client 不会发 worker RPC 了**（`DecreaseRef` 返回 false 才发 RPC，true 时不发）。这条逻辑参见：

  ```1466:1491:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
  if (!memoryRefCount_.DecreaseRef(shmId)) { continue; }
  decreaseShms.emplace_back(shmId);
  ...
  workerApi_[LOCAL_WORKER]->DecreaseWorkerRef(decreaseShms);
  ```

  如果 client 的 `memoryRefCount_[shmId]` 永远 ≥ 1，最后一次 `~Buffer` 也只是把它从 N 减到 N-1，**永远不会触发 worker 端 `DecreaseShmRef`** —— 这就是 user 怀疑「create/get 引用计数加了，但共享内存引用计数没再对账」的精确机制。

### 5.3 修改方案

二选一（推荐 (a)）：

(a) **保持 `~Buffer` 不调 worker `Decrease`，但 `Buffer::Publish` 在标记 `isReleased_` 时同步把 client 本地 ref 对账掉**：

```pseudo
} else {
    if (!bufferInfo_->shmId.Empty() && status.IsOk()) {
        clientImplSharedPtr->memoryRefCount_.DecreaseRef(bufferInfo_->shmId);
        isReleased_ = true;
    }
}
```

逻辑保持「worker 已经在 `PublishImpl` / `auto_release_memory_ref` 里释放了 worker 侧 ref（前提是 §4 的修改也落地），所以 client 这里只需要把对应的本地计数也减掉，整条对账就闭合」。

(b) 更保守：让 `~Buffer` 在 URMA + 非 shm 路径下，仍然调 `clientImpl->DecreaseReferenceCnt(...)`，但传一个 flag `localOnly = true`，只清本地 `memoryRefCount_` 不发 RPC（避免 worker 重复 Decrease）。需要新增参数。

无论哪种，都必须配套 §4 的「worker 端无条件 release」，否则 client 单方面减了，worker 那边照样残。

---

## 6. 客户端 RPC 永远打到 `workerApi_[LOCAL_WORKER]`（A 上的真正杀手）

### 6.1 现状

整个 `object_client_impl.cpp` 中所有「释放/对账」类 RPC 全部硬编码 `workerApi_[LOCAL_WORKER]`：

| 行                | 调用                                                                |
| ----------------- | ------------------------------------------------------------------- |
| 1482              | `workerApi_[LOCAL_WORKER]->DecreaseWorkerRef(decreaseShms)`         |
| 1515              | `... GIncrease/GDecrease ... clientId_, needDecreaseWorkerRef`      |
| 1527              | `workerApi_[LOCAL_WORKER]->DecreaseShmRef(shmId, ...)`              |
| 2377 / 2406       | `GIncreaseWorkerRef`                                                |
| 2483 / 2517       | `GDecreaseWorkerRef`                                                |
| 2463              | `ReleaseGRefs(remoteClientId)`                                      |
| 3525              | `ReconcileShmRef(...)`（甚至 reconcile 也只对当前 LOCAL_WORKER 跑）  |

### 6.2 漏点链路（A → B 切换的精确时序）

1. A 心跳超时 → `ProcessWorkerTimeout` 执行：

   ```501:512:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
   void ObjectClientImpl::ProcessWorkerTimeout()
   {
       ...
       (void)workerApi->CleanUpForDecreaseShmRefAfterWorkerLost();
       mmapManager_->CleanInvalidMmapTable();
       memoryRefCount_.Clear();   // ← 把 client 本地引用表全清
   }
   ```

2. `SwitchToStandbyWorkerImpl` 用 B 替换 `workerApi_[next]`，并最终把 `workerApi_[LOCAL_WORKER]` 指向 B（细节在 `SwitchWorkerNode` / `TrySwitchBackToLocalWorker`）。
3. 之后任何 `Buffer` 析构走到 `DecreaseReferenceCnt`：
   - `memoryRefCount_.DecreaseRef(shmId)` 因为表已 `Clear()`，返回 false ⇒ 直接跳过；
   - 即便有残存的（多 `Buffer` 持有同 shmId）能进到 RPC，也是发给 B 的 `DecreaseShmRef`，**A 永远收不到**。
4. A 上对该 client 的所有 `memoryRefTable_` entry 只能等 `node_dead_timeout_s − node_timeout_s` 默认 ≈ 240 s 的过期窗口才会被 client lifecycle 清理；这 240 s 内 8 MB × 在飞数千条 = 几十 GB shm 占用 — 与现象完全吻合。

### 6.3 修改方案

短期（最小改动，可立刻修住「老 worker 不收 release」）：

1. **维护 `pendingReleaseByWorker_`**：每条 `Create` / `Get` 成功后记录 `(workerApi, shmId)`；`~Buffer`/`DecreaseReferenceCnt` 时按这个表决定 RPC 发往谁，而不是无脑 `workerApi_[LOCAL_WORKER]`。
2. `ProcessWorkerTimeout` 不再 `memoryRefCount_.Clear()`，而是把当时 `workerApi`（即将下线的 A）连同其 `memoryRefCount_` 子集一起冷冻起来，进入「best-effort flush」队列：连续重试若干次（指数退避）发 `DecreaseShmRef` 给 A；只有重试彻底失败或 A 已被判死才丢弃，并显式记录 metric `CLIENT_LOST_RELEASE_RPC_TOTAL`。
3. `ReconcileShmRef` 不要只对 LOCAL_WORKER 跑，要对「曾经服务过本 client 的所有 worker」跑，至少在 worker 重新上线时（A 后续可能恢复）补一次对账。

中长期：

4. 引入 worker 端的「按 client + 短 TTL 过期」兜底（与 §2.3 第 3 条同源）。这样即使 client 完全消失，worker 也能在 ≪ 240 s 量级内主动收回 ref。建议 TTL 与 `request_timeout * k` 联动，`k` 默认 4。
5. `AddShmUnit` 时同时注册一个「软引用」到一个 LRU + TTL 表，`Publish` / `DecreaseShmRef` 把软引用转硬引用或直接删除。任何超过 TTL 没被「认领」的，由后台定期扫描线程触发 `RemoveShmUnit`，并打告警 metric 便于观察泄漏。

---

## 7. 切到 standby 后 `MultiCreate` 在非 shm 模式下加 ref 不释放（B 上独立泄漏）

> 与 §2/§3 不同：这是 client 切到 B 之后，因为 B 暂时 `IsShmEnable()==false` 或 client 端 `IsShmEnable()` 走非 shm 分支引发的独立漏点。

### 7.1 代码

```1402:1432:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
bool canUseShm = workerApi->IsShmEnable() && dataSizeSum >= workerApi->shmThreshold_;
if (canUseShm || IsUrmaEnabled() || !skipCheckExistence) {
    ...
    RETURN_IF_NOT_OK(workerApi->MultiCreate(skipCheckExistence, multiCreateParamList, version, exists, useShmTransfer));
} else {
    exists.resize(objectKeyList.size(), false);
}
if (!useShmTransfer) {
    for (size_t i = 0; i < objectKeyList.size(); i++) {
        ...
        auto bufferInfo = MakeObjectBufferInfo(objectKey, nullptr, dataSize, 0, param, false, version);
        auto rc = Buffer::CreateBuffer(bufferInfo, shared_from_this(), newBuffer);   // ← bufferInfo 没 shmId
        ...
    }
    return Status::OK();   // ← 直接返回，不会进到下面的 Raii
}
bool isInactive = false;
Raii handlerCreateFailed(...);   // ← 只在 useShmTransfer == true 时生效
```

### 7.2 漏点

- 当 `IsUrmaEnabled() == true` 但 `canUseShm == false` 时仍会走 `workerApi->MultiCreate(...)`。worker 侧 `MultiCreateImpl` 会 `AddShmUnits`（B 上 ref +1）并把 `shm_id` 填回 `subRsp`。
- 若 client 看到 `useShmTransfer == false`（比如 worker 端因为 SHM 紧张 reject 走 RPC payload 路径），client 直接 `return Status::OK()` —— **`subRsp` 里的 `shm_id` 丢弃，对应的 `memoryRefCount_` 没加，`Buffer` 也无 `shmId`，析构时不会触发任何 Decrease**。worker 永久残。
- `Raii handlerCreateFailed` 只在 `useShmTransfer==true` 之后才生效，覆盖不到这条分支。

### 7.3 修改方案

- `workerApi->MultiCreate` 返回的每条 `subRsp.shm_id`：
  - 若 client 端最终走 RPC payload（`useShmTransfer==false`），需要在 `return Status::OK()` 之前把这些 `shm_id` 收集起来，主动发一次 `workerApi->DecreaseWorkerRef(shmIds)`（通过 §6 改造后的 per-worker API 句柄）。
  - 或者：在 worker 端 `MultiCreateImpl` 里，**当确定要走 RPC payload 时（worker 自己最清楚），不要 `AddShmUnit`**——这是更干净的修法，把责任收回 worker。

---

## 8. `MultiCreateImpl` 内并发分批的 ref 泄漏窗口（B 上小量但累计）

```cpp
// worker_oc_service_create_impl.cpp 里 MultiCreate 的并发批
results[i] = AllocateMemoryForObject(...);
// URMA 路径： if (urmaStatus.IsError()) results[i] = ...;  但循环不 break
shmUnits[j] = shmUnit;          // ← 已经放进局部 vector
...
memoryRefTable_->AddShmUnits(clientAccessor, shmUnits, req.request_timeout());   // ← 整批一起 Add
```

后续顶层逻辑（行号 250-276）只对 `resp.results()` 中 `shm_id` 非空的项做 `RemoveShmUnit` 回滚。但 `AddShmUnits` 是把整个 `shmUnits` 都加了，**包括其中分配/URMA 失败、对应 `subRsp.shm_id()` 没填的项**。这些「半失败」项的 ref 从未被回收，全部漏在 worker 上。

修改方案：`AddShmUnits` 之前先按 `subRsp[i].shm_id().empty()` 把 `shmUnits` 里失败的位置剔除；或者顶层回滚时不仅遍历 `resp.results()`，还要遍历 worker 内部记录的「这次请求实际 Add 的全部 shm_id 集合」。

---

## 9. Worker 远端 Get 拉数据失败的 `HandleGetFailureHelper` 已有自愈（这里没有泄漏）

```89:105:yuanrong-datasystem/src/datasystem/worker/object_cache/service/worker_oc_service_batch_get_impl.cpp
void WorkerOcServiceGetImpl::HandleGetFailureHelper(...)
{
    (void)RemoveLocation(objectKey, version);
    auto obj = entry->Get();
    if (obj == nullptr) return;
    if (obj->GetShmUnit() != nullptr) {
        obj->GetShmUnit()->SetHardFreeMemory();
    }
    obj->FreeResources();
    obj->SetLifeState(ObjectLifeState::OBJECT_INVALID);
    obj->stateInfo.SetCacheInvalid(true);
}
```

`BatchGetObjectFromRemoteOnLock` 失败路径（包括 `RPC deadline exceeded` / `K_OUT_OF_MEMORY`）会进 `failedMetas` 并触发上面的清理。**worker_err.log 里大量出现的 `BatchGetObjectFromRemoteOnLock` OOM 不是泄漏源，而是泄漏的「受害者」**——B 的 shm 被前面那些漏点吃光了，所以新拉数据时 `AllocateMemoryForObject` 直接 OOM。这条路径本身的 cleanup 是健康的，不要改。

---

## 10. 触发链复盘

```
A（原 LOCAL）出现拥塞 → 单 Publish 没 auto_release（§4）+ 单 Create URMA 无回滚（§2）
   ↘ A 上 memoryRefTable_ 累积；客户端 ~Buffer 偶发能减 ref，但概率不高
A 心跳超时 → ProcessWorkerTimeout → memoryRefCount_.Clear() → 切 B（§6）
   ↘ A 上残留的全部 ref 进入 240 s 死等窗口 → A 物理 shm 直到 worker 重启才还
B 接管 → 客户端把 8MB Set 全部砸到 B（远端非 shm 走 URMA）
   ↘ §3 client 单 Create 无 Raii 回滚 + §5 Publish 不补 Decrease ＋ §6 RPC 永远发 LOCAL（即 B 自己）
   ↘ §7 切到 B 时若退化为 RPC payload，shm_id 直接丢弃
   ↘ §8 MultiCreate 半失败 ref 不回收
B 上 OBJECT_COUNT 因 TTL/淘汰被清掉，但 memoryRefTable_ 里 shared_ptr<ShmUnit> 还在 → shm.memUsage 单调上涨 → 阈值触发 OOM
   ↘ 之后 BatchGet / Create AllocateMemoryForObject 全部 K_OUT_OF_MEMORY（log 里看到的现象）
```

---

## 11. 修改优先级建议（不动代码，给评审用）

| 序号 | 改动                                                                   | 影响面                  | 风险 | 收益 |
| ---- | ---------------------------------------------------------------------- | ----------------------- | ---- | ---- |
| P0   | §4 单 `Publish` 走 `auto_release_memory_ref`（client + worker 各一处） | 单条 Set 路径，最高频   | 低   | 极高 |
| P0   | §2 单条 `CreateImpl` URMA 分支加 Raii 回滚                              | URMA 路径全量            | 低   | 高   |
| P0   | §3 client 单 `Create` 加 Raii（mmap 失败 / Buffer::CreateBuffer 失败）  | 全量                    | 低   | 高   |
| P1   | §5 `Buffer::Publish` URMA 非 shm 路径补 `memoryRefCount_.DecreaseRef`   | URMA 路径               | 中   | 高（去掉 client 内存表污染）|
| P1   | §6 `Decrease*Ref`/`Reconcile*` 改为 per-worker 句柄；`ProcessWorkerTimeout` 不再 `Clear()`，改 best-effort flush | 全量，涉及 switch 语义 | 高   | 极高（240 s 窗口直接消除）|
| P1   | §2.3 / §6.3 worker 侧 `memoryRefTable_` 加 TTL + 后台 reconcile         | worker 侧               | 中   | 极高（兜底所有未知漏点）|
| P2   | §7 `MultiCreate` 切到 RPC payload 后丢 `shm_id` 的回收                  | standby 场景            | 低   | 中   |
| P2   | §8 `MultiCreateImpl` 半失败项的 ref 精确剔除                            | worker 侧               | 低   | 中   |
| P2   | 可观测性：增加 metric `MEMORY_REF_TABLE_SIZE` / `MEMORY_REF_TABLE_BYTES` 与 `OBJECT_TABLE_SIZE` 对账，以便定位类似问题 | 全量 | 低 | 高 |

---

## 12. 验证手段（实施前可先做）

1. **复现侧打点**：在 `memoryRefTable_->AddShmUnit` / `RemoveShmUnit` 处加 trace（可临时 GLOG VLOG(2)），按 `clientId` 聚合实时观察「Add - Remove」的差值；与 `shm.memUsage` / `OBJECT_COUNT` 对齐看是否完全吻合。
2. **TTL 灰度**：先把 §2.3 第 3 条（worker 端短 TTL）作为只告警不删除的「dry-run」上线，统计触发量 + 关联到具体 client/RPC 路径，再切到真正回收。
3. **A→B 切换专项测试**：故意让 A 进入心跳超时（注入 `client.standby_worker`），观察 A 上 `memoryRefTable_` 是否在 ≤ 30 s 内清空；不清空就证明 §6 必改。
4. **URMA 非 shm 路径专项**：用 `set_usr_pin_8m` 但强制 `IsShmEnable()==false`（client 配置），单独验证 §5 的 client 端表污染是否随时间线性增长。

---

## 13. 不影响现有用户的兼容性注意

- §4 给单 `Publish` 加 `auto_release_memory_ref`：worker 升级前如果旧 worker 收到带这个 flag 的请求会被忽略（proto 字段默认值），不影响正确性，只是泄漏依旧。**部署顺序：先升级 worker，再升级 client**。
- §6 改 per-worker RPC：`workerApi_[LOCAL_WORKER]` 的语义保留，只是新增 `workerApiHistory_` 用于 release，不影响线上调用方。
- §2.3 第 3 条 worker 端 TTL：必须 ≥ `request_timeout * 2`，否则会误回收正在 `Create + MemoryCopy + Publish` 中的 shm。建议 dry-run 至少 1 周再启用。

---

文档到此为止。下一步如果需要，我可以直接把 P0 的三个改动（§2 / §3 / §4）写成 PR 草稿；但按你目前的要求，先停在分析阶段，不动代码。
