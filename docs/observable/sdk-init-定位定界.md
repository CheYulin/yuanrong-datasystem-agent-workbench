# SDK Init 接口：定位定界手册

本文档面向 **Object Client SDK 的 `Init`（及嵌入式 `InitEmbedded`）**，说明调用链、可能错误码、日志关键词，以及如何区分 **操作系统/网络环境** 与 **数据系统（Worker/配置/IAM）** 问题。

**代码仓库**：`yuanrong-datasystem`（下文路径均相对该仓库根目录）。

---

## 1. 接口与入口

| 场景 | 典型入口 | 说明 |
|------|----------|------|
| 远程 Worker | `ObjectClientImpl::Init` | 需配置 `host:port` 或 `serviceDiscovery` |
| 嵌入式 | `ObjectClientImpl::InitEmbedded` | 同进程加载 Worker 插件，注册走本地 `WorkerRegisterClient` |

公共连接逻辑在 `InitClientWorkerConnect`：创建 `workerApi`、执行其 `Init`、启动 `ListenWorker`、初始化设备侧 `ClientDeviceObjectManager`（无设备运行时时仅打 INFO，不失败）。

---

## 2. Init 主路径（远程）

```
ObjectClientImpl::Init
  ├─ Logging / FlagsMonitor / serviceDiscovery（可选）
  ├─ 校验 HostPort
  ├─ RpcAuthKeyManager::CreateClientCredentials（可选 ZMQ CURVE）
  └─ InitClientWorkerConnect
        ├─ ClientWorkerRemoteApi::Init
        │     ├─ ClientWorkerRemoteCommonApi::Init
        │     │     ├─ TimerQueue::Initialize
        │     │     └─ Connect
        │     │           ├─ CreateConnectionForTransferShmFd（可选）
        │     │           │     ├─ RPC: GetSocketPath
        │     │           │     └─ 本地 UDS/SCMTCP 握手（传 server_fd）
        │     │           └─ RegisterClient（RPC）
        │     └─ 创建 RpcChannel + WorkerOCService_Stub（业务通道）
        ├─ MmapManager 构造
        ├─ PrepairForDecreaseShmRef
        ├─ InitListenWorker → ListenWorker::StartListenWorker
        │     └─ RPC_HEARTBEAT：connectTimeoutMs 内必须收到首帧心跳
        └─ ClientDeviceObjectManager::Init
```

**嵌入式**省略 `GetSocketPath` 与跨机 SHM 握手，`Connect` 内直接 `WorkerRegisterClient`。

---

## 3. Init 阶段涉及的 RPC / 本地动作

| 阶段 | 类型 | 名称 / 动作 |
|------|------|-------------|
| 可选 | RPC | `GetSocketPath`（`FLAGS_ipc_through_shared_memory` 且能解析出 UDS 路径或 SCMTCP 端口） |
| 可选 | 本地 socket | UDS/SCMTCP 连接 + 接收 `server_fd` |
| 核心 | RPC | `RegisterClient`（`WorkerServiceImpl::RegisterClient`） |
| 核心 | RPC 循环 | `Heartbeat`（`ListenWorker` 在 `RPC_HEARTBEAT` 模式下，`connectTimeoutMs` 内要完成首次成功） |

**注意**：`PostRegisterClient` 中的 `FastTransportHandshake`（如 URMA）失败仅 **`LOG_IF_ERROR`**，**不导致 Init 失败**。

---

## 4. 错误码与日志树（客户端可见）

客户端失败时常见形式：`LOG(ERROR) << "<前缀>. Detail: " << status.ToString()`（宏 `RETURN_IF_NOT_OK_PRINT_ERROR_MSG`）。

### 4.1 配置与本地前置（多在 Register 之前）

| 错误码 | 典型消息 / 现象 | 可能原因 | 定界 |
|--------|-----------------|----------|------|
| `K_INVALID` (2) | `ConnectOptions was not configured with a host and port or serviceDiscovery` | 未配地址且无服务发现 | 数据系统 / 集成配置 |
| `K_INVALID` (2) | `Invalid IP address/port` | HostPort 非法 | 配置 |
| `K_INVALID` (2) | `connectTimeoutMs` / `requestTimeoutMs` 校验失败 | 超时参数非法 | 配置 |
| `K_RUNTIME_ERROR` (5) | `TimerQueue init failed!` | 客户端定时器初始化失败 | 进程环境 / 客户端 |
| `K_RPC_UNAVAILABLE` (1002) | `Can not create connection to worker for shm fd transfer.` | 必须 SHM 通道但未建立（如跨节点、Worker 未开 SHM） | 拓扑+配置偏数据系统；跨机属环境 |

**日志关键词**：`Start to init worker client at address`、`Invalid IP`、`TimerQueue init`。

### 4.2 GetSocketPath / 网络 / SHM 握手

| 错误码 | 客户端日志前缀 | 可能原因 | 定界 |
|--------|----------------|----------|------|
| 1001 / 1002 / 1000 等 | `Get socket path failed. Detail:` | Worker 不可达、超时、拒绝连接 | 优先网络、防火墙、Worker 是否监听 |
| — | `Client can not connect to server for shm fd transfer within allowed time`（WARNING） | UDS/SCMTCP 连不上或握手超时 | UDS 权限、路径、同机；Worker SHM 监听 |
| — | `Failed connect to local worker via ... falling back to TCP`（INFO） | 非本机或通道不可用 | 环境 / 配置预期 |

### 4.3 RegisterClient（Worker 返回映射到客户端 Status）

Worker 侧失败常带 `LOG(ERROR) "<步骤>. Detail: ..."`，客户端对应 `Register client failed. Detail: ...`。

| 错误码 | 典型含义 | 可能原因 | 定界 |
|--------|----------|----------|------|
| `K_NOT_AUTHORIZED` (9) | 签名校验 / 租户失败 | Token、AK/SK、租户 ID、时钟漂移 | IAM 配置；时钟属 OS |
| `K_INVALID` (2) | AK/SK 管理器未初始化等 | Worker 配置异常 | 数据系统 |
| `K_RUNTIME_ERROR` (5) | `AK/SK or token not provide` | 认证开启但未带凭证 | 客户端与 Worker 认证策略 |
| `K_NOT_READY` (8) | `Worker is exiting and unhealthy now!` | Worker 正在退出 | 数据系统生命周期 |
| `K_SERVER_FD_CLOSED` (29) | `Fd %d has been released` | 传递的 server_fd 已被 Worker 回收 | 竞态 / Worker 状态；客户端会转为 `K_TRY_AGAIN` 重试 |
| `K_TRY_AGAIN` (19) | 含由上项改写 | 可重试场景 | 结合 Worker 日志看根因 |
| `K_RUNTIME_ERROR` (5) | `Client number upper to the limit` | 连接数达 `max_client_num` | 数据系统容量或连接泄漏 |
| `K_RUNTIME_ERROR` (5) | `Failed to insert client %s to table` | 客户端表冲突等 | 数据系统状态 |
| `K_RUNTIME_ERROR` (5) | `worker add client failed`（Detail 内见 epoll/RegisterLostHandler） | UDS 心跳注册 fd 失败等 | fd/epoll 资源偏 OS；逻辑错误偏 Worker |
| `K_RUNTIME_ERROR` (5) | `worker process server reboot failed` | 重连恢复 `GIncreaseRef` 等失败 | 数据系统元数据/业务状态 |
| `K_RUNTIME_ERROR` (5) | `worker process get ShmQ unit failed` | SHM 队列未就绪、索引越界、分配失败 | Worker OC 与内存配置；mmap 失败兼看 OS |
| `K_RUNTIME_ERROR` (5) | `worker process get exclusive connection socket path failed` | 独占连接能力与版本不匹配 | 数据系统版本/特性 |

**日志关键词（客户端）**：`Start to send rpc to register client to worker`、`Register client failed`、`Register client to worker through the ... successfully`。

**日志关键词（Worker）**：`Register client:`、`Authenticate failed`、`worker add client failed`、`worker process get ShmQ unit failed`、`Register client failed because worker is exiting`。

### 4.4 Register 成功后：首心跳超时

| 错误码 | 消息 | 可能原因 | 定界 |
|--------|------|----------|------|
| `K_CLIENT_WORKER_DISCONNECT` (23) | `Cannot receive heartbeat from worker.` | `connectTimeoutMs` 内未收到首次 Heartbeat 响应 | Worker 过载或阻塞；网络 RTT/丢包；超时过短 |

**日志关键词**：`Start listen worker, heartbeat type: RPC_HEARTBEAT`。

### 4.5 ZMQ CURVE（可选）

| 错误码 | 典型消息 | 定界 |
|--------|----------|------|
| `K_RUNTIME_ERROR` (5) | `Client public key should not be null` / `Server key should not be null` | 客户端或服务端密钥未配置 |

**代码**：`RpcAuthKeyManager::CreateClientCredentials`、`CreateCredentialsHelper`。

---

## 5. 定界决策（简表）

1. **无任何 Worker 侧 Register 日志，只有 RPC 超时 / UNAVAILABLE / DEADLINE**  
   → 优先 **网络、防火墙、Worker 进程、监听地址**（环境 + 部署）。

2. **Worker 有 `Authenticate failed`**  
   → **IAM / Token / AK-SK / 租户 / 时间同步**（配置为主，时钟为 OS）。

3. **`Get socket path failed` 成功但 SHM 握手 WARNING 不断**  
   → **同机性、UDS 目录权限、`ipc_through_shared_memory` 与路径**（环境 + Worker 配置）。

4. **`Register client failed` 且 Detail 含 `ShmQ` / `queue`**  
   → **Worker OC 服务与共享内存初始化**（数据系统为主）。

5. **`Cannot receive heartbeat from worker`**  
   → **调大 `connectTimeoutMs` 做对比**；查 Worker 负载与 Heartbeat 处理；仍失败则查 **网络**。

---

## 6. 推荐采集信息（工单模板）

- SDK 版本 / `DATASYSTEM_VERSION` 与 Worker 版本是否一致（Worker 对版本不一致会打 WARNING，Register 仍可能成功）。
- `Init` 入参：`host`、`port`、`connectTimeoutMs`、`requestTimeoutMs`、`tenantId`、是否开启跨节点 / 独占连接 / 服务发现。
- 客户端完整 **`Register client failed. Detail: ...`** 或返回的 **`Status::ToString()`**。
- 同时间点 Worker 日志片段：`Register client` 起止、`Authenticate failed`、`worker add client`、`get ShmQ`。
- 环境：是否跨机、是否开启 SHM、`unix_domain_socket_dir` 与目录权限、大致连接数。

---

## 7. 代码索引（便于对照）

| 内容 | 路径 |
|------|------|
| Init | `src/datasystem/client/object_cache/object_client_impl.cpp`（`Init` / `InitClientWorkerConnect`） |
| 远程 Connect / Register | `src/datasystem/client/client_worker_common_api.cpp` |
| Worker Register | `src/datasystem/worker/worker_service_impl.cpp`（`RegisterClient`） |
| 认证 | `src/datasystem/worker/authenticate.cpp` |
| 客户端数 / lockId | `src/datasystem/worker/client_manager/client_manager.cpp`（`GetLockId`、`AddClient`） |
| SHM 队列单元 | `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`（`GetShmQueueUnit`） |
| 首心跳 | `src/datasystem/client/listen_worker.cpp`（`StartListenWorker`） |
| 错误码枚举 | `include/datasystem/utils/status.h` |
| 错误日志宏 | `src/datasystem/common/util/status_helper.h`（`RETURN_IF_NOT_OK_PRINT_ERROR_MSG`） |

---

## 8. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04-08 | 初版：基于 `ObjectClientImpl::Init` / `WorkerServiceImpl::RegisterClient` 代码路径整理 |
