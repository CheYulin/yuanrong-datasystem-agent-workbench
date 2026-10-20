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

r3 主矩阵使用新的冻结配置：仅将 `MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER` 和 `MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER` 设为 8192，所有 B 与重复保持一致；lanes、timeout、CPU、内存、原生制品与 API 实现保持原实验边界。它是容量适配后的独立配置，不能当作默认参数性能。完整门禁和 C8 smoke 仍须分别通过后才可进入正式矩阵。

独立 default / queue8192 机制诊断的成功率差异只说明这两个已执行配置的观测结果。尚未捕获明确 queue-full / rejection reason counter，因此报告不能宣称已确认 `QUEUE_FULL` 根因，也不能把诊断耗时或吞吐增加为第四轮正式性能结果。历史执行的 helper 原件与当前报告工具分别归档，原始 JSON、错误和日志保留于 raw evidence。
