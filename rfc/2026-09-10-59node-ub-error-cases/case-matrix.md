# 全部失败类别证据矩阵

源文件路径均相对 DataSystem 的 `src/datasystem/`；数量来自与归档 manifest 汇总一致的结构化模型。下列均为调查结论，尚非修复完成声明。

| 失败类别 | 数量 | SET / GET | 延迟中位数 ms | 最大 ms |
|---|---:|---|---:|---:|
| URMA完成等待超时（同Trace证据） | 6036 | 2591 / 3445 | 20.206 | 47.616 |
| Publish RPC截止超时 | 4747 | 4747 / 0 | 20.650 | 33.943 |
| Client路由：等待transport snapshot刷新超时 | 2032 | 0 / 2032 | 20.185 | 57.014 |
| Create RPC截止超时 | 1199 | 1199 / 0 | 20.163 | 48.561 |
| Client API预算耗尽：调用/重试检查 | 1105 | 49 / 1056 | 20.218 | 57.875 |
| URMA建链：握手RPC截止超时 | 982 | 982 / 0 | 20.215 | 21.140 |
| 业务逻辑：所有副本均未找到对象 | 505 | 0 / 505 | 0.356 | 19.623 |
| QueryAndGet RPC截止超时 | 431 | 0 / 431 | 20.316 | 51.460 |
| URMA准入：获取peer jetty槽位前预算耗尽 | 40 | 40 / 0 | 20.204 | 37.287 |
| 数据读取RPC截止超时 | 12 | 0 / 12 | 20.644 | 20.894 |
| URMA建链：API预算耗尽（具体阶段未观测） | 1 | 1 / 0 | 23.296 | 23.296 |

## URMA完成等待超时（同Trace证据）

GET 最终 1001 与 SET 最终 1010 分开；同 Trace 的 WR 超时不证明硬件或代码根因。R05 示例 8 MiB SET 拆成两个 4 MiB WR，第一项等待超时，第二项已完成；需复现部分完成与预算/资源收尾。

源码入口：`common/rdma/urma_manager.cpp:WaitForUrmaEvent；common/rdma/urma_resource.h:UrmaEvent::WaitFor`。

代表 Trace：`R01 / getBuffer-58-757-00139050;28de8ffd9e89`。

复现状态：R1 已验证分片超时错误传播；B2 已 RED/GREEN 修复批级回调早于完整故障聚合且可能重复的问题。历史 CQE 迟到根因尚未闭合。

## Publish RPC截止超时

代表请求 Worker Publish 已成功但 Client 超时；检查响应时序及提交不确定性，不能把 Client 超时当成服务端未提交。

源码入口：`client/object_cache/transport/rpc/worker_rpc_client.cpp:InvokeSet；client/object_cache/client_worker_api/client_worker_remote_api.cpp:Publish`。

代表 Trace：`R02 / setStringView-37-62-00000724;c3027ff3b8fb`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## Client路由：等待transport snapshot刷新超时

最终文案包含 snapshot 不含目标，但源码发现 metadata owner RPC 失败也被包装成同一 STALE_TRANSPORT_SNAPSHOT_MESSAGE，因此不能把此文案当成真实成员缺失的证据。当前基线新增 degraded admission 与版本交叉确认，须在当前代码复现，不能重做已存在修复。

源码入口：`client/object_cache/routed_mode.cpp:ApplyTransportReadRetryBudgetFailure；client/object_cache/transport/data_plane/data_plane_manager.cpp:GetOrCreateEntry`。

代表 Trace：`R02 / getBuffer-28-62-00000978;16f72f99ffa7`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## Create RPC截止超时

代表请求服务端 Create 24.097 ms，Client 20.156 ms 超时；分配内部未细分，不直接归因网络。

源码入口：`client/object_cache/transport/rpc/worker_rpc_client.cpp:InvokeCreate`。

代表 Trace：`R01 / setStringView-43-754-00137996;71b3bcf0b366`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## Client API预算耗尽：调用/重试检查

1056 GET、49 SET；需要确认调用前处理、重试或调度的预算消耗，不把最后检查点当作首发故障。

源码入口：`common/rpc/api_deadline.h；client/object_cache/routed_mode.cpp`。

代表 Trace：`R02 / getBuffer-28-62-00000998;ef712b515c05`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## URMA建链：握手RPC截止超时

982 SET；代表 trace 握手超时且没有有效服务端时间，不能将 0 填充值解释为服务端零耗时。

源码入口：`client/object_cache/transport/rpc/worker_rpc_client.cpp:ExchangeUrmaConnectInfo`。

代表 Trace：`R16 / setStringView-171-777-00000103;2bdb8e35a190`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## 业务逻辑：所有副本均未找到对象

504/505 集中 R05。已检查 R05 结构化证据中的对象关联：504 条对象未找到请求均没有采集到同对象 SET，无法区分之前写入失败、TTL 到期、主动删除或实际丢失；不能认定代码导致数据丢失。

源码入口：`client/object_cache/transport/object_read/replica_reader.cpp`。

代表 Trace：`R05 / getBuffer-30-1414-00340667;571fe0e0c781`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## QueryAndGet RPC截止超时

同 Trace 可见 Worker URMA 完成，Client 仍超时；需要分开业务响应、框架与最终路由重试。

源码入口：`client/object_cache/transport/metadata/object_metadata_client.cpp`。

代表 Trace：`R01 / getBuffer-3-754-00277445;cc3e20e70d39`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## URMA准入：获取peer jetty槽位前预算耗尽

代表请求 Create 消耗 19.966 ms，URMA 阶段仅 5 us，随后 8 MiB TCP fallback 被 1 MiB 限制拒绝；不能仅从错误名推断 jetty 队列拥塞。

源码入口：`common/rdma/urma_manager.cpp:AcquireSendJettyWithDeadline`。

代表 Trace：`R01 / setStringView-33-756-00138122;9d44bcd87ec6`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## 数据读取RPC截止超时

12 GET；代表 QueryAndGet 15.282 ms、数据读取 4.763 ms，检查剩余预算、重试及首发错误。

源码入口：`client/object_cache/transport/object_read/replica_reader.cpp`。

代表 Trace：`R26 / getBuffer-31-2455-00113458;24a9c61b4fdb`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。

## URMA建链：API预算耗尽（具体阶段未观测）

仅 1 SET，最终 23.296 ms；握手内部具体耗时未观测，保留未闭合。

源码入口：`client/object_cache/transport/rpc/worker_rpc_client.cpp:GetRpcTimeout`。

代表 Trace：`R28 / setStringView-80-4117-00000108;e3dd165de721`。

复现状态：当前源码契约与待补证据见文末；历史现场根因尚未闭合。


## 当前源码的可执行契约与现场差距

以下名称均为当前源码中存在的测试。已执行结果集中记于 RFC 验证进展；既有测试通过不能视为新缺陷 RED，也不能替代历史现场复现。

| 报告路径 | 测试契约 | 仍缺少的现场证据 |
|---|---|---|
| URMA 等待、部分完成 | `ChunkTimeoutPreservesErrorAndDrainsPrecompletedChunk`；`TimedOutWriteRetainsEventButReleasesWaiterForLateCqe4`；`TimedOutLaneIsForceReleasedAndLateCqeDoesNotCompleteReplacementLane` | 真实 WR/CQE、调度、设备状态与部署版本 |
| Publish 截止 | `PublishRpcFailureMarksEverySubmittedObjectFailed`；`MSetDoesNotReplayAmbiguousRpcFailure` | 已超时 SET 的最终提交/查询结果，以及响应交付时序 |
| Snapshot 刷新 | `PersistentOwnerDeadlineWrapsAfterBoundedInnerRetries`；`StaleLocationSlowReadDeadlineReturnsPublicDeadline`；`AlternatingRefreshableErrorsKeepConsumedRetryBudgets` | 失败当时版本、成员变化和原始 owner RPC 原因 |
| Create 截止 | `ExplicitWorkerCreateDeadlineDoesNotScheduleAmbiguousCleanup`；`AmbiguousCreateFailureSchedulesRepeatedCleanupForStableAllocationId`；`TcpFallbackKeepsAmbiguousShmAllocationForCleanup` | 服务端分配各阶段、失败后 allocation 的清理结果 |
| API 与读取 RPC 预算 | `ExpiredApiDeadlineDoesNotSendRpc`；`BoundsRpcTimeoutByApiDeadline`；`BatchDeadlineReturnsLastMeaningfulItemError` | 预算起点、排队/重试明细和调用线程调度 |
| URMA 握手与准入 | `UbConnectionFailureFallsBackToTcp`；`RejectedFallbackPreservesReconnectStatus`；`InFlightWaitTimeoutStatusIncludesPeerContext` | 握手端时间和准入前预算；这些测试未逐一重建真实握手截止场景 |
| 对象未找到 | `StopsOnNotFoundWithoutTryingNextLocation`；`BatchNotFoundTerminatesWithoutTryingNextReplica` | 同对象先前 SET 最终结果、TTL/删除及副本生命周期 |
| QueryAndGet | `DispatchedShmDegradationDoesNotEnterRetry`；`ConfirmedOwnerFailureStillReroutesDispatchedShm`；`SafeDegradedQueriesStillReachRetryAdmission` | B1 已复现当前 SHM 缺陷，真实 UB 路径仍需独立根因证据 |

排序最靠前的 URMA/Publish 两类没有因现有断言通过而降级为“无问题”。继续定位应从这些缺口补证，而不是把约 20 ms 的预算统一调大、把 snapshot 文案解释成拓扑事实，或把 NotFound 当作已证实丢数据。

## Publish 与 Create 的全量 access 证据复核

对全部 17,090 条失败按 run/Trace 关联原结构化证据，Publish 类未出现重复服务端 Publish 记录，且这批证据没有 dropped_evidence。4747 条 Publish 截止中：服务端成功 4705、服务端失败 26、未观测 16。失败状态为 3 共 22 条、1001 共 4 条；不把这些服务端失败合并进成功提交。

关键前因：全部 4747 条有 Client Create/Publish 阶段计时；4306 条 Create 已消耗至少 10 ms，27 条至少 19 ms。Create 中位数 12,892 us，Publish 中位数 7,124 us。4745 条有 Client URMA 阶段计时，中位数 245 us，仅 1 条达到 10 ms。各阶段阈值分布不是互斥分类，不能相加作为失败总数。

该群体大多数先在 Create 的 RPC 父阶段消耗预算，Publish 是最后超时位置。服务端 access 窗口普遍很短，不能将父阶段差额直接称作物理网络耗时；需要补充 RPC 收发/排队、线程调度和客户端端到端预算证据。当前复现优先验证提交不确定性与共享预算，再追查 Create 路径。

### Create RPC 四时间戳复核

4747 条 Publish 截止样本的先行 Create RPC 全部具有有效四时间戳；逐条重新计算 `(ClientRecv - ClientSend - ServerSend + ServerRecv) / 1000`，与结构化残差差异超过 2 us 的记录为 0。Create RPC e2e 中位数 12,879 us，server execution 中位数 37 us，残差中位数 12,839 us。4279 条残差达到 10 ms；4707 条残差占 RPC e2e 至少 90%。

源码边界已确认：`common/rpc/plugin_generator/brpc_stub_generator.cpp:BuildCallMethodAndDiagnosticsSnippet` 在发送前 VLOG 和 `CallMethod` 之前标记 ClientSend；`common/rpc/brpc_server_unary_writer_reader.h` 在应用 adapter 构造处标记 ServerRecv；`common/rpc/brpc_perf_trace.h:RecordBrpcRpcTrace` 从 Client 往返窗口减去服务端窗口。故残差包含应用计时点外的框架工作、排队、调度与传输，不能识别其单独贡献。`server_req_queue=0` 只覆盖 adapter 内已埋点区间，不等于 bRPC/内核所有队列均无等待。

当前明确缺口：对应运行的实际部署版本、worker/client 参数、日志等级及同步/异步配置、bRPC 队列/线程数据、调度 profile、网络与 CQE 证据。缺少这些时，扩大 Mock 延迟注入只能重现相同外观，不能证明真实首因，也不足以合理选择增加线程、调整队列或修改超时。上述证据缺口不否定已完成的 B1/B2 当前代码修复。

## 失败日志的 transportType 不能单独证明传输路径

当前源码与报告分析引用 a505893 的 `AccessTransportTracker::Reset` 都先将 transport 设为 SHM；现有 `DefaultIsShmAfterReset` UT 也明确固定了这个默认值。`UbTransporter::Set` 在 `PublishSetPayload` 成功返回后才通过 `SetTransportResponseStatus` 记录 UB/TCP；此前的失败可能保留默认 SHM。GET 的 `RoutedMode::FinishTransportRead` 也有条件记录实际 kind，未成功产生数据的请求可能保留默认值。

因此，失败日志中的 `transportType:SHM` 不能证明请求确实走了本节点共享内存，也不能据此把整个 59 节点运行解释为 SHM 运行。原模型全部 GET 失败标记为 SHM，属于这个字段的观测值，不是经过拓扑和成功数据传输校验的路径结论。R05 SET 的同 Trace 中同时出现 SHM 字段与真实 URMA WRITE 等待超时，也与默认值保留相符。后续归因应使用同 Trace 的真实 WR、RPC/Worker 路径和成功数据来源，缺失时标为未观测。

这是现有诊断字段的语义限制；本轮没有凭该字段扩大 B1 的历史因果声明，也未将默认值改变混入已验证的错误处理修复。
