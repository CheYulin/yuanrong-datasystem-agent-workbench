# KVCache 大规模组网资源专项分析

> 基线：upstream `main/master` at `ee711eb52baf0130752aefc1ae64159cace64b11`，提交时间 `2026-08-02 09:58:06 +0800`。
>
> 结论边界：本分析以源码、配置、部署模板为 ground truth。源码没有定义的容量、带宽、CPU 单 IO 开销、IRQ 数量不填猜测值，只标注为需外部规格或压测。

## 1. 执行记录

| 项目 | 结果 |
|---|---|
| 代码仓 | `/home/t14s/workspace/git-repos/datasystem` |
| 当前工作树 | `yche-dev@d0f53885c88e436c7f3810ade28c523e237a728c`，未切换分支 |
| 分析源码基线 | `main/master@ee711eb52baf0130752aefc1ae64159cace64b11` |
| upstream 同步 | 已执行 `git fetch main --prune` |
| remote 风险 | `origin` 为 `yche-huawei` fork，`main` 为 `openeuler/yuanrong-datasystem` upstream；本次无 push、无 retest |
| CodeGraph | `/home/t14s/.local/bin/codegraph status /home/t14s/workspace/git-repos/yuanrong-datasystem` 及核心 `query` 均失败：`unable to open database file`；未重建索引 |
| 源码复核方式 | 导出 `ee711e...` 到 `/tmp/ds-resource-ee711e`，用 `rg`、`nl -ba`、`git show` 复核行号 |

## 2. 本次专项场景输入

| 场景项 | 本次取值 | 对资源分析的影响 |
|---|---|---|
| RPC 通道 | 使用 BRPC | 正文只分析 BRPC server threads/concurrency、BRPC stub cache、BRPC channel 生命周期 |
| 业务数据路径 | 内存态 Object Cache 为主 | 正文聚焦 worker shared memory、ObjectTable、master ObjectMeta、DataPlane endpoint、日志观测 |
| 排除路径 | 用户明确排除的路径 | 不在正文展开容量、生命周期和风险；仅在附录保留源码证据，便于审核追溯 |
| 规格配置 | 双11 worker config：`C:/Users/T14S/Desktop/1111_config.json` | 所有“系统规格/场景配置”优先按该 config 实例化；源码默认值只作为源码证据，不作为正文规格 |
| RPC payload | 客户侧禁用 | 正文 RPC payload / brpc attachment data block 按 `0B/RPC` 计，不按 8MiB payload 做 burst 预算 |

## 3. 总体结论

1. 本场景生效的 KVCache 核心可定量资源来自配置项：worker shared memory、client 数、RPC session cache、BRPC server threads/concurrency、OC 线程池、URMA Jetty lane pool（仅当 UB/URMA 开启）、日志队列和日志文件上限。
2. 源码没有全局“系统最大规格”定义。大规模组网的最大节点数、总内存容量、UB/内存带宽、UB IRQ 数量、host fd 总上限来自部署资源、硬件、驱动、OS/K8s 配额，不能从源码直接推出。
3. 资源不足的主要客户面表现分三类：同步拒绝或错误码，例如 `K_OUT_OF_MEMORY`、`K_NOT_READY`、`K_URMA_CONNECT_FAILED`、BRPC `ELIMIT`；后台降级，例如 eviction、LRU evict；观测降级，例如异步日志队列满时丢旧日志。本场景排除路径的错误和降级行为仅放附录。
4. 客户面比例关系最明确的是：client 连接数受 `max_client_num` 控制；每个 object 至少占一条 worker ObjectTable entry 和一份 master ObjectMeta；对象数据共享内存约为 `dataSize + metadataSize`；URMA send lane 为进程级池，单 logical transfer 租一条 lane；DataPlaneManager 按 live worker endpoint 建连接 entry。

### 3.1 双11配置实例化结论

| 资源/逻辑 | 双11配置/源码上限 | 单项实例化占用 | 规模换算 |
|---|---:|---:|---:|
| worker shared memory | `shared_memory_size_mb=61440` | 60GiB/worker | host/container 级容量硬约束 |
| shared-memory metadata header | `max_client_num=200`、`memory_alignment=64` | `align64(200/8 + 1 + 4 + 1) = 64B/object` | 每个本地 data object 固定 64B header |
| 精确 8MiB value shared-memory real size | `metadataSize=64B`、`MALLOCX_ALIGN(64)` | xqyun main/master jemalloc 复测 `10MiB/object = 10,485,760B/object` | 裸上限 `61440/10 = 6144 objects/worker` |
| 精确 8MiB value shared-memory 损耗 | 同上 | `10MiB - 8MiB = 2MiB/object = 2,097,152B/object` | 这是 8MiB 边界 cliff；`8MiB-64B` 时 real size 为 8MiB |
| eviction reserve 后容量 | `eviction_reserve=min(61440MiB*10%,10240MiB)=6144MiB` | 可用 `55296MiB` | `floor(55296/10)=5529 objects/worker` |
| rebalance source 水位 | `rebalance_source_usage_percent=70` | `61440MiB*70%=43008MiB` | `floor(43008/10)=4300 objects/worker` |
| worker 本地 data object 匿名内存 | key=100B、本地 data object 含 `ShmUnit` 包装 | xqyun RSS 实测 `776.421B/key` | `776.421 * N local data keys` |
| worker 本地 data object 匿名内存 | 短 key 15B、本地 data object 含 `ShmUnit` 包装 | xqyun RSS 实测 `664.420B/key` | `664.420 * N local data keys` |
| master 元数据匿名内存 | key=100B、address=15B、1 location、hetero/device 不计 | xqyun generated PB + TBB RSS 实测 `699.884B/key` | `699.884 * N metadata keys` |
| master 元数据匿名内存 | key=15B、address=15B、1 location、hetero/device 不计 | xqyun generated PB + TBB RSS 实测 `587.885B/key` | `587.885 * N metadata keys` |
| BRPC server in-flight RPC | `brpc_max_concurrency=256` | payload 禁用，正文 data block `0B/RPC` | 超过 256 由 BRPC 返回 `ELIMIT` |
| BRPC server worker threads | `brpc_server_num_threads=64` | 64 threads/server process | 进程级常驻线程 |
| OC/RPC 业务线程 | `oc_thread_num=64`、`rpc_thread_num=64` | 至少两组 64 线程配置入口；实际还有子池 | 进程级常驻/业务线程池资源 |
| RPC session cache | `max_rpc_session_num=10000` | 10000 cached sessions 上限 | 进程级 LRU cache 上限，不等于 fd 精确数 |
| async update-location queue | `UPDATE_LOCATION_QUEUE_NUM=4`，merge 阈值 `300 params/task`，源码无容量上限 | key=100B、traceID=36B 时 xqyun RSS 实测 `254.976B/pending key`，按 `255B/key` | 无最大值；积压 N 个 key 为 `255*N B` |
| async resource release queue | worker 每轮最多取 `BATCH_SIZE=100`，源码无容量上限 | key=100B 时 xqyun RSS 实测 `153.805B/pending key`，按 `154B/key` | 无最大值；积压 N 个 key 为 `154*N B` |
| RPC payload / brpc attachment | 客户侧禁用 | `0B/RPC` | 不进入正文 burst 预算；若未来打开需重新按 attachment framing 计算 |

一句话：**双11主规格下，8MiB value 的第一瓶颈是 worker shared memory：70% rebalance 水位约 4300 objects/worker，eviction 前约 5529 objects/worker，物理上限约 6144 objects/worker；固定匿名元数据约 1.45KiB/key（worker key=100B + master key=100B），远小于 10MiB data real size。**

## 4. 资源表

主表仅保留本次专项生效路径；用户明确排除的路径不在正文展开，统一见附录。

**资源归属：KVCache**

| 资源项 | 系统规格 | 单个资源大小 | 场景配置 | 共享域 | 生命周期（创建时机） | 生命周期（销毁时机） | 资源影响性（资源溢出） | 资源影响性（资源不足） | 可观察手段 |
|---|---|---|---|---|---|---|---|---|---|
| BRPC用户态连接数 | 源码仅定义 server 侧 `brpc_server_num_threads=64`、`brpc_max_concurrency=128`；出向 stub cache 默认 worker `max_rpc_session_num=2048`、coordinator `4096`。BRPC 实际 pooled TCP fd 数源码未定义。 | 出向 cache key 为 `HostPort + StubType`，每 entry 创建一个 BRPC channel/stub；client 到 worker 的注册/心跳/FD 获取等直连 channel 不走 `RpcStubCacheMgr`。channel 内 socket 数由 BRPC 内部决定，源码未暴露。 | `use_brpc=true` 默认；BRPC 独占 worker address 同一端口；`brpc_enable_builtin_services=false` 默认；kvcache Helm 示例每 worker 容器 8C/32Gi，sharedMemory 30720MB。 | server 监听为进程/host 端口级；出向 stub cache 为单进程 singleton；client 直连 channel 为 client 进程级；K8s `hostNetwork` 时端口是 host 共享域。 | `RpcServer::StartBrpcServer()` 设置 BRPC options 并启动；`RpcStubCacheMgr::Init()` 创建 LRU cache；访问目标时 lazy create channel/stub；client API 初始化时创建直连 `WorkerService_BrpcGenericStub`。 | `StopBrpcServer()` Stop/Join/reset；stub cache LRU evict 或 stale socket remove；client API shutdown/destructor 释放直连 channel；进程退出释放。 | server 并发超过 `brpc_max_concurrency` 时 BRPC 立即返回 `ELIMIT`，用于避免无限 bthread 排队导致 OOM；单 RPC body 上限固定提高到 2GB，超过为 BRPC 层错误。 | 目标不可达或 socket 3s 不可用返回 `K_RPC_UNAVAILABLE`；stub LRU entry 仍被 shared_ptr 持有时移除返回 `K_TRY_AGAIN`，多次失败可变为 `K_RUNTIME_ERROR`。 | 日志 `brpc server started`、Start fail、`SLOW_RPC_STUB_GET/REMOVE`、`RPC_STUB_GET_FAIL`；可选开启 BRPC `/vars`、`/prometheus_metrics`，但默认关闭。 |
| 元数据配置 (jetty数、深度) | 元数据 RPC 本场景走 BRPC；源码没有“元数据 jetty 深度”配置。URMA send Jetty lane pool 属于数据传输资源：默认 `200`，refill extra 默认 `200`，且 `enable_urma=false` 默认关闭。 | BRPC 元数据请求不按 jetty 租用资源；若开启 UB/URMA，每个 logical URMA transfer 租用一个 send Jetty lane。 | `use_brpc=true`；`enable_urma=false` 默认；`urma_poll_size=8`；`urma_max_write_size_mb=4`。 | BRPC server 为进程/端口级；URMA manager/resource 为进程级。 | BRPC server 启动时创建线程/并发保护；URMA 仅在开启后 Init 并 prefill send lane。 | BRPC server stop/join；URMA Stop/Clear 后释放 lane pool。 | BRPC 并发超限返回 `ELIMIT`；URMA lane 池耗尽返回 `K_TRY_AGAIN`。 | BRPC 不可达返回 RPC 错误；URMA init/连接失败返回 `K_URMA_ERROR` 或 `K_URMA_CONNECT_FAILED`。 | BRPC 日志/可选 `/vars`；URMA `[URMA_SEND_LANE_POOL]`、`URMA_ELAPSED_*`、pool stats。 |
| 数据面连接数 | DataPlaneManager 按 live worker endpoint 建 entry；每 entry 持有 `WorkerRpcClient` 和一种 transporter。源码未定义集群最大 endpoint 数。 | 约为每 client 进程可达 worker 数的一阶关系：`DataPlaneEntry ~= live workerKey count`；TCP 数据面通过 `WorkerRpcClient` 调 `GetObjectRemote/BatchGetObjectRemote`，payload 走 BRPC attachment。 | `ipc_through_shared_memory=true`；`shared_memory_worker_port=0` 默认禁用 SCMTCP；`oc_shm_transfer_threshold_kb=500`；`enable_urma=false`。 | client 进程级；与目标 worker endpoint 形成跨进程资源；UDS/SCMTCP 路径受 host/container 挂载和端口影响。 | `GetOrCreateEndpoint()` lazy create entry/RPC/transporter；client register 后可建立 SHM fd socket；若开启 UB，连接握手时创建/import segment 和 target Jetty。 | `ResetDataPlane()` 关闭 data plane；`Shutdown()` 清 entries；ClientWorkerRemoteCommonApi reconnect/destructor 关闭 socket fd。 | endpoint 快照不一致时返回 `K_NOT_READY`；UB 建立失败返回 `K_URMA_CONNECT_FAILED`；fd 接收为空时日志提示可能超过 max open fd。 | worker 不可达、BRPC socket 不可用或 UB 未连接时 RPC/transport 失败，客户面表现为 Get/Set 失败或 fallback。 | `TransportGet][Connection/Reconcile]` VLOG；transport byte metrics；fd/socket 日志；`active_client_count`。 |
| 数据面连接配置 (jetty数、深度) | BRPC `CONNECTION_TYPE_POOLED`，但 pooled socket 数源码未定义；URMA send lane pool `200` + extra `200`，默认关闭。 | BRPC channel/stub entry 按 `HostPort + StubType`；DataPlane entry 按 worker endpoint；若开启 URMA，每 logical transfer 占一条 send lane。 | `use_brpc=true`；`enable_transport_fallback=true`；`enable_urma=false` 默认。 | BRPC stub cache 为进程级；URMA lane pool 为进程级。 | BRPC stub lazy create；URMA 在连接握手/lazy transfer 时建立 remote target 和 lane lease。 | stub LRU 或 shutdown 释放；URMA transfer settle 后 release lane，错误 completion retire lane。 | URMA lane 池耗尽返回 `K_TRY_AGAIN`；BRPC server 并发超限返回 `ELIMIT`。 | 连接不足或 lane 不足导致数据面失败、重试、fallback 或延迟上升。 | URMA pool stats、BRPC `/vars` 可选、RPC stub slow log、transport failure detail。 |
| 元数据内存 | 源码未给元数据内存上限。worker 本地 `ObjectTable = SafeTable<ImmutableString, ObjectInterface>`，底层是 TBB concurrent hash map；master ObjectMeta 为 `ObjectMetaPb + locations + state/timestamp`，metadata table 64 shards。 | 每 object 至少 worker 侧一条 SafeObject/ObjectInterface entry 和 master 侧一条 ObjectMeta；大小随 key 长度、protobuf 字段、location 数、副本数变化。worker entry 还包含锁、状态、`ObjCacheShmUnit` data/meta size、create time、ttl、address、life state 等，源码没有固定字节数。 | 无独立元数据 heap limit flag；本场景不把已排除的本地持久化后端计入正文资源。 | worker ObjectTable 为单 worker 进程级；master metadata owner 为 master/cluster 元数据域。 | object 创建/Publish/CreateMeta 时插入；worker service 创建 ObjectTable；master Init 加载元数据，但本场景不按已排除持久化后端口径分析。 | Delete/RemoveMeta/TTL expire/worker failure cleanup/migration cleanup 时删除；进程退出释放 heap。 | heap 膨胀没有源码级水位降级；对象数扩大时对象表、锁对象、引用表/eviction 元数据随之增长。 | heap 不足由 OS/OOM 表现，源码无固定错误；Etcd 写失败会返回或记录错误，可能影响元数据持久化/恢复。 | master/query/metadata 日志；`WORKER_OBJECT_COUNT`/`object_count`、`WORKER_ALLOCATED_MEMORY_SIZE`；Etcd queue ratio warning。 |
| 数据内存 | C++ 非测试默认 `shared_memory_size_mb=1024`，测试默认 64；CLI 默认 1024；kvcache Helm 示例 sharedMemory 30720MB。系统最大规格源码未定义。 | 对象共享内存申请约为 `dataSize + metadataSize`；metadata header 是否分配受 `oc_metadata_header=true` 影响；真实占用按 allocator real usage 统计。 | `shared_memory_populate=false`；`oc_shm_threshold_percentage=100`；`sc_shm_threshold_percentage=100`；eviction high/low `0.9/0.8`，reserve `10240MB`。 | 单 worker 进程共享内存池；OC/SC 共用 physical pool，但本场景正文只分析 Object Cache；容器 `/dev/shm` 和 K8s memory limit 是外层约束。 | Worker 初始化时 `Allocator::Init(shared_memory_size_mb * MB)`；Create/Set/Get from migration 或远端读取时分配 ShmUnit。 | Buffer release、Delete、eviction、ClearObject、ShmUnit destructor 释放；Allocator `FreeMemory` 更新 usage。 | 达到高水位触发 eviction；memory rebalance 若开启可跨 worker 迁移；逻辑 size 溢出返回 `K_RUNTIME_ERROR`。 | `AllocateMemoryForObject` OOM 时先尝试 eviction，再短退避重试；`ArenaGroup::AllocateMemory` 超过 `maxSize_` 或 extent 不足返回 `K_OUT_OF_MEMORY`，最终客户面 Set/Get/Load 失败或超时。 | `resource.log`/`kv_resource.log` 的 `shared_memory.worker_share_memory_usage`、`memory_usage`、`physical_memory_usage`、allocator metrics、object count、eviction summary log。 |
| 客户端/服务端 线程占用 | 本场景分 BRPC server 入口层和业务线程池层：server threads 默认 64、max concurrency 128；业务层 `rpc_thread_num=16`、OC `oc_thread_num=32`、OcGetThread、RemoteGetThreadPool、BatchQueryMeta、可选 BatchRemoteGet、OcDecRef 等。 | 每进程固定池加按 client/endpoint 的 listener 和 client-side pools；client 每个 `ObjectClientImpl` 会创建 async set/get/copy/pipeline/dev-delete 等池；每个 remote client 还可能有 fd 接收线程。 | kvcache Helm 示例 CPU 8；普通 Helm `rpc_thread_num=128`、`oc_thread_num=64`，这是部署示例非系统最大。 | server 进程级；client 进程/实例级；etcd/watch/coordinator dispatcher 为组件进程级。 | 进程/server/client 初始化时创建；部分 lazy start，如 heartbeat listener、remote fd 接收线程。 | Shutdown/destructor join/stop/reset；client shutdown 释放 pools；Worker/Coordinator stop 释放 server pools。 | ThreadPool `Execute()` 在池满且全忙时返回 false；`Submit()` 可排队导致 waiting 增长；BRPC 并发超限返回 `ELIMIT`；日志 async queue 满会丢旧日志。 | 线程不足导致 RPC 排队、后台回收/删除/迁移滞后、client async 请求变慢或切换延迟。 | ThreadPool `GetStatistics()`、`GetThreadPoolUsage()`、`waiting_task_num/thread_pool_usage`、Get queue latency、`resource.log` threadpool 组、worker/master/client 日志。 |
| 客户端/服务端 CPU占用 | 源码不支持“400 IOPS/每个核、每个 IO 2.5ms CPU 占用”作为结论。源码只给线程/并发/latency/perf 代理观测点，不提供 OS CPU 百分比资源模型。 | 无源码固定 CPU per IO；`2.5ms = 1/400 IOPS` 只是时间预算换算，不等于 CPU time，且异步 IO/日志/RPC 下不能直接对应。 | kvcache Helm 示例 requests/limits CPU 8；普通 Helm示例 CPU 3。 | container/host 级 CPU；线程池在进程内竞争 CPU。 | 请求执行、copy、serialize、RPC、日志等生效路径消耗 CPU；BRPC 模式下部分 service-thread-pool fanout 不启用，CPU 饱和更可能反映为 BRPC worker/concurrency 触顶或 handler exec latency 上升。 | 请求完成或后台任务完成后释放 CPU 时间片，不存在对象级 CPU 资源销毁。 | CPU 饱和表现为 threadpool waiting、latency phase 变长、slow log、heartbeat/watch 延迟、BRPC queue/exec latency 上升。 | CPU 不足导致超时、吞吐下降、BRPC 并发占满后 `ELIMIT`、后台任务滞后。 | `perf_client.GetPerfLog`、latency phase、slow log、ThreadPool usage、`waiting_task_num/thread_pool_usage`、BRPC latency metrics、K8s/OS CPU metrics、perf/eBPF/压测。 |
| 客户端/服务端 日志IO规格 | server 默认 `log_async=true`、async queue 65536、async thread 2、max log size 400MB；client 覆盖 queue 1024、max log size 100MB；worker config max log file num 5，部分 Helm 示例 25。 | 日志单条大小不固定，随日志内容、采样率、trace 字段变化；resource log 默认 interval 10000ms。 | `log_monitor=true`、`json_log_monitor=true`、`log_monitor_exporter=harddisk`；`request_sample_rate=1.0`。 | 进程级日志系统；日志目录若 hostPath 挂载则为 host 级，否则 container 级。 | `Logging::Start()` 初始化 spdlog async provider/thread pool 和 LogManager；resource collector 初始化 exporter。 | shutdown stop manager/drop provider；日志文件按 size/num/retention 滚动或清理。 | spdlog async overrun old logs，观测丢旧；日志过多消耗磁盘带宽和空间。 | 磁盘慢或满导致落盘滞后、日志缺失、exporter init/write 失败；业务一般不以日志 IO 作为强反压。 | `datasystem_worker.INFO.log`、access/request_out/resource.log、`kv_resource.log`、`kv_metrics.log`、operation log、collect_log。 |
| 客户端/服务端 内存带宽 | 源码无内存带宽数值。可配置 `shared_memory_distribution_policy`、`enable_ub_numa_affinity`、`shared_memory_populate`、`memory_alignment` 影响访问/页故障/NUMA，但不定义带宽。 | 每次内存 copy 大小随对象 dataSize；client copy pool thread 数由 env 或推荐值决定。 | `memory_alignment=64`；`shared_memory_distribution_policy=none`；`enable_thp=false`；`enable_huge_tlb=false`。 | host NUMA/内存控制器级；进程/容器共享 host 内存带宽。 | 共享内存 mmap/allocator 初始化；对象读写、copy、zero-fill、URMA whole arena register 使用内存。 | 对象释放后减少占用，但不会释放 host 内存带宽这一硬件资源。 | 内存带宽饱和无源码错误码，表现为延迟上升、copy phase 增长、CPU stall。 | 内存带宽不足导致吞吐下降；严重时 page fault/THP/HugeTLB 配置不当可导致 OOM 或分配失败。 | latency phase `client.process.memory_copy`、perf/eBPF/pcm/numastat、K8s/OS memory metrics、resource shared_memory。 |
| 客户端/服务端 磁盘IO | 本场景主动磁盘 IO 主要来自普通日志、access/request_out、resource.log、`kv_resource.log`、`kv_metrics.log`、operation log；不含用户已排除路径。共享内存 `enable_fallocate=true` 可能影响 mmap backing file 预分配/commit 行为，但源码未把它建模为持续业务落盘通道。 | 单条日志大小不固定，随 QPS、慢日志阈值、采样率、trace 字段和日志内容变化；日志文件按 max size / max num 滚动。 | server max log size 400MB；client max log size 100MB；worker config max log file num 5，部分 Helm 示例 25；resource log interval 10000ms。 | 日志目录若 hostPath 挂载则为 host 级，否则 container 级；K8s volume 为外层约束；fallocate 行为还受 tmpfs/memfd/容器存储实现影响。 | `Logging::Start()` 初始化 logger 和 LogManager；resource collector 初始化 exporter；共享内存 mmap 初始化时可能触发 fallocate。 | shutdown stop manager/drop provider；日志文件按 size/num/retention 滚动或清理；对象释放不等同于日志磁盘空间释放。 | 日志暴涨会覆盖旧异步日志、占用磁盘带宽和空间；源码未以日志 IO 对业务强反压。 | 磁盘慢或满导致落盘滞后、日志缺失、exporter init/write 失败；严重时影响问题定位。 | `datasystem_worker.INFO.log`、access/request_out/resource.log、`kv_resource.log`、`kv_metrics.log`、operation log、OS iostat、K8s volume metrics；fallocate 影响需现场 iostat/eBPF/压测确认。 |

## 5. 中间证据与推导

### 5.1 配置入口

| 配置 | 默认/示例 | 证据 | 推导 |
|---|---:|---|---|
| `shared_memory_size_mb` | C++/CLI 非测试默认 1024MB；kvcache Helm 示例 30720MB | `src/datasystem/worker/worker_oc_server.cpp:107-114`，`cli/deploy/conf/worker_config.json:30-33`，`k8s_deployment/helm_chart/deployment/values.yaml:7-42` | 源码默认不是大规格上限；kvcache Helm 是场景配置 |
| `max_client_num` | 200，范围 `[1,10000]` | `src/datasystem/worker/worker_service_impl.cpp:67-69`，`src/datasystem/common/util/validator.h:604-611` | worker 侧 client 注册和 lockId 资源上限 |
| `max_rpc_session_num` | 2048，范围 `[512,10000]` | `src/datasystem/worker/worker_oc_server.cpp:182-184`，`src/datasystem/common/rpc/rpc_stub_cache_mgr.cpp:102-125` | 出向 RPC stub cache 上限，不等于实际 fd 数 |
| `brpc_server_num_threads` / `brpc_max_concurrency` | 64 / 128 | `src/datasystem/common/flags/common_flag_define.cpp:178-185`，`src/datasystem/common/rpc/rpc_server.cpp:107-123` | server 侧线程/并发硬保护 |
| `oc_thread_num` | 32 | `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp:117-118` | worker OC service 主线程池 |
| `rpc_thread_num` | 16 | `common_flag_define.cpp`，`worker_oc_service_impl.cpp` 线程资源初始化路径 | 本场景作为业务线程池上限/子池参考，不作为已排除 RPC 通道资源口径 |
| `urma_send_jetty_lane_pool_size` / `refill_extra` | 200 / 200 | `common_flag_define.cpp:64-68` | 进程级 send lane 目标和额外容忍 |
| `log_monitor` / `json_log_monitor` | true / true | `common_flag_define.cpp:118-120`，`worker_config.json:234-240` | 默认开启 resource/JSON 观测日志 |

### 5.2 生命周期推导

| 对象/资源 | 创建路径 | 释放路径 | 与客户面对象关系 |
|---|---|---|---|
| Worker ObjectTable entry | `objectTable_->ReserveGetAndLock()`，Create/Get/Migrate 等路径插入 | `objectTable_->Erase()`，Clear/Delete/Evict/Migrate cleanup | 每个 object key 至少一条 worker 本地 entry；`ObjectTable` 底层为 `SafeTable`/TBB concurrent hash map |
| Master ObjectMeta | `OCMetadataManager::CreateMetaForBinaryFormat()` 插入 `metaShards_[shard].table` | `RemoveMeta*`、TTL expire、worker cleanup、delete mediator 清理 | 每 object 一条主元数据，locations 随副本/worker 地址增长 |
| 对象共享内存 | `ObjCacheShmUnit::AllocateMemory()` 以 `dataSize + metadataSize` 调 allocator | `ObjCacheShmUnit::FreeResources()` reset `ShmUnit`，底层 `Allocator::FreeMemory()` | 与对象 dataSize 线性相关，metadataSize 随 header/lock/ref 结构 |
| BRPC server | `StartBrpcServer()` | `StopBrpcServer()` | 每 worker/master/coordinator 进程一组，不随 client 数线性增长 |
| BRPC stub/channel | `RpcStubCacheMgr::GetStub()` lazy create | LRU evict/stale remove/shutdown | key 为 `HostPort + StubType`，受 `max_rpc_session_num` 上限 |
| DataPlane endpoint | `DataPlaneManager::GetOrCreateEndpoint()` | `ResetDataPlane()` / `Shutdown()` | 每 client 进程按 live worker endpoint 增长 |
| URMA send lane | Resource Init prefill；transfer `AcquireJetty()` | transfer settle release；错误 retire/refill | 每 logical transfer 租一条 lane |
| 日志资源 | `Logging::Start()` 初始化 async logger 和 LogManager | Shutdown drop provider/stop manager | 每进程全局，与请求量影响队列和磁盘写入速率 |

### 5.3 溢出/不足行为

| 类别 | 行为 | 源码依据 |
|---|---|---|
| 共享内存逻辑超限 | 返回 `K_OUT_OF_MEMORY`，消息含 `logical_size_limit_reached` | `src/datasystem/common/shared_memory/arena.cpp:115-121` |
| 共享内存 extent 不足 | 返回 `K_OUT_OF_MEMORY`，并打印 total limit/physical/real usage/try alloc size | `arena.cpp:133-144` |
| 分配前接近阈值 | 触发 `EvictWhenMemoryExceedThrehold()`，OOM 后退避重试 | `obj_cache_shm_unit.cpp:222-257` |
| BRPC 并发过高 | 返回 `ELIMIT`，避免 slow handler 无限排队导致 OOM | `rpc_server.cpp:117-123` |
| BRPC body 过大 | 固定提高到 2GB，超过为 BRPC 层错误 | `rpc_server.cpp:124-149` |
| URMA lane 池耗尽 | 无 idle lane 返回 `K_TRY_AGAIN`，后台 refill | `urma_resource.cpp:1445-1468` |
| 日志队列满 | async logger 使用 overrun oldest，旧日志被覆盖 | `logging.cpp` async logger 初始化路径 |
| Etcd async queue 满 | 删除 elder operation 并 warning | `object_meta_store.cpp` queue full 处理 |

## 6. 关键场景 cases

| Case | 资源关系 | 溢出/不足表现 | 建议观测 |
|---|---|---|---|
| client 数增加 | worker 侧受 `max_client_num` 控制；每 client 进程还会按可达 worker lazy 建 DataPlane entry | client 注册/连接失败，或 endpoint reconnect 增多 | `active_client_count`、worker/client 日志、`/proc/<pid>/fd`、BRPC vars |
| worker endpoint 增加 | 每 client 进程 `DataPlaneEntry ~= live worker endpoint count`；BRPC stub cache key 为 `HostPort + StubType` | `max_rpc_session_num` 触发 LRU/移除；目标不可达返回 RPC unavailable；endpoint 快照不一致返回 `K_NOT_READY` | RPC stub slow log、transport reconcile 日志、`ss/lsof` |
| 大对象/对象数增加 | 对象共享内存约 `dataSize + metadataSize`；每 object 增加 ObjectTable entry、SafeObject 锁/状态和 master ObjectMeta | shared memory OOM 返回 `K_OUT_OF_MEMORY`；OOM 前后会尝试 eviction 和短退避；metadata heap 无源码水位 | `resource.log` shared_memory、`object_count`/`WORKER_OBJECT_COUNT`、allocator metrics、eviction summary |
| BRPC 并发冲高 | server 侧 `brpc_max_concurrency=128` 作为硬保护 | 超限返回 BRPC `ELIMIT`，避免无限排队导致 OOM | BRPC vars、server 日志、RPC latency/slow log |
| 线程池排队/CPU 饱和 | BRPC/OC/client pools 在进程内竞争 container/host CPU | threadpool waiting 增长、latency phase 拉长、超时或吞吐下降 | `GetThreadPoolUsage()`、resource log、perf/eBPF、K8s CPU |
| 日志暴涨 | 异步日志 queue 和日志文件滚动是进程级；磁盘域取决于 volume/hostPath | async overrun 丢旧日志；磁盘慢/满导致观测缺失 | 日志文件大小/数量、resource log exporter 状态、iostat、K8s volume |
| 共享内存预分配 | `enable_fallocate=true` 可能影响 mmap backing file 的空间预分配/commit；是否产生磁盘 IO 取决于运行介质 | 启动或扩容阶段出现存储抖动；源码没有固定 MB/s 或错误码模型 | iostat、eBPF、容器 volume/tmpfs 指标、启动阶段日志 |
| UB/URMA 开启 | 默认 `enable_urma=false`；若开启，send lane pool 为进程级，logical transfer 租 lane | lane 耗尽 `K_TRY_AGAIN`；连接失败 `K_URMA_CONNECT_FAILED` | URMA pool stats、`URMA_ELAPSED_*`、provider counters |

## 7. 仍需外部输入的指标

| 指标 | 为什么源码不能定量 | 建议补充手段 |
|---|---|---|
| 集群最大 worker 数/最大总共享内存 | 源码和 Helm 示例没有定义系统最大规格 | 产品规格、压测报告、部署资源上限 |
| UB/URMA 带宽 | `urma_max_write_size_mb` 是单次写大小上限，不是带宽 | URMA provider counters、端到端吞吐压测 |
| 内存带宽 | 源码没有 memory controller 带宽模型 | perf/PCM/eBPF/numastat/压测 |
| UB 中断数量 | 源码只有 `urma_event_mode` 和 JFCE/poll 行为，不定义 IRQ/vector 数 | OS `/proc/interrupts`、驱动工具、硬件规格 |
| CPU 每 IO 占用 | 源码没有 CPU cycles 或 per-IO CPU ms 模型 | perf/eBPF/flamegraph、压测；区分同步路径与异步后台路径 |
| BRPC 实际 TCP fd 数 | 源码使用 pooled channel，但未暴露每 channel socket 数 | `/proc/<pid>/fd`、ss/lsof、BRPC vars、压测 |

## 8. 附录：本场景排除项源码证据

| 排除项 | 源码/配置证据 | 为什么排除 | 对正文资源表影响 |
|---|---|---|---|
| hetero-client / HeteroClient | Python `HeteroClient(..., enable_remote_h2d=False)` 默认关闭；C++ `ConnectOptions.enableRemoteH2D=false`。证据：`python/yr/datasystem/hetero_client.py:94,114,149,165`，`include/datasystem/utils/connection.h:56` | 用户场景明确 hetero-client 不使用、hetero off | 不计 HeteroClient 侧 device blob、dev publish/subscribe、MGetH2D/MSetD2H、device memory register |
| Remote H2D / HCCS | `enable_remote_h2d=false`、`remote_h2d_link_type=ROCE`、HCCS pool `4:8`；worker device ids 默认空；未启用时 `RemoteH2DManager::Init()` 直接返回。证据：`common_flag_define.cpp:79-83`，`remote_h2d_manager.cpp:52-53,142-144,584-588`，`worker_config.json:762-772` | 用户场景明确 remote h2d 不使用，HCCS 不作为本场景链路 | HCCS 带宽、HIXL buffer pool、device memory 预注册、RH2D heartbeat/comm/context 数量不作为正文容量项 |
| RocksDB | 默认配置存在，但 `rocksdb_write_mode=none` 时设置 disable 并不 `DB::Open`；worker 预初始化也在 none 时跳过；写元数据判断 `FLAGS_rocksdb_write_mode != "none"`。证据：`rocks_store.cpp:52-54,108-123`，`data_worker.cpp:190-194`，`object_meta_store.cpp:458-460` | 用户场景明确 RocksDB 不使用，正文按不使用口径处理 | 不计 RocksDB 目录容量、open files=128、background threads=16、RocksDB log/compaction/write IO |
| L2Cache | `l2_cache_type=none`；仅 `obs/sfs/distributed_disk` 会置位 storage type；storage 为 NONE 且请求写 L2 时返回 invalid。证据：`common_flag_define.cpp:29-33`，`l2_storage.cpp:24-34`，`object_persistence_api.cpp:44-52`，`worker_oc_service_crud_common_api.cpp:357-366` | 用户场景明确 L2Cache 不使用 | 不计 L2 async queue、L2 delete threads、OBS/SFS/distributed disk 容量、L2 fallback IO/队列 |
| ZMQ | `use_brpc` 缺省 true；BRPC 模式下 ZMQ server 不 bind，且跳过 ZMQ stub context 预热。证据：`common_flag_define.cpp:178-185`，`rpc_server.cpp:233-236`，`rpc_stub_cache_mgr.cpp:110-113` | 用户场景明确使用 BRPC、不使用 ZMQ | 不计 ZMQ `rpc_thread_num`、ZMQ socket/context/chunk、ZMQ worker-worker pool；正文 RPC 容量只讲 BRPC 与通用线程池 |
| Spill | `spill_directory=""` 默认禁用；Helm 默认 `spillDirectory: ""`；`IsEnabled()` 等价于目录非空；目录为空时 compact 线程退出；eviction 只有 `WorkerOcSpill::IsEnabled()` 时才走 spill 分支。证据：`worker_oc_spill.cpp:55-61,1039-1046,1135-1137`，`k8s/helm_chart/datasystem/values.yaml:97-104`，`worker_oc_eviction_manager.cpp:459-464` | 用户场景明确 Spill 不使用 | 不计 spill 目录容量、spill 文件、spill fd limit、spill 线程、spill 水位、`K_NO_SPACE` spill full；正文磁盘 IO 只保留日志等本场景仍存在项 |

## 9. 附录：`1111_config.json` dscli worker 配置影响分析

> 输入文件：`C:/Users/T14S/Desktop/1111_config.json`。本节分析该配置用于 dscli 启动 worker 时的启动、资源、性能、观测和安全影响。
>
> 证据边界：基于 `/home/t14s/workspace/git-repos/datasystem` exact HEAD `d0f53885c88e436c7f3810ade28c523e237a728c`。CodeGraph 查询仍失败 `unable to open database file`，本节结论来自 exact-HEAD 源码和配置取证。

### 9.1 总体判断

| 维度 | 结论 | 主要影响 |
|---|---|---|
| 启动成功率 | 中高风险 | `shared_memory_size_mb=61440`、`enable_urma=true`、`urma_register_whole_arena=true`、`shared_memory_distribution_policy=interleave_all_numa` 叠加后，启动期可能触发 60GiB 物理页 touch 和 whole-arena URMA 注册；现场内存、memlock/IOMMU、URMA 能力不足会启动失败或显著变慢。 |
| 网络/IPC | 中高风险 | `shared_memory_worker_port=31502` 会启用 SCMTCP FD passing，绑定 `tcp://<worker_host>:31502`；端口占用、内核协议不支持、网络策略或权限问题会在 `WorkerServiceImpl::Init()` 阶段硬失败。 |
| RPC 并发与线程 | 中风险 | BRPC worker threads 64、max concurrency 256、OC/rpc thread pools 64 会增加吞吐上限，也增加慢 handler 下排队、内存、线程和 CPU 竞争；超过 BRPC 上限会返回 `ELIMIT`。 |
| 故障检测 | 中风险 | `client_dead_timeout_s=3` 在当前源码合法，但非常激进；RPC heartbeat 3s 判死，socket heartbeat fallback 约 12s，结合 heartbeat 3s 对调度抖动、网络瞬断更敏感。 |
| 观测 | 中风险 | request/access/diagnostic sample rate 都为 0.01，请求链路常规日志大幅减少；`log_monitor_interval_ms=5000` 让 resource/metrics 写盘频率约翻倍。 |
| 安全 | 高风险 | `skip_authenticate=true` 绕过 worker 认证，`enable_etcd_auth=false` 不主动建立 etcd TLS/auth session；必须依赖外层网络隔离、mTLS/sidecar 或等价安全措施。 |

### 9.2 高风险启动前置检查

| 配置项 | 当前值 | 源码行为 | 风险/影响 | 启动前必须确认 |
|---|---:|---|---|---|
| `enable_urma` + `urma_register_whole_arena` | `true` / `true` | `enable_urma=true` 需要二进制编译 `USE_URMA`，否则 validator 直接失败；whole arena 会在 mmap 后注册整段共享内存。证据：`common_flags_validate.cpp:29-39`，`fast_transport_base.cpp:40`，`urma_resource.cpp:578` | URMA 未编译则启动失败；设备注册能力不足则 allocator/init 失败。 | worker 镜像是否带 `USE_URMA`；`ulimit -l`/memlock、IOMMU、URMA device 可注册内存上限。 |
| `shared_memory_size_mb` | `61440` | worker 将 61440MB 转为 bytes 后传给 `Allocator::Init()`。证据：`worker_oc_server.cpp:1466-1473`，`validator.h:844-854` | 参数合法但资源很重；60GiB 共享内存池会压缩系统余量。 | 每 worker cgroup memory、主机可用内存、NUMA node、`/dev/shm`/memfd 行为是否能覆盖 60GiB 加进程其它开销。 |
| `shared_memory_distribution_policy` | `interleave_all_numa` | 在 URMA whole-arena 条件下会走 NUMA 分布；实现按 chunk `mbind` 并逐页 touch。证据：`mem_mmap.cpp:97-105`，`numa_util.cpp:152` | `shared_memory_populate=false` 也不能完全避免启动期物理页触碰；启动可能变慢或 OOM。 | 容器 cpuset/mems 与 NUMA node 是否一致；是否应改为 `interleave_affinity_numa` 或 `none`。 |
| `shared_memory_worker_port` | `31502` | 非 0 时启用 SCMTCP FD passing 并 bind `tcp://<localAddress.Host()>:31502`。证据：`worker_service_impl.cpp:117-127` | 端口占用、协议不支持或网络策略阻断会硬失败。 | 31502 每节点唯一未占用；内核/镜像支持 `IPPROTO_SCMTCP` 518；网络策略放通。 |
| `ready_check_path` / `health_check_path` | `/export/Logs/datasystem/probe/ready` / `healthy` | worker 启动/健康检查会创建、删除、chmod 探针文件；dscli 还会轮询 ready 文件。证据：`worker_health_check.cpp:36`，`worker_oc_server.cpp:2204`，`cli/start.py:520` | 目录不可写会导致启动超时或失败。 | `/export/Logs/datasystem/probe` 存在且 worker 用户可写、可删、可 chmod。 |

### 9.3 运行时容量与性能影响

| 配置项 | 当前值 | 影响 | 风险表现 | 建议观测 |
|---|---:|---|---|---|
| `brpc_server_num_threads` / `brpc_max_concurrency` | `64` / `256` | 每个 BRPC server 使用 64 worker threads，最多 256 in-flight RPC；超限 BRPC 返回 `ELIMIT`。证据：`rpc_server.cpp:107-123` | 慢 handler 下允许更多并发，内存和排队压力更高；超限请求失败/重试。 | BRPC vars、`ELIMIT`/`K_RPC_CANCELLED` 日志、RPC latency、threadpool usage。 |
| `oc_thread_num` / `rpc_thread_num` | `64` / `64` | OC Get、parallel_for、RemoteGet、BatchQueryMeta、BatchRemoteGet 等多组业务线程池叠加，不是总线程只有 64。证据：`worker_oc_service_impl.cpp:439-443`，`worker_oc_service_get_impl.cpp:169-171` | 线程数、CPU、pid limit、上下文切换增加。 | `waiting_task_num/thread_pool_usage`、CPU、pid/thread 数、Get queue latency。 |
| `max_rpc_session_num` | `10000` | 合法最大值，初始化 RPC stub LRU cache 上限。证据：`validator.h:1078-1088`，`worker_oc_server.cpp:1483` | 大规模 worker/master/coordinator 连接面下 channel/socket/fd/memory 上界变高。 | `/proc/<pid>/fd`、`ss/lsof`、RPC stub slow log、RSS。 |
| `max_client_num` | `200` | worker client lock id 上限；超过明确拒绝。证据：`worker_service_impl.cpp:67-69`，`client_manager.cpp:315-321` | client 超过 200 后注册失败；DecRef 线程上限也受其影响。 | `active_client_count`、RegisterClient 错误、client 数量。 |
| `oc_shm_transfer_threshold_kb` | `256` | 下发给 client，低于默认 500KB，更容易走 client-worker SHM。证据：`worker_service_impl.cpp:75`，`worker_service_impl.cpp:352` | 更多 fd/shm 引用和共享内存压力；小对象路径可能收益。 | fd/socket、SHM usage、client/worker copy latency。 |
| `enable_memory_rebalance` | `true` | source ready/active 且 usage >= 70% 才会调度；60GiB 下约 42GiB used 成为 source 候选。证据：`memory_rebalance_scheduler.cpp:99-144,430` | 迁移与业务、URMA/CQE/网络争抢。 | rebalance logs、migration latency、URMA stats、shared memory usage。 |
| `data_migrate_rate_limit_mb` | `3072` | 迁移限速 3GiB/s 级别，同时影响 master timeout 和 worker limiter。证据：`memory_rebalance_scheduler.cpp:505`，`migrate_data_handler.cpp:50` | 迁移高峰消耗大量网络/UB/CPU。 | 迁移吞吐、网络/UB 带宽、业务 P99。 |
| `enable_data_replication` | `true` | remote read 后可注册热副本 location；这是性能优化，不是可靠性机制。证据：`oc_metadata_manager.cpp:1230`，`worker_oc_service_get_impl.cpp` 相关分支 | 额外共享内存占用、metadata location 膨胀、删除/失效链路压力。 | object count/location 数、shared memory usage、delete/invalidate latency。 |

### 9.4 故障检测、日志与安全影响

| 配置项 | 当前值 | 源码行为/影响 | 风险 | 建议 |
|---|---:|---|---|---|
| `client_dead_timeout_s` | `3` | 当前源码生产最小值实际为 3；RPC heartbeat 3s 判死，socket heartbeat fallback 约 12s。证据：`client_info.cpp:30-51,101-125` | 对短暂调度抖动、网络瞬断敏感，可能误判 client lost。 | 压测/混部环境先观察 client lost、RegisterLostHandler、fd reconnect。 |
| `heartbeat_interval_ms` / `node_timeout_s` / `node_dead_timeout_s` | `3000` / `30` / `35` | heartbeat 3s 合法；lost 30s，dead 35s，只留 5s 确认窗口。证据：`etcd_keep_alive.cpp:36`，`node_descriptor.cpp:48`，`worker_oc_server.cpp:1178` | 对 etcd 抖动、GC/CPU 抢占、leader 切换容忍度低。 | 对齐现场最坏 pause 和网络抖动；必要时拉大 dead-timeout 窗口。 |
| `request_sample_rate` / `access_sample_rate` / `diagnostic_sample_rate` | `0.01` / `0.01` / `0.01` | 显式采样；请求内 access/diagnostic 理论保留约 `0.01 + 0.99*0.01 = 1.99%`，FATAL/非请求 trace 旁路保留。证据：`logging.cpp:213-244`，`log_sampler.cpp` | 正常期取证显著减少，偶现问题可能缺链路日志。 | 灰度期或问题定位期临时提高采样；依赖 metrics/slow/error 兜底。 |
| `log_monitor_interval_ms` | `5000` | resource/metrics 采集从默认 10s 提升到 5s。证据：`res_metric_collector.cpp:148`，`metrics.cpp:376` | 观测粒度更好，但写盘和采集开销约翻倍。 | 关注 `resource.log`、`kv_resource.log` 生成量和 IO。 |
| `max_log_size` / `max_log_file_num` / `log_compress` / `log_retention_day` | `400MB` / `20` / `false` / `0` | 单类滚动文件约 8GB 未压缩；不按时间删除，仅按数量裁剪。证据：`log_manager.cpp:207,231` | 异常期日志占盘高；旧日志被数量裁剪。 | 确认 `/export/Logs/kvcache/logs` 容量和采集器策略。 |
| `skip_authenticate` | `true` | worker 认证入口提前返回，绕过 token/AK/SK/tenant/system auth；带 tenant_id 的请求还可能被拒。证据：`authenticate.cpp:25`，`authenticate.h:47-92` | 生产安全高风险；租户/认证语义变化。 | 仅在可信网络或外层 mTLS/sidecar/网络策略完整时使用。 |
| `enable_etcd_auth` | `false` | 不主动创建 etcd TLS/auth session。证据：`grpc_session.h:100` | etcd 控制面依赖外层隔离。 | 确认 etcd listener 暴露面、ACL、TLS 策略。 |
| `rocksdb_write_mode` | `none` | 跳过 RocksDB pre-init、DB open 和 liveness check。证据：`data_worker.cpp:190-194`，`rocks_store.cpp:108-123`，`worker_liveness_check.cpp:264-267` | 避免 RocksDB LOCK/IO 风险；但不提供 RocksDB 元数据落盘语义。 | 确认当前场景接受无 RocksDB。 |

### 9.5 启动前检查清单

| 检查项 | 通过标准 |
|---|---|
| URMA 构建 | worker 二进制确认为 `USE_URMA` 构建；启动日志无 “not build with URMA framework”。 |
| 60GiB 内存预算 | cgroup memory、主机可用内存、NUMA、memlock/IOMMU、URMA 注册上限能覆盖 60GiB whole-arena 注册和进程其它开销。 |
| NUMA 策略 | `interleave_all_numa` 与容器 cpuset/mems、UB chip/NUMA 拓扑匹配；否则考虑 `interleave_affinity_numa` 或 `none`。 |
| SCMTCP 端口 | 31502 未占用，协议 518 可用，网络策略放通，多个 worker 不冲突。 |
| 探针/日志目录 | `/export/Logs/datasystem/probe`、`/export/Logs/datasystem/config`、`/export/Logs/kvcache/uds`、`/export/Logs/kvcache/logs` 权限和容量满足要求。 |
| 安全边界 | `skip_authenticate=true` 与 `enable_etcd_auth=false` 已由外层网络隔离、mTLS/sidecar、ACL 或等价机制兜底。 |
| 故障检测窗口 | 3s client timeout、30s lost、35s dead 能覆盖现场最坏 GC、CPU 抢占、网络抖动和 etcd leader 切换。 |
| 观测策略 | 1% 采样符合排障要求；灰度/压测期准备好动态提升采样或额外采集 metrics/BRPC vars。 |

## 10. 审核问题

1. 表格中的“系统规格”是否需要严格只填源码/配置值，还是可以额外引入产品规格书或硬件规格？
2. kvcache Helm 示例 `8C/32Gi/sharedMemory 30720MB` 是否就是本专项的“场景默认/典型配置”，还是还需要对齐线上 `xqyun-32c32g` 的实际 values？
3. 后续发布到 yche.me 前，建议补一轮运行态采样：`resource.log/kv_resource.log`、`/proc/<pid>/fd`、`ss -antp`、`iostat`、`perf` 或 eBPF，以把源码配置转成实测规格。
