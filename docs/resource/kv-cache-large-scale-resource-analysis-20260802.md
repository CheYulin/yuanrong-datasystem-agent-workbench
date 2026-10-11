# KVCache 大规模组网资源专项分析（双11配置版）

> 基线：datasystem local `HEAD=d0f53885c88e436c7f3810ade28c523e237a728c`，upstream main/master 同步基线 `ee711eb52baf0130752aefc1ae64159cace64b11`；配置：`C:/Users/T14S/Desktop/1111_config.json`。  
> CodeGraph CLI：`codegraph status /home/t14s/workspace/git-repos/datasystem` 返回 `Not initialized`，本轮不 init/reindex；结论以 exact source `rg/nl` + xqyun probe 为 ground truth。

## 0. 场景边界

- 主场景开启：BRPC、shared memory IPC、URMA worker-worker data path、data replication、memory rebalance、异步日志/资源日志。
- 主场景排除：hetero-client、remote H2D/HCCS、RocksDB、L2Cache、Spill、ZMQ、引用计数/订阅、RPC 对象数据 payload。
- RPC 口径：客户侧禁用对象数据 RPC payload，正文 data block 按 `0B/RPC`；metadata/control PB 只作为控制面包体/延迟风险，不进入对象数据容量预算。

## 1. 关键结论

- 数据共享内存是第一瓶颈：`shared_memory_size_mb=61440`，精确 `8MiB value + 64B metadata header` 经系统 jemalloc 实占 `10MiB/object`，损耗 `2MiB/object`；这是 exact 8MiB 落点的 size-class cliff，不代表所有 value 固定 25% 损耗。
- 同一探针验证：`8MiB - 64B value + 64B header` 实占 `8MiB/object`，只损耗 `64B`；因此实际规格要按 value size 分桶测算。
- 双11 8MiB exact 口径：物理上限 `floor(61440MiB/10MiB)=6144 objects/worker`；eviction reserve `min(61440*10%,10240)=6144MiB`，eviction 前约 `5529 objects/worker`；rebalance source 70% 水位约 `4300 objects/worker`。
- key=100B 口径固定匿名元数据：worker 本地 data object `776.421B/key`，master metadata `699.884B/key`，合计 `1476.305B/key`；对 8MiB value 占比很小，但 key 数决定 master/worker heap 规格。
- 无界异步队列需要单独预算：AsyncUpdateLocation `255B/pending key`，AsyncResourceReleaser `154B/pending key`；源码没有容量上限，积压会线性涨 RSS。

## 2. 1111 配置实例化

| 类别 | 1111配置 | 对资源分析的实例化 |
|---|---|---|
| shared memory | `shared_memory_size_mb=61440`、`memory_alignment=64`、`arena_per_tenant=1`、`enable_fallocate=false`、`shared_memory_distribution_policy=interleave_all_numa` | worker 对象池 `60GiB`；对象分配按 64B alignment + jemalloc size class；whole arena/NUMA interleave 影响 host 内存域。 |
| 客户端/SHM | `max_client_num=200`、`ipc_through_shared_memory=true`、`shared_memory_worker_port=31502`、`oc_shm_transfer_threshold_kb=256`、`unix_domain_socket_dir=/export/Logs/kvcache/uds` | metadata header `64B/object`；SCMTCP/UDS fd 传递；client 相关 fd 缓存按 `max_client_num*10=2000` 上界。 |
| BRPC/RPC | `use_brpc=true`、`brpc_server_num_threads=64`、`brpc_max_concurrency=256`、`rpc_thread_num=64`、`max_rpc_session_num=10000` | BRPC server bthread worker 64；in-flight 超 256 立即 ELIMIT；BRPC 模式不按 ZMQ service socket/thread pool 建业务线程。 |
| OC线程 | `oc_thread_num=64`、`max_client_num=200` | `OcGetThread=64`；memcopy pool 16；parallel_for core 8/max64；GC 4；`OcDecRef` min1/max7。 |
| URMA | `enable_urma=true`、`urma_max_write_size_mb=4`、`urma_register_whole_arena=true`、`enable_ub_numa_affinity=true` | send lane pool 默认 200 + refill extra 200；8MiB value 若走 URMA write 至少 2 个 4MiB chunk；硬件带宽/IRQ 源码不给常量。 |
| 热副本/再均衡 | `enable_data_replication=true`、`enable_memory_rebalance=true`、`rebalance_source_usage_percent=70`、`rebalance_usage_gap_percent=20`、`data_migrate_rate_limit_mb=3072`、`enable_worker_worker_batch_get=true` | 远端读成功可能落本地副本并异步更新 location；worker 达 70% 使用率可作为 source，迁移限速 3072MB/s 是外部配置上限。 |
| eviction | `eviction_reserve_mem_threshold_mb=10240` | 高水位按 `max(90%*memory, memory-reserve)`；60GiB 下 reserve 取 6144MiB，和 90% 高水位一致。 |
| 日志/观测 | `log_async=true`、`log_async_queue_size=65536`、`log_monitor=true`、`json_log_monitor=true`、`log_monitor_exporter=harddisk`、`log_monitor_interval_ms=5000`、`max_log_size=400MB`、`max_log_file_num=20`、`request/access/diagnostic_sample_rate=0.01` | spdlog async queue 65536 + 2 async worker；LogManager 1；resource/json exporter flush thread；单 severity 滚动约 8GB。 |
| 口径排除 | `rocksdb_write_mode=none`；用户确认 hetero/remote H2D/l2cache/spill/ref/subscription/RPC payload 不用 | 正文容量预算不计 RocksDB/L2/Spill/HCCS/hetero/device/ref/subscription/RPC object payload。 |

## 3. 资源归属：KVCache

| 资源项 | 系统规格 | 单个资源大小 | 场景配置 | 共享域 | 生命周期（创建时机） | 生命周期（销毁时机） | 资源影响性（资源溢出） | 资源影响性（资源不足） | 可观察手段 |
|---|---|---|---|---|---|---|---|---|---|
| BRPC用户态连接数 | `brpc_max_concurrency=256`；`brpc_server_num_threads=64`；`max_rpc_session_num=10000`；BRPC body上限源码设为2GB | 对象数据 payload 禁用：`0B/RPC`；metadata/control PB 不计入对象数据容量 | `use_brpc=true` | 进程级/host端口级 | RpcServer启动并注册BRPC service；StubCache lazy create | Stop/Join；LRU evict；进程退出 | 超过256 in-flight 返回 ELIMIT，阻止无限排队/OOM | 目标不可达或 session cache 压力导致重建、延迟、`K_RPC_UNAVAILABLE` | BRPC vars(若开启)、RPC slow log、`/proc/<pid>/fd`、`ss/lsof` |
| 元数据配置(jetty数、深度) | 元数据RPC走BRPC，无 metadata jetty 深度；URMA jetty 属于数据传输 | 元数据RPC不租 jetty；Create/Publish/Query PB 随 key batch 增长 | BRPC on，URMA on | 进程级 | BRPC server启动；metadata manager 初始化 | server stop/进程退出 | BRPC并发超限 ELIMIT；metadata PB 过大影响延迟 | 控制面不可达导致 Create/Get/Publish 失败 | BRPC日志、slow log、metadata manager 日志 |
| 数据面连接数 | 源码无集群 endpoint 最大值；按 live worker endpoint 建 DataPlane entry；`max_rpc_session_num=10000` | 每endpoint约 DataPlaneEntry + WorkerRpcClient/transporter；payload RPC 禁用 | `ipc_through_shared_memory=true`、SCMTCP port 31502 | client进程级 + host网络/端口级 | `GetOrCreateEndpoint` lazy create；client register 后建 SHM fd socket | ResetDataPlane/Shutdown/destructor | endpoint/stub过多导致 fd/RSS 增长 | worker不可达、socket/fd 传递失败导致 Get/Set 失败或 fallback | DataPlane日志、`/proc fd`、`ss/lsof`、active_client_count |
| 数据面连接配置(jetty数、深度) | URMA send lane pool 默认200，refill extra 200；`urma_max_write_size_mb=4MiB`；`urma_poll_size=8` | 8MiB value 若走 URMA 至少 2 次 4MiB write；1 transfer 租 1 lane | `enable_urma=true`、whole arena register | 进程级/host UB设备级 | URMA init prefill；transfer `AcquireSendLane` | transfer完成 release；错误 retire/refill | lane耗尽 `K_TRY_AGAIN`；注册/连接失败影响数据面 | UB不可用时依赖 fallback，否则远端读失败 | URMA pool stats、provider counters、logs、perf/网卡计数 |
| 元数据内存 | 源码无独立 metadata heap 上限；master 按 key/location 线性增长 | key=100B/address=15B/1 location：master `699.884B/key`；worker local data heap `776.421B/key`；合计 `1476.305B/key` | `max_client_num=200`；key 最大按100B；不计 hetero/device/ref/subscription | master进程/cluster metadata域；worker进程级 | Create/Publish/CreateMeta 插入 ObjectTable/ObjectMeta | Delete/RemoveMeta/TTL/cleanup/migration 释放 | heap无源码水位，key数过大导致 RSS/OOM | OS OOM或分配失败；metadata操作失败影响Create/Get | object_count、WORKER_OBJECT_COUNT、RSS、metadata日志 |
| 数据内存 | `shared_memory_size_mb=61440`；metadata header `64B`；alignment `64B` | exact 8MiB value real=`10MiB`，损耗`2MiB`；`8MiB-64B` value real=`8MiB` | eviction reserve=6144MiB；rebalance source=70% | worker shared memory pool；host/cgroup约束 | Allocator Init 60GiB；Set/Get/remote落本地时 AllocateMemoryForObject | Delete/evict/Clear/ShmUnit 析构 FreeMemory | 70%约4300 objects触发rebalance候选；eviction前约5529；物理上限约6144 | `K_OUT_OF_MEMORY`；Set/Get/Load失败或超时 | `kv_resource.log/resource.log`、allocator metrics、eviction日志、`/proc/smaps` |
| 异步消息队列匿名内存 | AsyncUpdateLocation 4队列，merge阈值300，无容量上限；AsyncResourceReleaser batch=100，无容量上限 | update-location key=100B/traceID=36B：`255B/pending key`；release：`154B/pending key` | data replication on；RPC payload 禁用不影响该队列 | worker进程级 | remote get成功保存本地副本后 AddTask；失败释放时 AddTask | 后台线程处理成功后销毁；失败/超时重入队 | 无上限，master慢/释放失败会线性积压RSS | OOM；location更新滞后；清理滞后 | pending task日志、RSS、async update/release日志 |
| 客户端/服务端线程占用 | BRPC 64；OC业务64；memcopy16；parallel_for 8~64；GC4；OcDecRef 1~7；AsyncUpdate4；Release1；日志/监控见线程表 | 主路径常驻约 `64+64+16+8+4+1+4+1+日志/监控/URMA`；按配置可到 parallel 64、OcDecRef 7 | 双11 config | 进程级；CPU/cgroup/host共享 | 进程/worker/client初始化；部分 lazy | Shutdown/destructor join/stop | 线程过多导致上下文切换/RSS增加；池满排队 | 线程不足导致排队、超时、ELIMIT或后台滞后 | ThreadPool usage、waiting_task_num、`ps -L`、`/proc/<pid>/task`、resource log |
| 客户端/服务端CPU占用 | 源码没有每IO CPU ms模型；400IOPS/core=2.5ms/IO只能作为外部预算 | 8MiB对象至少涉及内存copy/serialize/metadata处理；CPU需 perf/eBPF 压测 | CPU配额不在1111_config | container/host CPU级 | 请求执行时消耗CPU | 请求完成释放CPU时间片 | CPU饱和表现为 latency/queue 增长 | CPU不足导致超时、吞吐下降、ELIMIT | perf/eBPF/flamegraph、CPU metrics、slow log、latency phase |
| 客户端/服务端日志IO规格 | async queue 65536；2 async worker；`max_log_size=400MB`、`max_log_file_num=20` | 单severity理论滚动上限约 `8000MB`；单条日志大小源码不固定 | sample_rate=0.01；log monitor 5s | 进程级日志；目录若hostPath则host级 | Logging::Start 初始化 spdlog/LogManager/exporter | shutdown/drop；按size/num滚动 | async queue overrun丢日志；日志暴涨占盘/IO | 磁盘慢/满导致观测缺失 | 日志目录du、iostat、kv_resource.log、采样率、collector状态 |
| HCCS带宽（排除项） | 主场景 hetero off、remote H2D off | `0B/s` 业务需求；不计 HCCS pool/buffer | config里 pipeline_h2d=true，但用户场景禁用 remote H2D | host硬件级/集群级 | 主场景不创建/不使用 | 不适用 | 不适用 | 误开才引入 HCCS/设备带宽风险 | HCCS/driver counters，附录关注 |
| UB带宽 | `enable_urma=true`；源码无硬件带宽值；`urma_max_write_size_mb=4MiB` | 8MiB对象若走URMA至少2个write；B/s不能源码推出 | fallback enabled；whole arena register | host UB设备级/网络级 | URMA init/连接/transfer时占用 | transfer完成释放lane | UB带宽饱和导致 latency 升高、fallback/重试 | UB不可用导致连接失败/`K_TRY_AGAIN`或 fallback | URMA/provider counters、perf、网络/UB监控 |
| 内存带宽 | 源码无内存带宽上限；NUMA interleave all | 每次8MiB对象按 copy 路径消耗 8MiB 级读写；次数需按API路径实测 | alignment=64；THP on/off按运行配置 | host NUMA/内存控制器级 | 对象读写/copy/zero-fill/URMA注册时占用 | 操作完成释放带宽 | 带宽饱和无源码错误，表现为延迟升高 | 不足导致吞吐下降/超时 | perf/PCM/eBPF/numastat、latency phase、CPU stall |
| 磁盘IO | RocksDB/Spill/L2 不用；主要磁盘IO为日志 | 数据面不走磁盘；日志见日志IO | `log_dir=/export/Logs/kvcache/logs` | hostPath/容器volume级 | 日志系统启动后写入 | 滚动/清理/进程退出 | 日志暴涨占满磁盘或覆盖旧日志 | 磁盘满/慢导致日志缺失 | du/iostat/K8s volume/log collector |
| UB中断数量 | 源码不定义 IRQ/vector 数量；URMA may poll/event mode 取决于运行 flag/driver | 无源码常量 | `urma_poll_size=8` | host硬件/内核级 | 驱动/设备初始化 | 设备释放/进程退出 | IRQ/轮询压力导致CPU抖动 | 设备不可用影响URMA链路 | `/proc/interrupts`、driver工具、provider counters |
| 文件/socket句柄 | `max_client_num=200`；`max_rpc_session_num=10000`；`arena_per_tenant=1`；SCMTCP port 31502 | client fd cache 上界 `200*10=2000`；每client/endpoint/socket/fd传递增加fd | shared memory IPC on | 进程级fd表/host端口级 | client register、DataPlane endpoint、arena创建、日志打开 | disconnect/reset/LRU/close/进程退出 | fd泄漏/过多导致 EMFILE/连接失败 | fd不足导致注册、socket、日志、shm fd失败 | `/proc/<pid>/fd`、lsof、ss、`ulimit -n` |
| 系统共享内存 | worker pool=61440MiB；arena_per_tenant=1；enable_fallocate=false | exact 8MiB value real=10MiB/object；metadata header=64B/object | SCMTCP 31502；IPC SHM on | worker进程/cgroup/host内存级 | Worker allocator init/mmap；对象分配 | 对象free；进程退出 | 达水位 eviction/rebalance/OOM | `K_OUT_OF_MEMORY`；客户面 Set/Get 失败 | resource.log、`/proc/smaps`、cgroup memory、df/du |

## 4. 线程模型明细

### 4.1 线程数口径汇总

| 进程/口径 | 常驻线程数 | 按需峰值增量 | 不计入正文项 | 说明 |
|---|---:|---:|---|---|
| Worker，双11主场景，排除 Decref/remote H2D/hetero/ZMQ | 171 | +62 | `OcDecRef`、remote H2D、ZMQ、hetero/device、启动 warmup 阶段线程 | 常驻口径包含 BRPC 64、OC 64、memcopy 16、parallel_for core 8、GC 4、AsyncUpdate 4、AsyncRelease 1、URMA 4、日志/指标 4、NodeSelector/AsyncRequest/dev 等轻量线程。 |
| Client，1 个远端 worker 连接，client-direct/routing 模式，含 FlagsMonitor | 15 | +32 | remote H2D、hetero/device、ZMQ、订阅/引用计数、`ENABLE_PERF` 编译可选线程 | 常驻口径包含日志/指标 6、ShmRefReconcile 1、worker 连接 2、URMA 4、transport/routing 2；若 FlagsMonitor 未启动则 14。 |
| Client，local-cache-only，1 个远端 worker 连接，含 FlagsMonitor | 13 | +32 | 同上 | 相比 client-direct/routing 少 `transport-recon` 和 hash-ring refresher 两个线程。 |
| Client，每增加一个 standby/active worker 连接 | +2 | +2 | - | 常驻增加 `RecvPageFd + ListenWorker`；按需增加 `urma_handshake + urma_handshake_retry`。 |

> 线程数说明：`ThreadPool(core=0,max=N)` 按源码不会在初始化时创建常驻线程，只有提交任务后最多扩到 `N`；表中“按需峰值增量”只按当前 set/get 相关池统计，不把 remote H2D/device/ZMQ 纳入正文。

### 4.2 Worker 侧线程明细

| 线程/线程池 | 数量（双11） | 职责 | 业务影响 | 源码证据 |
|---|---:|---|---|---|
| BRPC server worker | 64 | BRPC bthread worker pool，承载所有 BRPC service adapter 的 handler 分发 | 池满或 in-flight 超限时排队/ELIMIT；影响所有控制面和元数据 RPC | `common/rpc/rpc_server.cpp:117,123`；`common/flags/common_flag_define.cpp:180-185` |
| BRPC max in-flight | 256 | 不是线程数，是每个 brpc server 的并发闸门 | 超过立即 ELIMIT，保护 bthread/OOM | `common/rpc/rpc_server.cpp:118-123` |
| ZMQ service pool | 0（主场景） | BRPC mode 下不启动 ZMQ listener/service thread pool | 不能按 ZMQ `rpc_thread_num*service` 估线程；`rpc_thread_num=64`主要保留兼容/非BRPC路径 | `worker/worker_oc_server.cpp:575-584,1460-1463` |
| OcGetThread | 64 | object cache 业务后台执行池 | Get/Set/metadata recovery 等内部任务排队会直接影响延迟 | `worker/object_cache/worker_oc_service_impl.cpp:441` |
| memCpyThreadPool | 16 | 大块内存拷贝辅助池 | 8MiB value 下 copy 并发/内存带宽压力主要来源之一 | `worker/object_cache/worker_oc_service_impl.h:99`；`.cpp:442` |
| parallel_for | core 8，max 64 | 本地并行计算/批处理辅助 | 可扩到 oc_thread_num 上限；会和业务线程争 CPU | `worker/object_cache/worker_oc_service_impl.h:100`；`.cpp:443`；`parallel_for_local.cpp:33-39` |
| OcCleanClient GC | 4 | 客户端清理/GC | 滞后会导致对象/锁/fd等清理延迟 | `worker/object_cache/worker_oc_service_impl.cpp:444-445` |
| devThread/AsyncRpcManager | 主场景非 hetero：1 + 1 | BUILD_HETERO 关闭时仍创建轻量 dev 管理线程池/async rpc manager | 主场景不承载 hetero 数据，但常驻线程/RSS仍存在 | `worker/object_cache/worker_oc_service_impl.cpp:447-458` |
| OcDecRef | min 1，max 7 | 与 shm lockId/client 清理相关的 decrement/ref 清理池 | `max_client_num` 越大上限越高；双11 `200/32+1=7` | `worker/object_cache/worker_oc_service_impl.cpp:459-462` |
| AsyncUpdateLocation | 4 | 热副本落本地后异步向 master 更新 location | master 慢时无界积压，location 滞后，RSS线性涨 | `async_update_location_manager.h:95-102`；`.cpp:32-34,89-103` |
| AsyncResourceReleaser | 1 | 失败/迁移等场景异步释放本地资源 | release 慢时无界积压，内存回收滞后 | `async_resource_releaser.h:91-101`；`.cpp:95-135` |
| URMA RetireJfs | max 1（lazy 0~1） | 异步 retire/delete jetty | UB异常/retire慢会占后台线程并影响 lane 回收 | `common/rdma/urma_resource.cpp:733-734` |
| URMA refill thread | 1 | send lane pool 后台补齐 | refill失败或慢会导致 lane pool 长期不足 | `common/rdma/urma_resource.cpp:747-750,1330-1372` |
| URMA warmup | 启动阶段 4 + controller 1 | worker-worker connection warmup | 阶段性线程，不纳入长期稳态；启动期会抢 CPU/网络 | `worker/worker_oc_server.cpp:1767-1773` |
| MasterRpcWarmup | 启动阶段 4 | master RPC warmup | 阶段性线程，不纳入长期稳态；启动期影响可用时间 | `worker/worker_oc_server.cpp:1905-1923` |
| spdlog async worker | 2 | 异步日志落盘 | queue满时丢日志/阻塞取决于 spdlog 策略；影响可观测性和磁盘IO | `common/log/logging.cpp:69,287-288` |
| LogBackgroundTask | 1 | 日志滚动、压缩、flush/monitor 周期任务 | 日志滚动滞后会占盘或影响排障 | `common/log/logging.cpp:314-315`；`common/log/log_manager.cpp:70-79` |
| ResMetricCollector | 1 | 资源指标采集入口 | 指标缺失会影响容量观测；collector itself 很轻 | `common/metrics/res_metric_collector.cpp:135-150` |
| MetricsFlush | 每 exporter 1（resource/json 至少2） | harddisk/json exporter 刷盘 | 磁盘慢时可观测性滞后 | `common/metrics/metrics_exporter.cpp:48-65` |

一句话：双11主场景长期可确认的 worker 线程资源不是单一 `64`，而是 BRPC 64 之外还有 OC/拷贝/并行/GC/异步/URMA/日志监控等后台线程；CPU不足时先表现为队列和 latency 上升，BRPC 并发闸门会进一步以 ELIMIT 形式拒绝过载请求。

### 4.3 Client 侧线程明细

| 线程/线程池 | 数量（双11） | 职责 | 业务影响 | 源码证据 |
|---|---:|---|---|---|
| spdlog async worker | 2 | client 异步日志落盘 | 日志量大时消耗磁盘 IO；queue 满会影响可观测性 | `common/log/logging.cpp:60-71,284-315`；`common/log/spdlog/logger_context.cpp:85-90` |
| LogBackgroundTask | 1 | 日志后台周期任务 | flush/滚动滞后会导致日志观测延迟或磁盘压力 | `common/log/log_manager.cpp:70-79` |
| FlagsMonitor | 0/1 | client 配置文件监听 | 只有 monitor config path 存在时启动；否则不占线程 | `client/object_cache/object_client_impl.cpp:935-942`；`client/client_flags_monitor.cpp:67-85` |
| MetricsFlush | 1 | `json_log_monitor=true` 时 JSON 指标 exporter 刷盘 | 磁盘慢时指标落盘滞后 | `client/object_cache/object_client.cpp:54-58`；`client/kv_cache/kv_client.cpp:78-82,96-99`；`common/metrics/metrics.cpp:98-108`；`metrics_exporter.cpp:42-65` |
| Client metrics thread | 1 | client 侧周期采集 metrics | 观测线程，不处理业务请求；`log_monitor=false` 时不启动 | `client/object_cache/object_client_impl.cpp:6929-6957` |
| ShmRefReconcile | 1 | client 侧共享内存引用/资源 reconcile | 清理滞后时影响本地资源释放，不直接增加 set/get 并发 | `client/object_cache/object_client_impl.cpp:6959-6967` |
| RecvPageFd | 1 / worker 连接 | 接收 worker 侧 page fd/共享内存 fd | 每个 `ClientWorkerRemoteCommonApi` 常驻 1 个；worker 数越多线程线性增加 | `client/client_worker_common_api.cpp:336-349`；`client/object_cache/object_client_impl.cpp:716-735` |
| ListenWorker | 1 / worker 连接 | RPC heartbeat 方式监听 worker 存活 | heartbeat 开启时每连接 1 个；worker 切换/standby 会增加 | `client/object_cache/object_client_impl.cpp:719,841-867`；`client/listen_worker.cpp:62-109` |
| UrmaPollJfc | 1 | URMA 完成队列 poll | UB 完成处理滞后会影响远端读写完成时延 | `common/rdma/urma_manager.cpp:268-284` |
| URMA async event | 1 | URMA 异步事件处理 | UB 异常/事件处理滞后影响链路恢复 | `common/rdma/urma_async_event_handler.cpp:46-49` |
| URMA perf thread | 1 | URMA 内部 perf/统计线程 | 观测线程；不直接处理业务 | `common/rdma/urma_manager.cpp:272-283` |
| URMA send lane refill | 1 | URMA send lane pool 后台补齐 | refill 慢会导致 lane 不足，出现 `K_TRY_AGAIN`/重试 | `common/rdma/urma_resource.cpp:733-750,1299-1372` |
| transport-recon | 0/1 | client-direct/routing 模式下 transport 重连 | local-cache-only 且未启用 direct pipeline H2D 时不启动 | `client/object_cache/object_client_impl.cpp:806-833`；`transport/transport_layer.cpp:242-258` |
| hash-ring refresher | 0/1 | client-direct/routing 模式下周期刷新路由 | 路由刷新滞后会影响 worker 选择/切换 | `client/routing/routing.cpp:69-81`；`client/routing/hash_ring_refresher.cpp:53-60` |
| Client perf thread | 0/1 | client `ENABLE_PERF` 编译开关下的 perf 周期线程 | 编译期开关，当前源码不能从配置判定是否存在 | `client/object_cache/object_client_impl.cpp:6910-6927` |

| Client 按需线程池 | 最大线程数 | 当前场景口径 | 源码证据 |
|---|---:|---|---|
| async_set | 8 | set 异步路径可能使用；非稳态常驻 | `client/object_cache/object_client_impl.cpp:594-604`；`common/util/thread_pool.cpp:223-249` |
| async_get_copy | 8 | get 拷贝异步路径可能使用；非稳态常驻 | 同上 |
| async_get_rpc | 8 | get RPC 异步路径可能使用；非稳态常驻 | 同上 |
| async_release_buffer | 4 | buffer 异步释放可能使用；非稳态常驻 | 同上 |
| switch | 1 | worker 切换/失败场景使用 | 同上 |
| urma_handshake | 1 / worker 连接 | URMA 建连阶段使用 | `client/client_worker_common_api.cpp:336-349,1151-1173` |
| urma_handshake_retry | 1 / worker 连接 | URMA 建连重试阶段使用 | `client/client_worker_common_api.cpp:336-349,1222-1242` |
| RetireJfs | 1 | URMA 异常/retire JFS 时按需使用 | `common/rdma/urma_resource.cpp:733-734` |
| async_pipeline_rh2d | 8 | 当前场景排除：remote H2D 不用 | `client/object_cache/object_client_impl.cpp:594-604` |
| asyncDevDeletePool | 8 | 当前场景排除：hetero/device 不用 | `client/object_cache/object_client_impl.cpp:594-604` |

一句话：Client 侧单连接、client-direct/routing 模式稳态常驻约 `15` 个线程；当前 set/get 相关 lazy pool 在任务打满时最多再扩 `32` 个线程，remote H2D/device/ZMQ/订阅引用路径不进入正文容量口径。

## 5. Per-key 与 per-value 推导

| 分类 | 推导 | 一句话总结 |
|---|---|---|
| worker 本地 data object 匿名内存 | xqyun RSS probe，key=100B，含 ObjectTable/SafeObject/ObjCacheShmUnit/std::string 等，不含 value shared memory：`776.421B/key` | 本地 worker 每 key 固定 heap 约 `776B`，与 8MiB value 相比很小，但百万级 key 会进入 GB 级 RSS。 |
| master metadata 匿名内存 | xqyun RSS probe，key=100B，address=15B，1 location，含 ObjectMetaPb/value/locations/state/timestamp：`699.884B/key` | master 每 key/1 location 约 `700B`，location 数增加会继续线性增长。 |
| 数据 shared memory | 源码 `needSize=dataSize+metadataSize`；metadataSize 双11 `64B`；jemalloc probe：exact 8MiB+64B -> 10MiB | exact 8MiB value 每对象实占 `10MiB`，损耗 `2MiB`；但 `8MiB-64B+64B` 不触发 cliff，只占 `8MiB`。 |
| 异步 update-location | 4队列、merge阈值300、无容量上限；RSS probe key=100B/traceID=36B：`255B/pending key` | 积压按 `255B/key` 线性涨，风险不是单 key 大小，而是无界 pending 数。 |
| 异步 release | batch=100、无容量上限；RSS probe key=100B：`154B/pending key` | 释放失败/重试积压按 `154B/key` 线性涨，并拖慢 shared memory 回收。 |

## 6. 源码校验证据矩阵

| 结论 | 源码/证据 | 是否足以支撑 |
|---|---|---|
| CodeGraph 不可用，本轮不重建 | `codegraph status /home/t14s/workspace/git-repos/datasystem` -> `Not initialized` | 支撑：只能报告 CodeGraph 状态，不能用节点/边做结论。 |
| shared memory pool = 61440MiB | `worker/worker_oc_server.cpp:1466-1472` 将 `shared_memory_size_mb` 转 bytes 后传入 Allocator | 支撑。 |
| metadata header = 64B | `worker/object_cache/worker_oc_service_impl.cpp:2248-2260`：`max_client_num/8+1+sizeof(uint32_t)+sizeof(char)` 后按 `memory_alignment` 对齐；200 得 31B 对齐 64B | 支撑。 |
| 对象分配按 data+metadata | `worker/object_cache/obj_cache_shm_unit.cpp:212-229`：`needSize=dataSize+metadataSize` 后 `AllocateMemory` | 支撑。 |
| jemalloc alignment/real size | `common/shared_memory/jemalloc.cpp:121-136` 用 `mallocx` + `MALLOCX_TCACHE_NONE` + `MALLOCX_ALIGN`; `allocator.cpp:229-236` 用 `sallocx` | 支撑，size-class 数字由 xqyun probe 给出。 |
| master metadata 结构 | `master/object_cache/oc_metadata_manager.h:96-103`：`ObjectMetaPb meta`、`value`、`locations`、`multiSetState/timestamp` | 支撑。 |
| object_key 不在 meta value 冗余存储 | `oc_metadata_manager.cpp:3477-3489` 创建 meta 后清 `object_key`，key 作为 table key | 支撑。 |
| worker object table 结构 | `common/immutable_string/immutable_string.h:27-29`：`ImmutableString=std::string`；`worker/object_cache/object_kv.h:42-43`、`safe_table.h:47-52`：`SafeTable<ImmutableString,ObjectInterface>` 基于 TBB concurrent_hash_map | 支撑。 |
| DeviceMetaInfoPb 属 hetero/device | `worker_object.proto:353-369` 有 `device_info`，但用户场景 hetero off，正文不计 | 支撑排除口径。 |
| AsyncUpdateLocation 无上限 | `async_update_location_manager.h:95-102` 4队列；`.cpp:32-34` 起4线程；`.cpp:98-103` merge或emplace无capacity判断 | 支撑。 |
| AsyncResourceReleaser 无上限 | `async_resource_releaser.h:91-101` queue + batch 100；`.cpp:115-135` 每轮取100，失败重入队，无capacity判断 | 支撑。 |
| BRPC线程/并发 | `common/rpc/rpc_server.cpp:117-123` 设置 `num_threads/max_concurrency`；`common_flag_define.cpp:180-185` 说明 ELIMIT | 支撑。 |
| BRPC mode 不启动 ZMQ | `rpc_server.cpp:233-243`；`worker_oc_server.cpp:1460-1463` exclusive mode | 支撑。 |
| URMA lane pool | `common_flag_define.cpp:61-68` 默认 poll=8/write=4MiB/pool=200/refill extra=200；`urma_resource.cpp:1299-1327` prefill；`:1361-1372` live limit | 支撑。 |
| Client 初始化日志/配置/指标线程 | `object_client_impl.cpp:935-942` 启动 Logging/FlagsMonitor；`client_flags_monitor.cpp:67-85` 仅 config path 存在时起线程；`object_client_impl.cpp:6929-6967` 启动 metrics/ShmRefReconcile | 支撑。 |
| Client lazy 线程池 | `object_client_impl.cpp:594-604` 创建 `async_set/get/copy/release/switch` 等 `ThreadPool(0,N)`；`thread_pool.cpp:223-249` 表明 core=0 初始化不创建常驻线程、按任务扩到 max | 支撑。 |
| Client 每 worker 连接线程 | `client_worker_common_api.cpp:336-349` 每连接起 `RecvPageFd`，并创建 URMA handshake/retry lazy pool；`listen_worker.cpp:62-109` RPC heartbeat 起 `ListenWorker` | 支撑。 |
| Client URMA 常驻线程 | `urma_manager.cpp:268-284` 创建 `UrmaPollJfc`、async event、perf；`urma_async_event_handler.cpp:46-49`；`urma_resource.cpp:747-750` refill thread | 支撑。 |
| Client routing/direct 线程 | `object_client_impl.cpp:806-833` client runtime 按 local cache/direct H2D 条件初始化 transport/routing；`transport_layer.cpp:242-258` transport reconnect；`routing.cpp:69-81` + `hash_ring_refresher.cpp:53-60` hash ring refresh | 支撑条件化结论。 |
| fd上限线索 | `worker_service_impl.cpp:67-69` max_client_num；`:97` fd cache = `max_client_num*10`；`:117-124` SCMTCP bind | 支撑。 |
| eviction/rebalance水位 | `common_flag_define.cpp:92-99,131-134`；`allocator.cpp:268-276`；`memory_rebalance_scheduler.cpp:430-437` | 支撑公式。 |
| 日志线程/queue | `logging.cpp:69,108,287-288,314-315`；`log_manager.cpp:70-79`；`metrics_exporter.cpp:48-65` | 支撑线程和queue配置。 |

## 7. 排除项附录

- hetero-client/device/remote H2D/HCCS：用户确认不使用；正文只保留 HCCS=`0B/s`、DeviceMetaInfoPb 不计入 per-key。
- RocksDB/L2Cache/Spill：`rocksdb_write_mode=none`，用户确认 l2cache/spill 不用；磁盘 IO 只按日志估计。
- ZMQ：`use_brpc=true`，BRPC exclusive mode，不计算 ZMQ socket/thread/hwm。
- 引用计数/订阅：用户确认不使用，不进入 per-key 固定内存。
- RPC payload：客户侧禁用对象数据 payload，不进入 RPC 临时匿名内存；metadata/control PB 另按控制面风险观察。
