# 说明：USE_URMA / UB 路径下连接建立、回退与销毁（与切流/Po2 正交）

- **Status**: Draft  
- **目的**：实现 **Po2、etcd 负载、限流** 等能力时，仍须保证 **UB/URMA 数据面** 在 **Client–Worker** 维度的**建链、重试、回退与析构**有序，避免「逻辑上已切走 / 已退出」但 **URMA 状态或线程未停** 的残留。  
- **非** 完整 URMA 手册；细节以 `urma_manager.cpp`、`client_worker_common_api.cpp`、`urma_resource.cpp` 为准。  
- **时序图**：[diagrams/seq-client-init-and-failover.puml](./diagrams/seq-client-init-and-failover.puml)（与 SD/切流同图）、[diagrams/seq-urma-client-worker-lifecycle.puml](./diagrams/seq-urma-client-worker-lifecycle.puml)（专述 URMA 建链/重试/销毁）；Worker etcd 图末条注见 [diagrams/seq-worker-etcd-lease.puml](./diagrams/seq-worker-etcd-lease.puml)。

---

## 1. 编译期与运行期开关

| 概念 | 含义 |
|------|------|
| **`#ifdef USE_URMA`** | 编译进 **URMA/快路径** 相关代码；未定义时无此类路径。 |
| **`FLAGS_enable_urma`（运行时）** | 是否**实际**走 URMA；可与对端能力、握手结果、SHM 条件有关。 |

**注意**：`FastTransportHandshake` 失败时会 **`FLAGS_enable_urma = false`** 并 **回退 TCP**（见 `client_worker_common_api.cpp`），之后同进程内 **勿假设** 仍走 UB；切 Standby 后 **新** `ClientWorkerRemoteCommonApi` 会重新走注册与 `TryFastTransportAfterHeartbeat`，需按**新连接**看待。

---

## 2. 建立（Client 侧）

1. **RegisterClient** 成功后，若有 **pending 快路径握手**（`pendingFtHandshake_`），在 **`TryFastTransportAfterHeartbeat`** 里进入 **`FastTransportHandshake` → `InitializeFastTransportManager`（含本地 URMA 预热）**；`USE_URMA` 且 `UrmaManager::IsUrmaEnabled()` 时还会 **异步 `AsyncFirstUrmaHandshake` → `TryUrmaHandshake`**（与对端交换、JFR 等）。  
2. **握手可能晚于心跳启动**：若首次 URMA 握手在约定时间内未结束，日志会提示**后台继续**；**`ScheduleUrmaHandshakeRetry`** 会按版本号与 `stopUrmaHandshakeRetry_` 协作，避免**旧连接**上重试覆盖**新 Worker** 会话。  
3. **按 Worker 端点建 transport**：`TryUrmaHandshake` 内对 `hostPort` 有 **并发 map** 复用/创建 `WorkerRemoteWorkerTransApi`；切流后 **新地址 = 新握手域**，旧 `ClientWorkerRemoteCommonApi` **析构** 时应能停掉重试与池（见 §4）。

---

## 3. Worker 侧与对端一致性

- Worker 与 Client 的 **`enable_urma` / UB 模式** 需**部署层**对齐；测试表明**混开**时部分路径可 **TCP 兜底**，但**不能**作为生产默认策略。  
- **W↔W** UB/URMA 与 **C↔W** 共用大量栈时，**销毁** 顺序（先停业务线程、再清连接表）错误会导致 **JFR/seg** 类问题，修改 Po2/限流 时**勿** 只改 Client 而忽略 **Worker 侧** `RemoveClient` / URMA import 清理由。

---

## 4. 销毁与切换（须保证的语义）

1. **正常退出**：`ClientWorkerRemoteCommonApi` **析构** 时设 **`stopUrmaHandshakeRetry_ = true`**，停收 fd 线程、**reset 握手池**；确保**不再** 向已关闭会话投递重试。  
2. **先通知 Worker 再断**：`Disconnect` **RPC** 在资源回收路径上应仍可用（`commonWorkerSession_` 非空时），使 Worker 侧能 **RemoveClient** 与 **对 URMA/UB 连接做对称释放**（具体见 Worker 的 disconnect 处理）。  
3. **切 Standby**：`StopListenWorker`、替换 `workerApi_` 前，旧 API 的 **析构/Disconnect** 应**完成**或**可证明** 无并发 URMA 回调在跑；否则新 Worker 的握手与旧实例**重叠** 易导致 **device/segment 混用** 或**重复建连**。  
4. **UrmaManager 单例/生命周期**：`UrmaManager` **析构** 走 **`Stop` → 清 map → `UrmaResource::Clear` → `UrmaUninit`** 等；多 Client/多 Worker 进程模型下需明确 **每进程** 一个 manager 的假设，**切流** 不应对**同一进程**错误地**二次 Init 未配对的 Destroy**（若未来支持多集群，需单独设计）。

---

## 5. 与本 RFC（Po2 / 均衡）的关系

- **切流** 会短时间增加 **新 Worker 上 URMA 首次握手** 的并发；**Po2** 和 **限流** 缓解 **堆积**，但不替代 §2–§4 的**正确性**。  
- **etcd 上的负载数字** 与 **URMA 是否已完全 ready** 可能**不同步**（握手慢于连接数+1）：若用连接数/UB 作 Po2，**陈旧度阈值** 与 **READY 判定** 需在联调中写清。  

---

## 6. 相关代码索引（便于打开）

| 主题 | 路径（`yuanrong-datasystem`） |
|------|------------------------------|
| 握手/回退/异步重试 | `src/datasystem/client/client_worker_common_api.cpp`（`TryFastTransportAfterHeartbeat`、`TryUrmaHandshake`、`ScheduleUrmaHandshakeRetry`、析构） |
| URMA 资源与全局清理 | `src/datasystem/common/rdma/urma_manager.cpp`（`Init`、`~UrmaManager`、`Stop`） |
| 切 Standby 调 Init/握手 | `src/datasystem/client/object_cache/object_client_impl.cpp`（`TrySwitchToStandbyWorker`） |
| 测试（含 enable_urma 错配） | `tests/st/client/object_cache/urma_object_client_test.cpp` |

---

## 7. 实施清单（自测时勾选）

- [ ] 单 Client **反复切 Worker**（故障注入），无 **JFR/URMA 泄漏** 与 **重试打向旧 id** 的日志风暴。  
- [ ] 进程 **exit** 路径上 **gcore** 无 URMA 线程悬挂（按环境可抽样）。  
- [ ] 与 `FLAGS_enable_urma` **回退 TCP** 后，业务仍正确；再切到 URMA 开启的 Worker 时 **全链路再握手** 成功。  
