# 进程视图（4+1）

本文档基于 **`yuanrong-datasystem`** 源码，梳理 **Client 进程** 与 **Worker 进程** 的职责边界、Worker 内「本地缓存 vs 分布式元数据」的分工，以及 **线程模型（pthread + `ThreadPool`）**、**IPC** 与典型 **后台任务**。可与 [开发视图](./development.md) 对照。

**说明**：代码中 **未使用 bthread**；并发主要依赖自研 **`datasystem::ThreadPool`**（底层为 `std::thread`，线程名通过 `pthread_setname_np` 设置），以及少量独立 `Thread` / 回调线程。

---

## 1. 进程粒度：谁跑什么

| 进程/形态 | 角色 | 源码锚点 |
| --- | --- | --- |
| **Client（用户进程）** | 嵌入应用或独立进程，加载 SDK（C++/Python 等），通过连接/服务发现绑定 Worker；`DsClient` 聚合 `KVClient`、`HeteroClient`、`ObjectClient`。 | `src/datasystem/client/datasystem.cpp`（`DsClient::Init` / `ShutDown`） |
| **Worker（`datasystem_worker`）** | 独占进程：内存/缓存服务、与 Client 的共享内存与 RPC、参与集群与元数据协调；同进程内链接 **master**（`ds_master`）与 **server**（`ds_server`）库完成组装。 | `src/datasystem/worker/worker_main.cpp` → `Worker::Init`；`src/datasystem/worker/worker_oc_server.cpp` |
| **嵌入式 Worker（可选）** | 同一进程内同时跑 Client 与 Worker 插件（测试/特殊部署）。 | `ObjectClientImpl::InitEmbedded`、`EmbeddedClientWorkerApi`（`src/datasystem/client/object_cache/object_client_impl.cpp`） |

---

## 2. Worker 进程内的两类逻辑

### 2.1 本地缓存与数据面

Worker 侧在 **对象缓存（Object Cache）**、**流缓存（Stream Cache）** 等子系统中维护 **本地元数据与数据页、淘汰、二级缓存、异步回写/删除** 等，例如：

- **对象服务**：`WorkerOCServiceImpl` 初始化多种线程池（见下节）、`eviction_manager`、L2、`SlotRecoveryManager`、`MetaDataRecoveryManager` 等（`src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`）。
- **淘汰与 Spill**：`WorkerOcEvictionManager` 使用多类独立池：`MemEvictionThread`、`SpillEvictionThread`、`MasterTaskThread`、`SpillThread`、`scheduleEvictThread`（`worker_oc_eviction_manager.cpp`）。

这些路径主要回答：**本节点上数据放哪里、何时淘汰、如何与共享内存中的对象条目对齐**。

### 2.2 分布式化元数据与控制面

同一 Worker 进程通过 **etcd / Metastore**、**一致性哈希 / 哈希环事件**、**集群节点表** 等与集群对齐，回答：**键路由到谁、节点上下线、扩缩容、副本与元数据修复** 等。

- **集群与 etcd**：`EtcdClusterManager` 订阅/维护节点与集群状态，并与哈希环等事件联动（`src/datasystem/worker/cluster_manager/etcd_cluster_manager.cpp`）。
- **与 master 协同**：`WorkerOCServiceImpl::Init` 中初始化 `NodeSelector`、`HashRingEvent` 订阅、`slotRecoveryManager_` 等，将本地行为与 **全局元数据与路由** 连接（`worker_oc_service_impl.cpp`）。
- **元数据存储抽象**：worker/master 侧通过 `etcdStore_`、`EtcdStore` 等与 kvstore 后端交互（见 `common/kvstore`）。

**小结**：开发视图上是「worker + master 库」同进程装配；运行时在逻辑上仍可区分 **本地缓存管理**（数据面/资源）与 **分布式元数据与集群事件**（控制面/路由）。

---

## 3. 线程模型：核心线程资源

### 3.1 统一线程池：`ThreadPool`（pthread 语义）

`ThreadPool` 使用 **`Thread` 封装 `std::thread`**，工作线程循环从队列取任务执行；支持 **最小/最大线程数**、可选 **idle 收缩**（`droppable`）、`Submit`（带 `future`）与 `Execute`（fire-and-forget）。

```93:98:yuanrong-datasystem/src/datasystem/common/util/thread_pool.cpp
void ThreadPool::AddThread()
{
    auto thread = Thread([this] { this->DoThreadWork(); });
    thread.set_name(name_);
    std::lock_guard<std::shared_timed_mutex> workerLock(workersMtx_);
```

```80:86:yuanrong-datasystem/src/datasystem/common/util/thread.h
    void set_name(const std::string &name)
    {
        const size_t taskCommLen = 15;
        auto truncateName = name.substr(0, taskCommLen);
        auto handle = thread_.native_handle();
        (void)pthread_setname_np(handle, truncateName.c_str());
    }
```

调度 **不是** bthread M:N；不同业务用 **不同名字的池** 隔离负载，便于在 `top`/profiler 中按线程名区分。

下文将线程池按 **三大类** 归纳：**基础 RPC / 连接服务**、**对象缓存**、**流缓存**。每类下再列 **细分子池**（均以 `ThreadPool(..., "name")` 中的 **name** 或源码变量为准）。另外还有少量 **跨模块公共池**（如并行框架）和 **Client 侧传输握手**，附在各类末尾。

### 3.2 基础 RPC 与连接服务类

面向 **ZMQ 服务入口、连接建立、消息派发**，与具体业务（Object/Stream）解耦较清晰。

| 细分子池 | 典型命名 / 位置 | 用途摘要 |
| --- | --- | --- |
| **服务侧请求线程池** | `InitThreadPool()` 中线程名取 **`ServiceName()`**；规模与 `numRegularSockets_`、`numStreamSockets_` 相关 | ZMQ 后端从队列取任务、处理进入该服务的 RPC（`zmq_service.cpp`） |
| **Work agent 池** | `workAgentThreadPool_` | 将 work agent 的 `Run` 投递到池内线程执行，与前端/路由配合（`zmq_service.cpp`） |
| **连接侧线程池** | `"ZmqHandleConnect"`（`zmq_stub_conn.cpp`） | Stub 侧处理建连等阻塞型工作，与服务端池分离 |

### 3.3 对象缓存处理类（Object Cache）

面向 **KV/Object 语义、共享内存数据路径、淘汰、恢复与异构**；Worker、Master 相关逻辑与 **Client 侧异步** 均可能建池。

| 层级 | 细分子池 | 典型命名 / 位置 | 用途摘要 |
| --- | --- | --- | --- |
| **主路径与拷贝** | 通用 Get / 拷贝 | `OcGetThread`、`memCpyThreadPool_`（`WorkerOCServiceImpl`） | 对象服务主并发、内存拷贝 offload |
| | **并行分片** | `parallel_for`（`Parallel::InitParallelThreadPool`，在 OC 初始化中调用） | `parallel_for` 计算并行（`parallel_for_local.cpp`）；与 OC 初始化参数绑定 |
| **Get 子路径（批量/远端）** | 批量查 meta / 远端 get | `RemoteGetThreadPool`、`BatchQureyMeta`、`BatchRemoteGet`（`worker_oc_service_get_impl.cpp`） | 批量与远端拉取拆分线程，避免单池拥塞 |
| **生命周期与引用** | 清理 / 减引用 | `OcCleanClient`、`OcDecRef` | 按客户端聚合的清理、引用计数减少后台队列 |
| **异构设备** | 设备侧 RPC | `devThread`（规模受 `BUILD_HETERO` 等影响） | 与 `AsyncRpcRequestManager` 配合的异构请求路径 |
| | 旧版本删除 | `oldVerDelAsyncPool_`（等） | 异步删除旧版本对象（`worker_oc_service_impl` 初始化段） |
| **淘汰与持久化溢出** | 多池拆分 | `MemEvictionThread`、`SpillEvictionThread`、`MasterTaskThread`、`SpillThread`、`scheduleEvictThread` | 内存淘汰、落盘/spill、与 master 协同任务、定时阈值扫描（`worker_oc_eviction_manager.cpp`） |
| **恢复与元数据一致性** | Slot / 延迟重试 | `SlotRecoveryTask`、`SlotMetaRetry` | 二级存储与 slot 恢复、元数据重试队列（`slot_recovery_manager.cpp`） |
| **Worker–Worker 对象侧** | 通信初始化等 | `CommInit`（`worker_worker_oc_service_impl.cpp`） | Worker 间对象通信相关初始化任务 offload |
| **Client 侧（用户进程）** | 异步 API | `async_set`、`async_get_copy`、`async_get_rpc`、`switch`、`async_release_buffer` 等 | 异步 Set/Get RPC、拷贝、切换 Worker、缓冲释放（`object_client_impl.cpp::ConstructTreadPool`） |
| | 内存拷贝与并行 | `memoryCopyThreadPool_` + `Parallel::InitParallelFor` 环境变量支路 | 与控制面并行度、按 key 拷贝线程数等（同文件 `InitParallelFor`） |

**说明**：`MetaDataRecoveryManager`、`clearDataFlow` 等仍以 **OC 线程池或 Execute 投递** 驱动，归类上属对象缓存控制面，不单独占一类新池名时从属于上表各类子池。

### 3.4 流缓存处理类（Stream Cache）

面向 **流式数据、页缓冲、跨 Worker 流式协同与 Master 侧流元数据**；**Worker / Master / Client** 各有独立命名，避免与 OC 混淆。

| 层级 | 细分子池 | 典型命名 / 位置 | 用途摘要 |
| --- | --- | --- | --- |
| **Worker 上 SC 服务** | 主服务异步 | `ScThreads` | 流缓存服务上异步请求处理（`client_worker_sc_service_impl.cpp`） |
| | 共享内存/大内存分配 RPC | `memThreads` | CreateShm/AllocBigShm 等易阻塞路径单独池，与主服务池隔离 |
| | ACK / 轻量 GC | `ackThreads` | ACK 与周期 `AutoAckImpl` 类逻辑，小固定线程数 |
| **Worker 上流数据与缓冲** | 远端协调扫描 | `RemoteWorkerManager`（`StreamDataPool`） | 流变更扫描、分区任务（`stream_data_pool.cpp`） |
| | 分区内缓冲 | `buffer_pool.cpp` 内按 **分区构造** 的具名 `ThreadPool` | 各 partition 内后台任务与 buffer 子系统绑定 |
| | 用量监控 | `ScUsageMonitor`（`usage_monitor.cpp`） | 流producer阻塞等监控路径 |
| **Master 上流元数据** | 主服务 | `MScThreads` | Master 侧流缓存服务线程（`master_sc_service_impl.cpp`） |
| | 检查类 | `MScCheck` | 与节点集合规模相关的检查并发（同文件） |
| | 异步协调 | `MScAsyncReconcilation`（`sc_metadata_manager.cpp`） | 流元数据异步对账 |
| | 通知 / 删除 | `ScNotify`、`ScDelete`（`sc_notify_worker_manager.cpp`） | 通知 Worker 与删流异步化 |
| | 迁移 | `ScMigrateMetadata`（`sc_migrate_metadata_manager.cpp`） | 流元数据迁移任务 |
| **Client 上流** | 预取 | `stream_client_impl.cpp` 中 `prefetchThdPool_`（构造时 `ThreadPool`） | 预取等客户端侧并发 |

### 3.5 跨模块与传输补强（非三大族核心业务池）

| 细分子池 | 典型命名 | 用途摘要 |
| --- | --- | --- |
| **URMA 握手（Client）** | `urma_handshake`、`urma_handshake_retry` | 与 `ClientWorkerCommonApi` 路径绑定，属传输层而非 OC/SC 语义（`client_worker_common_api.cpp`） |
| **RDMA 资源** | 如 `RetireJfs`（`urma_resource.cpp`） | 资源回收类后台队列 |

Worker 主线程在 `worker_main.cpp` 中 **阻塞等待终止信号**，循环中驱动 `PerfManager::Tick()`、`metrics::Tick()` 等，**业务请求不由该主线程直接处理**，而是由上述池与 ZMQ 路径承接。

```65:78:yuanrong-datasystem/src/datasystem/worker/worker_main.cpp
        std::unique_lock<std::mutex> termSignalLock(g_termSignalMutex);
        while (!IsTermSignalReceived()) {
            bool signalReceived = g_termSignalCv.wait_for(termSignalLock, std::chrono::milliseconds(CHECK_EVERY_MS),
                                                          [] { return IsTermSignalReceived(); });
            if (signalReceived) {
                break;
            }
            auto elapsedMs = timer.ElapsedMilliSecondAndReset();
            if (elapsedMs > REPORTING_THRESHOLD_MS) {
                LOG(ERROR) << FormatString("Worker was hanged about %.2f ms", elapsedMs);
            }
            if (perfManager != nullptr) {
                perfManager->Tick();
            }
            metrics::Tick();
```

### 3.6 Client 侧补充线程（非 ThreadPool 独占）

除多个 `ThreadPool` 外，`ObjectClientImpl::InitClientWorkerConnect` 还会启动 **`ListenWorker`**（worker 失联/切换）、**`StartShmRefReconcileThread`**、**`StartPerfThread`**、**`StartMetricsThread`** 等独立线程（同文件 `InitParallelFor` 前后），用于 **共享内存引用对账与可观测性**，与业务线程池并列存在。

```322:330:yuanrong-datasystem/src/datasystem/client/object_cache/object_client_impl.cpp
void ObjectClientImpl::ConstructTreadPool()
{
    const size_t threadCount = 8;
    asyncSetRPCPool_ = std::make_shared<ThreadPool>(0, threadCount, "async_set");
    asyncGetCopyPool_ = std::make_shared<ThreadPool>(0, threadCount, "async_get_copy");
    asyncGetRPCPool_ = std::make_shared<ThreadPool>(0, threadCount, "async_get_rpc");
    asyncSwitchWorkerPool_ = std::make_shared<ThreadPool>(0, 1, "switch");
    asyncDevDeletePool_ = std::make_shared<ThreadPool>(0, threadCount);
    asyncReleasePool_ = std::make_shared<ThreadPool>(0, 1, "async_release_buffer");
}
```

---

## 4. IPC 与传输路径（结合代码）

| 机制 | 作用 |
| --- | --- |
| **共享内存 + FD 传递** | Client 与本地 Worker 之间通过注册/心跳后建立的 **mmap 与共享内存单元** 读写大体量数据；FD 通过 **Unix domain socket 等** 传递（`unix_sock_fd`、`fd_pass`、`fd_manager`，见 `client_worker_common_api.cpp` 及 worker 侧注册逻辑）。`ShmEnableType` 区分 UDS / SCMTCP / 纯 TCP 等（同文件）。 |
| **ZMQ RPC** | 控制面与服务间请求大量使用 **ZMQ**（`common/rpc/zmq`）：服务端 `ZmqService` 结合 **线程池** 与 **work agent 池** 处理消息；Stub 侧有连接与线程池（如 `ZmqHandleConnect`）。 |
| **可选 URMA / RDMA** | `BUILD_WITH_URMA` 等编译选项打开时，`common/rdma` 与 **握手线程池** 参与；与 **共享内存主路径** 并行存在，服务于高性能网络传输。 |

---

## 5. 后台任务（非请求热路径）举例

- **集群与元数据**：etcd watch、节点超时、哈希环变更订阅、缩容/节点退出时的 voluntary exit 处理（`WorkerOCServiceImpl` 中 `HashRingEvent`、`EraseFailedNodeApiEvent` 等订阅者）。
- **恢复与一致性**：`SlotRecoveryManager`、`MetaDataRecoveryManager`、异步回滚/持久化相关 manager。
- **淘汰与垃圾回收**：`WorkerOcEvictionManager` 定时 `EvictWhenMemoryExceedThrehold`、流侧 `AutoAckImpl` 循环（`ackPool_` / GC 间隔）。
- **Client**：共享内存引用定期 reconcile、perf/metrics 上报线程。

---

## 6. 相关源码路径（速查）

| 主题 | 路径 |
| --- | --- |
| 线程池实现 | `src/datasystem/common/util/thread_pool.{h,cpp}`、`common/util/thread.h` |
| Worker 主进程 | `src/datasystem/worker/worker_main.cpp`、`worker.cpp` |
| 对象缓存服务与池初始化 | `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp` |
| ZMQ 服务与池 | `src/datasystem/common/rpc/zmq/zmq_service.cpp` |
| Client 异步池与并行 | `src/datasystem/client/object_cache/object_client_impl.cpp` |
| 并行框架 | `src/datasystem/common/parallel/detail/parallel_for_local.cpp` |
| 流缓存 Worker 池 | `src/datasystem/worker/stream_cache/client_worker_sc_service_impl.cpp`、`stream_data_pool.cpp`、`buffer_pool.cpp`、`usage_monitor.cpp` |
| 流缓存 Master 池 | `src/datasystem/master/stream_cache/master_sc_service_impl.cpp`、`sc_metadata_manager.cpp`、`sc_notify_worker_manager.cpp`、`sc_migrate_metadata_manager.cpp` |
| Client 流预取池 | `src/datasystem/client/stream_cache/stream_client_impl.cpp` |
| 集群 / etcd | `src/datasystem/worker/cluster_manager/etcd_cluster_manager.cpp` |
| Client↔Worker 通用 IPC | `src/datasystem/client/client_worker_common_api.cpp` |

---

*若与最新源码不一致，以 `yuanrong-datasystem` 为准并回写本节。*
