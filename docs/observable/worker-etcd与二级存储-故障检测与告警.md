# Worker 访问 etcd（gRPC）与二级存储（文件/OBS）故障：检测、日志与告警

本文依据当前 **`yuanrong-datasystem`** 源码梳理：**故障如何被检测**、**细分类型**、**典型日志与返回消息**、**指标埋点**；**告警当前代码侧未见统一 Prometheus/告警规则**，表中「建议告警」为待补充项。

**配套（Worker 本进程：续约感知、健康检查、线程池/客户端/缓存命中）**：[worker-续约-健康检查-资源-故障检测与告警.md](./worker-续约-健康检查-资源-故障检测与告警.md)

**关键代码位置（便于核对）**：

- etcd gRPC 封装：`src/datasystem/common/kvstore/etcd/grpc_session.h`（`SendRpc` / `AsyncSendRpc`）
- etcd 租约与续租：`src/datasystem/common/kvstore/etcd/etcd_keep_alive.cpp`、`etcd_store.cpp`（`RunKeepAliveTask` / `AutoCreate` / `LaunchKeepAliveThreads`）
- Worker 启动前 etcd 探活：`src/datasystem/common/kvstore/etcd/etcd_health.cpp`、`worker_oc_server.cpp`（`ConstructClusterInfo`）
- 网络分区时“etcd 是否仍可用”的间接探测：`etcd_cluster_manager.cpp`（`CheckEtcdStateWhenNetworkFailed`）、`worker_worker_oc_service_impl.cpp`（`CheckEtcdState`）
- 二级存储：`src/datasystem/common/l2cache/persistence_api.cpp`、`sfs_client/sfs_client.{h,cpp}`；OBS 路径 `obs_client/obs_client.cpp`

---

## 一、etcd（gRPC）侧

### 1.1 当前是否存在「etcd 健康」检测？

| 检测方式 | 何时触发 | 实现要点 | 典型日志/返回 |
|----------|----------|----------|----------------|
| **Maintenance.Status 探活** | Worker 构造集群信息 **`ConstructClusterInfo`** 时 | `CheckEtcdHealth(FLAGS_etcd_address)` 新建 `Maintenance` stub，10s deadline 调 `Status` | 失败：`K_RUNTIME_ERROR`，消息 **`Connect to etcd failed, as: <grpc error_message>`**（`etcd_health.cpp`） |
| **可写探测 Writable** | 其他 Worker 响应 **`CheckEtcdState`**、以及 Watch 初始化时作为 `checkEtcdStateHandler` | `EtcdStore::Writable()` 对 **`ETCD_HEALTH_CHECK_TABLE`** 做一次 **Put**（短超时 `MIN_RPC_TIMEOUT_MS`） | 成功则 `CheckEtcdStateRspPb.available=true`；失败时 Put 的错误同 **`SendRpc` 统一包装**（见下表） |
| **续租与 Watch 运行态** | 进程常驻 | `EtcdKeepAlive::Run` / `SendKeepAliveMessage`；`EtcdStore::LaunchKeepAliveThreads` 重试；`WatchRun` 重连 | 见 **1.2 / 1.3** |
| **本机断 etcd 但集群仍可用（间接）** | KeepAlive 连续失败后 | `EtcdClusterManager::CheckEtcdStateWhenNetworkFailed` 向**其他活跃 Worker** 发 `CheckEtcdState`，若任一回 **`available==true`** 则认为 etcd 可用、本机更像网络故障 | 日志：`The nodes to be queried are: ...`；任一对端确认：`... confirms that etcd is OK`；RPC 写失败：`Rpc write failed, with rc: ...` |

**结论**：有 **启动时直连 etcd 的 Status 探活**，运行期还有 **Writable 探针（Put 健康表）**、**续租流**、**Watch 重连** 与 **跨 Worker 间接确认**；**没有单独的周期性告警模块**，需依赖日志/指标平台。

---

### 1.2 gRPC 调用失败（统一包装 + 细分）

**统一行为**：同步 RPC `SendRpc` 在 `!status.ok()` 时返回 **`K_RPC_UNAVAILABLE(1002)`**，消息形如：

`[<methodName>] Send rpc failed: (<grpc_error_code_int>) <grpc error_message>`

异步 `AsyncSendRpc`（如 **LeaseGrant**）为：

`[<addresses>,LeaseGrant] Send rpc failed:<grpc error_message>`

**如何从消息粗分「TCP/连接」**：gRPC 码常为 **14 UNAVAILABLE**、**4 DEADLINE_EXCEEDED**（超时也会走同一包装，见 1.3）、TLS 失败等，以 **`(<int>)`** 与 **`error_message`** 为准。

| 故障检测（如何发现） | 细分故障类型 | 典型 Status / 日志 | 指标/访问记录（若有） | 告警（当前→建议） |
|----------------------|--------------|----------------------|------------------------|-------------------|
| **`SendRpc` 返回错误** | **TCP/连接/对端不可用**（含 TLS、拒绝连接等） | `K_RPC_UNAVAILABLE`：`[Put::etcd_kv_Put] Send rpc failed: (14) ...` 等 | `AccessRecorderKey::DS_ETCD_PUT` 等（见 `GetEtcdReqRecorderKey`） | **无** → 建议：`DS_ETCD_*` 失败率 + gRPC 码 TopN |
| **`LeaseGrant` 失败** | **建连后 Lease 接口失败** | `GetLeaseID`：`LeaseGrant error: ...`（`RETURN_IF_NOT_OK_PRINT_ERROR_MSG`）+ 底层 `AsyncSendRpc` 消息 | `DS_ETCD_LEASE_GRANT` | **无** → 建议：LeaseGrant 连续失败告警 |
| **`EtcdKeepAlive::Init` 失败** | **LeaseKeepAlive 双向流创建失败**（偏连接/协议） | `K_KVSTORE_ERROR`：**`Init stream grpc failed!`**（`RETURN_STATUS_LOG_ERROR`）；可能伴随 **`Finish stream with error: ...`** | 无独立 key（流不走 `SendRpc` 表） | **无** → 建议：关键字 `Init stream grpc failed` |
| **`SendKeepAliveMessage` 收到 ttl=0** | **续租逻辑上失败（租约已失效）** | `K_RUNTIME_ERROR`：**`Failed to refresh lease: the new ttl is 0.`** | 同上 | **无** → 建议：与节点下线/迁移关联 |
| **KeepAlive 等待超时** | **在租约周期内未收到有效续租响应** | `K_RPC_UNAVAILABLE`：**`SendKeepAlive Timeout`**；循环中 **`Retrying KeepAlive taken ...`**（`LOG(ERROR)`） | 无 | **无** → 建议：续租超时次数、即将 SIGKILL 前窗口 |
| **写集群节点（带租约 Put）失败** | **`AutoCreate` / `PutWithLeaseId` 写本节点 key 失败** | 同 `Put::etcd_kv_Put` 的 `SendRpc` 包装；另有本机判死：**`local node is failed, keepAliveTimeoutTimer ... not put data to etcd`**（`EtcdStore::Put`） | `DS_ETCD_PUT` | **无** → 建议：集群表 Put 失败 + 本机 “local node is failed” |
| **退出/缩容场景** | **避免在 leaving 时访问 etcd** | `K_RETRY_IF_LEAVING`：**`During worker exit, avoid accessing etcd if etcd fails.`** | 依调用 | **无** → 建议：仅审计，一般不告警 |

---

### 1.3 gRPC 调用超时（deadline）

**说明**：`SendRpc` 对每次调用设置 **`context.set_deadline(..., rpcTimeoutMs)`**，超时后 gRPC 通常返回 **`DEADLINE_EXCEEDED`**，仍被包装为 **`K_RPC_UNAVAILABLE`**，消息中带 **`(4)`** 及 `Deadline Exceeded` 类文案（具体以运行时 `error_message` 为准）。

| 故障检测 | 细分类型 | 典型现象 / 日志 | 指标 | 告警（建议） |
|----------|----------|-----------------|------|----------------|
| **同步 Put/Get/Range 等** | **写集群信息超时** | `[Put::etcd_kv_Put] Send rpc failed: (4) deadline exceeded`（示例形态） | `DS_ETCD_PUT` / `DS_ETCD_GET` / … | Put 超时率、P99 |
| **`LeaseGrant`（AsyncSendRpc）** | **申请租约超时** | `[<addr>,LeaseGrant] Send rpc failed:...` + deadline 相关文案 | `DS_ETCD_LEASE_GRANT` | 连续超时 |
| **KeepAlive** | **续租交互超时** | 见 **`SendKeepAlive Timeout`** 与 **`Retrying KeepAlive taken ...`**（偏“在租约窗口内未完成”） | 无 | 续租超时趋势 |

---

## 二、二级存储（`PersistenceApi` + `SfsClient` / `ObsClient`）

### 2.1 接入方式

| `FLAGS_l2_cache_type` | 客户端实现 | 说明 |
|----------------------|------------|------|
| **`sfs`** | `SfsClient` | 挂载路径下本地文件 API（`mkdir`/`open`/`read`/`write`/`opendir` 等） |
| **`obs`** | `ObsClient` | 对象存储 HTTP(S) + eSDK（错误形态与 SFS 不同） |
| 其他值 | 不初始化 `client_` | 日志：`L2 cache is of type: %s, will not init PersistenceApi.` |

---

### 2.2 文件接口（SFS）— 故障检测与消息

除你提到的 **建目录 / 建文件 / 读写失败** 外，代码中还有 **列举、删除、路径校验、URL 编码、超时** 等路径。

| 故障检测 | 细分故障类型 | 典型 Status / 日志 | 告警（当前→建议） |
|----------|--------------|---------------------|-------------------|
| **`SfsClient::Init`** | **挂载点不可用** | `K_RUNTIME_ERROR`：`SFS path does not exist` / `not a directory` / `not readable` / `not writable` | **无** → 建议：Init 失败即致命告警 |
| | **gflag 未配置** | `K_INVALID`：**`sfs_path gflag can't be empty.`** | 配置类告警 |
| **`NewDirIfNotExists`**（含根目录 `datasystem`、对象目录） | **创建目录失败** | `K_RUNTIME_ERROR`：**`Failed to create the given directory.`** | 与写路径失败率 |
| **`Upload`：open 临时文件** | **创建/打开文件失败** | `K_RUNTIME_ERROR`：**`Failed to open object file with errno: <strerror>`** | I/O 告警 |
| **`LoopUpload`** | **写入超时** | `K_RUNTIME_ERROR`：**`Timed out during uploading object to SFS. Please clear leftovers. Upload to SFS failed.`** | 超时告警 + 残留 `_` 文件巡检 |
| **`rename` 提交** | **原子提交失败** | `K_RUNTIME_ERROR`：**`Rename failed. Upload to SFS failed. errno: %d`** | 数据一致性风险告警 |
| **`Download`** | **对象不存在** | `IfPathExists` → **`K_NOT_FOUND`**：**`Path does not exist`** | 业务/缓存未命中 |
| | **读超时** | `K_RUNTIME_ERROR`：**`Timed out during downloading object from SFS. Read from SFS failed.`** | 读超时 |
| **`List` / `ListOneObject`** | **打开目录失败** | `K_RUNTIME_ERROR`：**`Cannot open the path in SFS for object persistence.`** / **`Cannot open the path for given object`** | 列举失败 |
| | **列举超时** | `K_RUNTIME_ERROR`：**`Timed out during listing objects.`** | 列举超时 |
| **`Delete` / `DeleteOne`** | **删除文件失败** | `Failed to remove object` / `Failed to remove tmp object` | 删除失败 |
| | **打开父目录失败** | `Failed to open directory` | I/O |
| | **部分删除失败汇总** | `K_RUNTIME_ERROR`：**`The following objects were not successfully removed: ...`** | 批量删除不完整 |
| **`GenerateFullPath`** | **路径穿越/不在工作区** | `K_RUNTIME_ERROR`：**`<path> is not in <dsPath>`** | 安全/配置 |
| **`ValidateObjNameWithVersion`** | **非法对象名/版本** | `K_RUNTIME_ERROR`：**`Illegal object name: ...`** | 业务错误 |
| **`PersistenceApi::UrlEncode`** | **curl 初始化/编码失败** | `K_RUNTIME_ERROR`：**`Failed to init curl, encode the object key failed.`** / **`Failed to curl_easy_escape...`** | 不应频繁出现；出现即告警 |
| **`ListAllVersion` 分页异常** | **OBS/SFS List 分页 stuck** | `LOG(ERROR)`：**`the nextMarker ... not change!`** | 建议：分页死循环巡检 |
| **`GetWithoutVersion` / `Del`** | **对象在持久化中不存在** | `K_NOT_FOUND_IN_L2CACHE`：**`The object is not exist in persistence.`** 等 | 与业务删除/一致性相关 |
| **`Del` 完整性** | **指定版本未删到** | `K_NOT_FOUND`：**`The scenarios is delete object ... maxVerToDelete ... is not found...`** | 删除未完成需重试 |

### 2.3 OBS（`ObsClient`）

若 `l2_cache_type == obs`，错误来自 **eSDK/OBS HTTP** 层，消息与码与 SFS 不同；`PersistenceApi` 层仍会打 **`invoke save/get/delete`** 及 **成功/失败 error code** 日志。若需要与 SFS 同级表格，建议再单独扫 `obs_client.cpp` 中 `RETURN_STATUS` / `LOG(ERROR)` 做 OBS 专表（体量较大）。

---

## 三、小结

| 能力 | etcd | 二级存储（SFS 文件） |
|------|------|----------------------|
| **代码内检测** | 有（Status 探活、Writable Put、KeepAlive、Watch、跨 Worker 确认） | 有（Init、各 API 返回码 + `PersistenceApi` 日志） |
| **统一告警** | **未见** | **未见** |
| **建议补齐** | 按 `DS_ETCD_*` + gRPC 码 + KeepAlive 关键字做规则 | 按 Save/Get/Del/List 失败率、超时、残留 `_*` 临时文件、`nextMarker` 日志做规则 |

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04-09 | 初版：对照 `yuanrong-datasystem` 当前代码整理 etcd gRPC / SFS / PersistenceApi |
