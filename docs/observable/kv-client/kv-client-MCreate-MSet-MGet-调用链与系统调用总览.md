# KVClient MCreate / MSet / MGet 调用链与系统调用总览

本文梳理 KVClient 三个核心批量接口（MCreate、MSet、MGet）以及 Init 的完整调用链，标注每一步涉及的 **OS 系统调用** 和 **URMA 接口调用**，供定位定界和可观测改进参考。

**仓库**：`yuanrong-datasystem`
**约定**：与 [kv-client-URMA-OS-读写初始化-跨模块错误与重试.md](./kv-client-URMA-OS-读写初始化-跨模块错误与重试.md) 及 [Sheet1](../workbook/kv-client/kv-client-Sheet1-调用链-错误与日志.md) 一致。

---

## 0. 角色约定

| 代号 | 含义 |
|------|------|
| **client** | SDK 进程（`KVClient` / `ObjectClientImpl` / `ClientWorkerRemoteApi`） |
| **worker1** | 入口 Object Cache Worker（与 client 建 ZMQ 会话） |
| **worker2** | Directory Worker（对象目录 hash ring 分片，`QueryMeta` RPC 对端） |
| **worker3** | 数据副本 Worker（跨节点 UB 数据面对端） |

---

## 1. Init 接口

### 1.1 SDK 侧（client）

```text
KVClient::Init
  └─ ObjectClientImpl::Init
      ├─ ValidateHostPortString                          // 纯校验，无 I/O
      ├─ RpcAuthKeyManager::CreateClientCredentials      // AKSK，无 I/O
      └─ InitClientWorkerConnect
          └─ ClientWorkerRemoteApi::Init
              └─ Connect
                  ├─ CreateConnectionForTransferShmFd
                  │   ├─ GetSocketPath                   // [ZMQ RPC → worker1]
                  │   └─ CreateHandShakeFunc             // OS: socket → connect（UDS/TCP）
                  ├─ RegisterClient                      // [ZMQ RPC → worker1]
                  └─ PostRegisterClient
                      └─ FastTransportHandshake          // URMA 握手（失败仅 LOG_IF_ERROR，不阻断）
                          ├─ UrmaManager::UrmaInit       // URMA: ds_urma_register_log_func → ds_urma_init
                          ├─ UrmaGetEffectiveDevice      // URMA: ds_urma_get_device_list → ds_urma_get_device_by_name
                          ├─ GetEidIndex                 // URMA: ds_urma_get_eid_list → ds_urma_free_eid_list
                          ├─ UrmaResource::Create        // URMA: ds_urma_create_context → jfce → jfc → jfs → jfr
                          ├─ InitMemoryBufferPool        // OS: mmap(MAP_PRIVATE|MAP_ANONYMOUS)
                          └─ ExchangeJfr                 // URMA: ds_urma_import_jfr → ds_urma_advise_jfr
          [并行] RecvPageFd 线程                          // OS: recvmsg(SCM_RIGHTS) 循环收 fd
          MmapManager / InitListenWorker / 心跳线程       // OS: mmap；ZMQ 心跳
```

### 1.2 Worker 侧（worker1）

收到 `RegisterClient` RPC 后：
- 分配 client 资源（client id、心跳参数、SHM 阈值）
- 通过 UDS 发送 fd：**OS: sendmsg(SCM_RIGHTS)**
- UB 信息交换：URMA `ExchangeJfr` 对端处理

### 1.3 涉及的系统调用与 URMA 接口

| 类别 | 接口 | 代码位置 |
|------|------|---------|
| OS | `socket` / `connect` | `client_worker_common_api.cpp` (CreateHandShakeFunc) |
| OS | `recvmsg(SCM_RIGHTS)` / `sendmsg` | `common/util/fd_pass.cpp` (SockRecvFd/SockSendFd) |
| OS | `close` | `fd_pass.cpp`; `client_worker_common_api.cpp` |
| OS | `mmap(MAP_ANONYMOUS)` / `munmap` | `urma_manager.cpp` (InitMemoryBufferPool) |
| URMA | `ds_urma_init` / `ds_urma_uninit` | `urma_manager.cpp` (UrmaInit) |
| URMA | `ds_urma_get_device_list` / `ds_urma_get_device_by_name` | `urma_manager.cpp` (UrmaGetEffectiveDevice) |
| URMA | `ds_urma_get_eid_list` / `ds_urma_free_eid_list` | `urma_manager.cpp` (GetEidIndex) |
| URMA | `ds_urma_create_context` / `jfce` / `jfc` / `jfs` / `jfr` | `urma_resource.cpp` |
| URMA | `ds_urma_import_jfr` / `ds_urma_advise_jfr` | `urma_manager.cpp` (ExchangeJfr) |
| URMA | `ds_urma_register_log_func` | `urma_manager.cpp` (RegisterUrmaLog) |

---

## 2. MCreate 接口

### 2.1 SDK 侧

```text
ObjectClientImpl::MultiCreate
  ├─ (1) IsClientReady()                                // 状态检查
  ├─ (2) ConstructMultiCreateParam                      // key/size 组装
  ├─ (3) GetAvailableWorkerApi                          // 注意：始终走 LOCAL_WORKER
  └─ (4) workerApi_[LOCAL_WORKER]->MultiCreate
          ├─ MultiCreateReqPb + SetTokenAndTenantId
          ├─ RetryOnError → stub_->MultiCreate          // [ZMQ RPC → worker1]
          └─ PostMultiCreate                            // 回填 shmBuf / urmaDataInfo
  ├─ (5) [useShmTransfer] MutiCreateParallel
          ├─ SHM: mmapManager_->LookupUnitsAndMmapFd   // OS: mmap(store_fd)
          └─ UB: ubUrmaDataInfo 分支
```

### 2.2 Worker 侧（worker1）

- `ProcessMCreate` → 按条件分配 SHM/UB 空间
- 若落盘：**OS: open → pwrite → fsync**
- fd 传输：**OS: sendmsg(SCM_RIGHTS)**

### 2.3 涉及的系统调用与 URMA 接口

| 类别 | 接口 | 场景 |
|------|------|------|
| OS | ZMQ socket send/recv | 控制面 RPC |
| OS | `mmap` / `shm_open` | SHM 分配（client 侧） |
| OS | `open` / `pwrite` / `fsync` | 落盘场景（worker 侧） |
| OS | `sendmsg(SCM_RIGHTS)` / `recvmsg` | fd 传输 |
| URMA | `ds_urma_write` / `ds_urma_poll_jfc` | 若走 UB 数据面 |

---

## 3. MSet 接口

### 3.1 SDK 侧

```text
ObjectClientImpl::MSet(buffers)
  ├─ (1) batch 上限 + IsClientReady()
  ├─ (2) GetAvailableWorkerApi
  ├─ (3) 各 buffer: CheckDeprecated; seal → K_OC_ALREADY_SEALED
  ├─ (4) 组装 PublishParam / bufferInfoList
  └─ (5) workerApi->MultiPublish
          ├─ MultiPublishReqPb + payload 组装
          ├─ RetryOnError → stub_->MultiPublish         // [ZMQ RPC → worker1]
          │   重试码含: K_RPC_UNAVAILABLE, K_SCALING, K_OUT_OF_MEMORY
          └─ [可选] SendBufferViaUb → UrmaWritePayload  // URMA: ds_urma_write
  └─ (6) HandleShmRefCountAfterMultiPublish
```

### 3.2 Worker 侧

```text
worker1: MultiPublish 处理
  ├─ Directory 提交 (worker1→worker2)                    // RPC
  └─ 数据面 UB 写 (worker1→worker3)
      └─ UrmaWritePayload
          ├─ ds_urma_write                               // URMA
          └─ ds_urma_poll_jfc / ds_urma_wait_jfc        // URMA
```

### 3.3 涉及的系统调用与 URMA 接口

| 类别 | 接口 | 场景 |
|------|------|------|
| OS | ZMQ socket send/recv | 控制面 RPC (MultiPublish) |
| OS | `memcpy` | payload 组装 |
| URMA | `ds_urma_write` | UrmaWritePayload |
| URMA | `ds_urma_poll_jfc` / `ds_urma_wait_jfc` | 完成队列等待 |
| URMA | `ds_urma_ack_jfc` / `ds_urma_rearm_jfc` | event mode 分支 |

---

## 4. MGet 接口

### 4.1 SDK 侧

```text
ObjectClientImpl::Get(objectKeys, subTimeoutMs, buffers)
  ├─ (1) 校验 + GetAvailableWorkerApi
  └─ (2) GetBuffersFromWorker
          └─ ClientWorkerRemoteApi::Get
              ├─ PreGet
              ├─ [UB 且无 SHM] PrepareUrmaBuffer       // URMA 缓冲池
              │   失败 → WARNING + fallback TCP payload  // 不抛 URMA 码
              ├─ RetryOnError → stub_->Get              // [ZMQ RPC → worker1]
              │   lambda: last_rc 为 timeout/try_again/OOM+全失败 → 重试
              └─ FillUrmaBuffer                         // URMA: UB 回填；OS: mmap
  └─ (3) ProcessGetResponse → 各 key 的 Buffer / failed keys
```

### 4.2 Worker 侧

```text
worker1: WorkerOcServiceGetImpl::Get
  ├─ (W1-A) TryGetObjectFromLocal                       // 本地缓存命中，无跨进程
  │
  ├─ (W1→W2) QueryMetadataFromMaster → Directory Worker // RPC（gRPC/hash ring 分片）
  │   └─ workerMasterApi->QueryMeta
  │       成功: 得到 QueryMetaInfoPb（含副本 address）
  │       失败: 日志原文 "Query from master failed"
  │
  └─ (W1→W3) GetObjectsFromAnywhere                     // 远端数据拉取
      └─ GetObjectFromAnywhereWithLock
          ├─ workerStub->GetObjectRemote → clientApi     // W↔W RPC
          ├─ worker3: CheckConnectionStable              // URMA: CheckUrmaConnectionStable
          ├─ worker3: UrmaWritePayload                   // URMA: ds_urma_write / ds_urma_read
          │   └─ rsp.data_source = DATA_ALREADY_TRANSFERRED
          └─ worker1: PollJfcWait                        // URMA: ds_urma_wait_jfc / poll_jfc / ack_jfc / rearm_jfc
              ├─ [event mode] ds_urma_wait_jfc → ds_urma_poll_jfc → ds_urma_ack_jfc → ds_urma_rearm_jfc
              └─ [poll mode] 循环 ds_urma_poll_jfc + usleep(0)
  └─ ReturnToClient → last_rc + Write + SendPayload → 回到 client
```

### 4.3 涉及的系统调用与 URMA 接口

| 类别 | 接口 | 场景 | 代码位置 |
|------|------|------|---------|
| OS | ZMQ socket send/recv | 控制面 Get RPC | `client_worker_remote_api.cpp` |
| OS | `mmap(store_fd)` | SHM 映射回数 | `object_client_impl.cpp` (MmapShmUnit) |
| OS | `recvmsg(SCM_RIGHTS)` | SHM fd 回传 | `fd_pass.cpp` |
| OS | `usleep(0)` | URMA poll 等待 | `urma_manager.cpp` (PollJfcWait) |
| OS | `memcpy` | payload/UB 组装 | `client_worker_base_api.cpp` |
| URMA | `PrepareUrmaBuffer` (GetMemoryBufferHandle) | 客户端 UB 缓冲准备 | `client_worker_base_api.cpp` |
| URMA | `ds_urma_write` | 远端数据面写 | `urma_manager.cpp` (UrmaWritePayload) |
| URMA | `ds_urma_read` | 远端数据面读 | `urma_manager.cpp` |
| URMA | `ds_urma_wait_jfc` | event mode 等待完成 | `urma_manager.cpp` (PollJfcWait) |
| URMA | `ds_urma_poll_jfc` | 轮询完成队列 | `urma_manager.cpp` (PollJfcWait) |
| URMA | `ds_urma_ack_jfc` | 确认完成事件 | `urma_manager.cpp` (PollJfcWait) |
| URMA | `ds_urma_rearm_jfc` | 重新激活完成队列 | `urma_manager.cpp` (PollJfcWait) |

---

## 5. URMA 接口全量清单

来自 `urma_manager.cpp` 直接调用（经 `urma_dlopen_util.cpp` 动态绑定 UMDK `urma_*`）：

| 分类 | 接口 |
|------|------|
| 生命周期 | `urma_init`, `urma_uninit`, `urma_register_log_func`, `urma_unregister_log_func` |
| 设备发现 | `urma_get_device_list`, `urma_get_device_by_name`, `urma_query_device` |
| EID | `urma_get_eid_list`, `urma_free_eid_list` |
| 上下文 | `urma_create_context`, `urma_delete_context` |
| 完成队列 | `urma_create_jfce`, `urma_delete_jfce`, `urma_create_jfc`, `urma_delete_jfc`, `urma_rearm_jfc` |
| 发送/接收 | `urma_create_jfs`, `urma_delete_jfs`, `urma_create_jfr`, `urma_delete_jfr` |
| 内存注册 | `urma_register_seg`, `urma_unregister_seg`, `urma_import_seg`, `urma_unimport_seg` |
| JFR 导入 | `urma_import_jfr`, `urma_unimport_jfr`, `urma_advise_jfr` |
| 数据操作 | `urma_write`, `urma_read`, `urma_post_jfs_wr` |
| 完成处理 | `urma_wait_jfc`, `urma_poll_jfc`, `urma_ack_jfc` |

---

## 6. OS/syscall 全量清单

| syscall / OS 接口 | 主要位置 | 关联流程 |
|---|---|---|
| `sendmsg` / `recvmsg` | `common/util/fd_pass.cpp` | Init UDS 传 fd、读路径 SHM 回数 |
| `close` | `fd_pass.cpp`; `client_worker_common_api.cpp` | fd 生命周期管理 |
| `mmap` / `munmap` | `urma_manager.cpp`; `object_client_impl.cpp` | UB 缓冲池、SHM 映射 |
| `socket` / `connect` | `client_worker_common_api.cpp` | Init 建连 |
| `usleep` | `urma_manager.cpp::PollJfcWait` | URMA 轮询等待 |
| `memcpy` / `MemoryCopy` | `client_worker_base_api.cpp`; `object_client_impl.cpp` | payload/UB 组装 |
| `open` / `pwrite` / `fsync` | worker 侧落盘路径 | MCreate 持久化 |

---

## 7. KVClient 返回码补充：`K_RUNTIME_ERROR` / `K_RPC_UNAVAILABLE` 触发时机

> 结论先行：`KVClient` 基本不改写这两个错误码，主要由 `ObjectClientImpl` / `ClientWorkerRemoteApi` / worker 服务侧生成后透传。

### 7.1 返回路径（统一视角）

`KVClient::{MCreate|MSet|Get}` 直接调用 `impl_` 并返回 `Status rc`：
- `KVClient::MCreate` → `impl_->MCreate(...)` → `return rc`
- `KVClient::MSet` → `impl_->MSet(...)` → `return rc`
- `KVClient::Get` / `KVClient::Get(keys...)` → `impl_->Get(...)` → `return rc`

因此“KVClient 中看到的 `K_RUNTIME_ERROR` / `K_RPC_UNAVAILABLE`”，触发点通常在下游模块。

### 7.2 MCreate 触发时机

| 接口阶段 | 触发条件 | 返回码 | 关键位置 |
|---|---|---|---|
| 选路前连通性检查 | worker 断连（`workerAvailable_ == false`） | `K_RPC_UNAVAILABLE` | `listen_worker.cpp::CheckWorkerAvailable` |
| 选路前内部状态检查 | `listenWorker_` 容器异常/空指针 | `K_RUNTIME_ERROR` | `object_client_impl.cpp::CheckConnection` |
| RPC 调用 `MultiCreate` | `stub_->MultiCreate` 网络不可达/超时重试后仍失败 | 常见 `K_RPC_UNAVAILABLE` | `client_worker_remote_api.cpp::MultiCreate` + `RETRY_ERROR_CODE` |

补充：`RETRY_ERROR_CODE` 明确包含 `K_RPC_UNAVAILABLE`，即先重试，超出预算后向上返回该码。

### 7.3 MSet 触发时机

| 接口阶段 | 触发条件 | 返回码 | 关键位置 |
|---|---|---|---|
| 前置选路 | 与 MCreate 相同：worker 断连 | `K_RPC_UNAVAILABLE` | `listen_worker.cpp::CheckWorkerAvailable` |
| `MultiPublish` 控制面 RPC | `stub_->MultiPublish` 失败并且重试耗尽 | 常见 `K_RPC_UNAVAILABLE` | `client_worker_remote_api.cpp::MultiPublish` |
| 本地请求组装/校验 | 溢出、内部队列状态异常等本地运行时失败 | `K_RUNTIME_ERROR` | `client_worker_base_api.cpp`（如 `PreparePublishReq`） |

补充：`MultiPublish` 的重试集合显式包含 `K_RPC_UNAVAILABLE`、`K_RPC_DEADLINE_EXCEEDED`、`K_RPC_CANCELLED` 等。

### 7.4 MGet 触发时机

| 接口阶段 | 触发条件 | 返回码 | 关键位置 |
|---|---|---|---|
| 前置选路 | worker 断连 | `K_RPC_UNAVAILABLE` | `listen_worker.cpp::CheckWorkerAvailable` |
| 控制面 RPC 收包 | 阻塞接收超时（底层 `K_TRY_AGAIN` 在阻塞模式被转换） | `K_RPC_UNAVAILABLE` | `zmq_socket.cpp::ZmqRecvMsg` |
| worker 侧 Get 处理 | 非 RPC 错误在 worker 聚合阶段被覆盖成运行时错误 | `K_RUNTIME_ERROR` | `worker_oc_service_get_impl.cpp::CheckAndResetStatus` |
| worker 侧查元数据失败 | `QueryMetadataFromMaster` 失败后统一对外返回 | `K_RUNTIME_ERROR` | `worker_oc_service_get_impl.cpp`（`"Query from master failed"`） |
| client 汇总响应 | 全部 key 失败时返回 `rsp.last_rc` | 透传（可能为两者之一） | `object_client_impl.cpp::GetBuffersFromWorker` |

关键语义：`GetBuffersFromWorker` 在“全失败”时返回 `rsp.last_rc`；所以如果 worker 侧 `last_rc` 为 `K_RUNTIME_ERROR` 或 `K_RPC_UNAVAILABLE`，会原样出现在 KVClient 返回中。

### 7.5 两类错误的判别建议（面向排障）

| 观察到的返回码 | 更可能的边界 | 首查点 |
|---|---|---|
| `K_RPC_UNAVAILABLE` | client 与 worker / worker 与上游的网络可达性、RPC 通道 | `CheckWorkerAvailable`、`ZmqRecvMsg`、`RetryOnError` |
| `K_RUNTIME_ERROR` | 业务/流程内部状态异常、数据一致性/映射异常、worker 统一封装 | `worker_oc_service_get_impl.cpp` 的封装点、`object_client_impl.cpp::CheckConnection` |

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04-15 | 初版：MCreate/MSet/MGet/Init 调用链与系统调用/URMA 接口全量总览 |
| 2026-04-15 | 补充：KVClient 返回 `K_RUNTIME_ERROR` 与 `K_RPC_UNAVAILABLE` 的接口触发时机与关键代码路径 |
