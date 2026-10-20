# Mooncake v0.3.13.post1 原生批量接口审查

固定源码 `719735896c86b56fabec6cf3e825fb2ea640597a`。五个被审文件的目标工作区 SHA256 与 `git show HEAD` 副本一致，工作区无修改。以下是源码边界；真实服务正确性、TCP 与压力结果由独立门禁和正式矩阵证明。

| 适配器操作 | 一次原生 Python 调用 | 返回与完成边界 |
|---|---|---|
| 首次 PUT | `put_batch(keys, values, ReplicateConfig)` | 整体 `int` 状态；成功为 0 |
| UPSERT | `upsert_batch(keys, values, ReplicateConfig)` | 整体 `int` 状态；插入或真正更新 |
| GET | `get_batch(keys)` | 原顺序 `list[bytes]`；返回时已物化 Python 自有 bytes |
| EXIST | `batch_is_exist(keys)` | 每 key 整数：1 命中、0 未命中，其余值为错误 |

绑定位置：[PUT](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-integration/store/store_py.cpp#L2964-L2988)、[UPSERT](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-integration/store/store_py.cpp#L2758-L2788)、[GET](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-integration/store/store_py.cpp#L559-L609)、[EXIST](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-integration/store/store_py.cpp#L2385-L2395)。Python 适配器不通过单 key API 循环实现 batch；原生内部可发出多个元数据 RPC 和对象传输。

`put_batch` 对已有 key **返回成功但不重写**。Master 的现存对象分支返回 `OBJECT_ALREADY_EXISTS`，客户端将它转为成功。因此首次 PUT 使用新 key；复用 key 的更新吞吐只放入独立 UPSERT 图。[Master 分支](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master_service.cpp#L4423-L4432) · [客户端兼容语义](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/client_service.cpp#L3063-L3082)。

PUT / UPSERT 在 staging 分配与拷贝后，各调用一次 `Client::BatchPut` / `BatchUpsert`。两条路径等待 transfer future，再完成 Master finalize 后返回；不是仅提交异步请求。[PUT 同步路径](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/client_service.cpp#L3239-L3274) · [UPSERT 同步路径](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/client_service.cpp#L2146-L2179) · [future 等待](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/client_service.cpp#L2577-L2600)。

批次不是全有或全无事务。bytes PUT / UPSERT 会把每 key 状态折叠成首个错误码；失败时已完成多少 key 可能未知。GET 对缺失或读取失败均可返回空 bytes，无法从包装层区分。适配器在非零写状态、空 GET、结果长度不符或非 0/1 EXIST 状态时抛错，不重试、不追加 EXIST 补查、不把错误算未命中。[写状态折叠](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/real_client.cpp#L2015-L2084) · [GET 顺序与部分结果](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/real_client.cpp#L3147-L3282) · [EXIST 实际错误码](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/real_client.cpp#L2303-L2318)。

审查路径未发现固定 key 数上限，不能据此宣称请求无限。批内对象同时占用注册 staging，RPC 和服务端资源仍会限制请求。本实验 B=4096、每 key 1024 bytes，payload 为 4 MiB；默认客户端 staging 为 128 MiB。真实上限与成功率仍由门禁观察。

新 `batch_mooncake.py` 固定单内存副本、零客户端存储容量、TCP；要求 `MC_STORE_MEMCPY=0`、`MC_STORE_LOCAL_HOT_CACHE_SIZE=0`。`write_mode=insert/upsert` 显式选择对应原生 API。输入去重、bytes 长度检查与返回向量检查计入端到端 API 延迟，metadata 保留这一边界。17 项本地 fake-native fixture 证明四种 B 各调用一次批量入口、错误不触发单 key 回退及部分成功保持未知；这些 fixture 不代表真实产品性能或运行成功。

## 默认配置与批量容量配置的边界

锁定源码的 TCP 默认值为每 peer 4 lanes、1024 queued transfers、1024 pending admissions、1000 ms admission timeout；queue 和 pending 可通过原生环境变量修改，上限均为 65535。[默认值与环境变量解析](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-transfer-engine/src/transport/tcp_transport/tcp_transport.cpp#L301-L338)。直接队列已满时会尝试 pending admission，pending 也满时存在拒绝路径；这是可能导致批量传输失败的源码机制。[接纳路径](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-transfer-engine/src/transport/tcp_transport/tcp_transport_lane_impl.h#L581-L595)。

默认 r2 的带跟踪 B4096 门禁出现过 GET 空结果和 `TRANSFER_FAIL`；之后独立无跟踪 C1 诊断通过，仍未证明 C8 稳定性。默认 C8 smoke 随后又在 B4096 UPSERT 预热出现 `-800 / TRANSFER_FAIL`。这些失败、诊断和命令原始边界单独保留，不能删除失败后把独立诊断写成原门禁成功，也不能归入正式性能重复。

从 r3 引入的容量配置将 `MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER` 和 `MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER` 设为 8192。r5 沿用它，并显式固定 lanes=4、admission timeout=1000ms 为锁定源码默认数值，所有 B 与重复一致；CPU、内存、原生制品与 API 实现保持同一边界。它是容量适配配置，不能当作默认参数性能。r3失败与r4旧协议预检均不属于最终矩阵；r5独立门禁和C8 smoke通过后才进入当轮正式测量，后续失败原件仍单独保留。当前 R7 的已执行资格状态见末节。

独立 default / queue8192 机制诊断的成功率差异只说明这两个已执行配置的观测结果。尚未捕获明确 queue-full / rejection reason counter，因此报告不能宣称已确认 `QUEUE_FULL` 根因，也不能把诊断耗时或吞吐增加为第四轮正式性能结果。历史执行的 helper 原件与当前报告工具分别归档，原始 JSON、错误和日志保留于 raw evidence。

## 纯 KV 内存 TCP 配置审查

以下表格记录 R5 结束后、R6/R7 执行前的历史配置审查：用户要求关闭非必要能力后，按同一固定 SHA 提出独立 `pure-kv` profile 并实现启动脚本；当时尚未执行该 profile，不能据源码审查推断最优吞吐。R5 冻结输入没有被追改。审查从官方固定提交取得源码，逐文件以 Git tree 的 blob SHA1 校验，另保留 SHA256 清单；该审查没有访问目标节点。随后 R7 的实际配置与资格执行证据单列于末节，不能把历史建议状态当作当前执行状态。

| 项目 | R5 实际配置 / 默认行为 | `pure-kv` 显式配置与边界 |
|---|---|---|
| 内存副本与数据路径 | 两个 peer 各 4 GiB；client store=0；TCP；单内存副本 | 保持容量、C8、8192/8192/4/1000、完整 bytes 校验；MEMCPY=0、hotcache=0，不引入本地旁路 |
| client metrics | `MC_STORE_CLIENT_METRIC` 未设，原生默认启用；周期 interval 默认0，仅不报告 | 所有 peer/client 设 `MC_STORE_CLIENT_METRIC=0`、`MC_STORE_CLIENT_METRIC_INTERVAL=0`；工厂返回空指针，使受条件保护的客户端指标记录跳过 |
| TE metrics 与 trace | 未设置 TE metrics；`MC_LOG_LEVEL=ERROR`，原生 trace=false | 显式 `MC_TE_METRIC=0`；保留 ERROR 日志与外部 cgroup/native 监控。此变量控制 TE 周期线程，不声称移除所有编译内计数 |
| Master 周期报告 | `enable_metric_reporting` 原生默认 true，错误日志等级不会阻止其统计工作 | `--enable_metric_reporting=false`；关闭报告线程。Master admin HTTP listener 仍存在，不能把 port=0 误作禁用 |
| SSD / DFS / HA / oplog | 已关闭 HA/offload/oplog；setup SSD offload默认false；`root_fs_dir` 默认空 | 显式保留这些false，`--root_fs_dir=`、`--enable_disk_eviction=false`、`--offload_on_evict=false`、`--offload_force_evict=false`、`--promotion_on_hit=false` |
| 快照 / 事件 / quota | snapshot/restore、ZMQ KV event publisher、multi-tenant均原生默认false | 显式 `--enable_snapshot=false --enable_snapshot_restore=false --enable_kv_events=false --enable_multi_tenants=false`，避免环境或后续默认值歧义 |
| 内存 eviction | ratio=0.05、高水位0.90；后台线程常驻 | `--eviction_ratio=0 --eviction_high_watermark_ratio=1` 避免本受控容量下的内存驱逐；原生无全停线程开关，轮询及必要未完成写清理仍存在，满容量错误不能隐藏 |
| 必要控制面与校验 | Master 内存元数据、TCP RPC、peer心跳/lease、测前后内容校验 | 保留。这里没有启用 DataSystem stream cache 的对应路径；不能把必要 TCP 字节流称为可关闭的可选 streaming 功能 |

证据：[client metrics 工厂](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/client_metric.cpp#L15-L20)与[关闭路径](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/client_metric.cpp#L89-L107)、[批量指标条件](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/client_service.cpp#L1809-L1817)、[TE metric 周期线程](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-transfer-engine/src/transfer_engine_impl.cpp#L1011-L1049)、[trace等级解析](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-transfer-engine/src/config.cpp#L393-L407)。

[Master 原生开关](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master.cpp#L123-L148)、[磁盘/租户/快照默认](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master.cpp#L300-L369)；[setup 默认关闭 SSD 与 client HTTP](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-integration/store/store_py.cpp#L2202-L2236)；空 DFS 路径使[客户端不初始化持久化 backend](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/client_service.cpp#L1073-L1096)。

限制必须与参数同时披露：[Master 无条件启动 admin server](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master.cpp#L1647-L1653)，[HTTP异步启动与周期报告分别控制](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master_admin_service.cpp#L250-L278)；[eviction线程的启动](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master_service.cpp#L476-L477)和[条件/清理循环](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master_service.cpp#L9053-L9121)无法通过上述参数整体删除。必要 Master 指标计数仍可能存在。关闭指标或持久化可能降低开销，但收益必须由独立新 profile 测量，不能从源码推导性能提升或断言已解决其它产品错误。

`mooncake/launch_master.sh` 仅在 `KVC_FEATURE_PROFILE=pure-kv` 时附加以上 Master 参数；默认基线 argv 不变。启动前将实际完整 argv 原子写入当前服务期 `RUNTIME_DIR/master-argv.json`；记录失败会使启动失败，不隐藏 helper 错误。三个本地 fake-master 验证覆盖默认 argv、pure-kv argv与保存原件一致，以及 helper返回23时不启动Master；它们不代表真实服务资格门禁。

端点值已另行核对：`eviction_ratio=0` 被[gflags validator](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master.cpp#L172-L178)允许；`eviction_ratio=0` 与 `eviction_high_watermark_ratio=1` 也都通过[MasterService 构造的闭区间校验](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/src/master_service.cpp#L295-L305)。[builder setter](https://github.com/kvcache-ai/Mooncake/blob/719735896c86b56fabec6cf3e825fb2ea640597a/mooncake-store/include/master_config.h#L877-L886)没有附加更严格下限，无需用 epsilon 伪装关闭。控制面另以 `MC_RPC_PROTOCOL=tcp` 显式固定，避免继承环境选择 RDMA；必要 TCP RPC 保留。R6 pure-kv 预检的 Mooncake 启动失败来自 stage overlay 丢失 launcher 可执行位，尚未执行 Moon API，不归因于原生参数或产品缺陷；由新 stage 修正权限后重新验证，旧失败原件保留。

## R7 已执行的 pure-kv 资格状态

本节只核对已采集到本地的 R7 stage、门禁与 smoke，证据截止 smoke 结束时间 `2026-10-20T06:00:05.021960+00:00`。不填写仍在进行的正式矩阵终态，也不把资格测试当作三轮性能结果。

| 已执行阶段 | 实际状态 | 原始证据 |
|---|---|---|
| R7 冻结配置 | `feature_profile=pure-kv`；三产品环境均显式声明该 profile。Mooncake 仍使用同一固定源码构建物，未在 R7 重新编译；queue/pending=8192/8192、lanes=4、admission timeout=1000ms | `provenance/batch-stage.json`；`measured-runtime/products.json` |
| 三产品批量门禁 | `valid=true`，三产品均完成、failed为空；全阶段实际时长137.246847605秒。Mooncake 两客户端的 insert/upsert 均完成四档 B 的正确性检查 | `gate/summary.json`；`gate/r0-mooncake/client-{0,1}-{insert,upsert}.json` |
| 三产品 C8 smoke | `valid=true`，三产品均完成、failed为空；全阶段实际时长437.495450633秒。它是短资格窗口，不证明20秒正式窗口或全部运行期稳定性 | `smoke/summary.json`；`smoke/r0-mooncake/` 原始结果 |
| 精简启动参数 | 已采集 gate 与五个 smoke 服务期的6份 `master-argv.json`，均记录上述 Master 关闭参数；冻结产品环境同时记录 client/TE metrics=0、MEMCPY/hotcache=0、RPC协议TCP | raw `run/gate-r0-mooncake/` 与 `run/smoke-r0-mooncake-*/`；`measured-runtime/mooncake/launch_master.sh` |

这些是实际启动意图、接口门禁与短 smoke 证据，不是所有后台成本消失的证明。Master admin listener、必要清理循环与控制面仍按固定源码存在；没有进行开关逐项消融或穷举调优，不能宣称全局最优或把容量参数与精简配置的收益混为一谈。未来 `builds-only-native-root` 重放入口与本次 R7 的原构建复用路径分别留证，工具 fixture 不能冒充从零构建至正式报告的已执行整链。
