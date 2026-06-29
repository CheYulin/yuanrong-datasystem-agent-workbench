# Client Direct Read PR Review Guidance - 2026-06-25

## 背景

Review target: `openeuler/yuanrong-datasystem` PR #1119, branch `feature/client-direct-read-flow`.

目标不是单纯让 client 绕过 gateway，而是把 client/worker 的 read 访问流程尽可能复用，同时保持角色边界清晰：

- Client 本地无健康 worker 时，可以直接访问远端 workers 的 Meta/Data TCP 路径。
- 本地 worker 恢复后，可以回切到 gateway/local worker path。
- 扩缩容、hash ring 更新、meta moving、redirect、stale route 都能正确处理。
- 多次 RPC 出错时能 retry/fallback，不挂死、不丢 object 顺序，不破坏 partial not found 语义。
- Common 层只承载真实共同语义，不用空实现或未调用接口制造“看起来复用”。

## 当前 Review 结论

当前实现已经比初版明显收敛，基础 ST 覆盖也更完整：

- 本地无 worker direct read 有 ST。
- 本地 worker 恢复 cutback 有 ST。
- distributed master cutback 有 ST。
- scale down/up 后 direct read 仍能读到数据有 ST。
- meta timeout、redirect loop、stale route、data worker unavailable 有 fallback 覆盖。

但合入前仍需处理几个高风险点：

1. Worker 返回的 hash ring snapshot 仍缺少可比较版本；client 端 `LoadFromWorker` 后使用 `version = -1` 会削弱 stale snapshot 保护。
2. Direct read 运行态对象生命周期需要收敛；请求热路径不能无锁重置正在被 `DirectReadFlow` 使用的 ring source / RPC adapter。
3. Data TCP timeout 语义不能静默丢弃。若设计上要使用 RPC timeout `0`，必须显式设置并补测试证明整体 Get 会收敛。
4. 批量 data phase 不能逐对象串行新建 RPC channel/stub，否则 MGet 在 direct path 上会退化为 N 次串行远端 RPC。
5. Data 也应该抽象，但要抽真实可复用流程；不要在 meta flow 上挂一个未使用的 data client，也不要保留 `WorkerNullDataClient` 这类空实现。

## 目标架构

将 read 访问拆成三层。

### 1. ReadMetaFlow：共用层

输入：

- `objectKeys`
- request/subscription timeout context
- route provider
- meta transport

职责：

- 按 key 找 meta address。
- 按 meta address 分组 `QueryMeta`。
- 处理 redirect、meta moving、stale route refresh。
- 合并 `QueryMetaRspPb` 和 meta payloads。
- 保持结果和原始 `objectKeys` 可对齐。

要求：

- Client direct read 和 worker gateway 都要实际调用这一层。
- QueryMeta transport 只做一次 RPC；redirect/moving/retry 留在 common orchestration。

### 2. ReadDataFlow：共用层

输入：

- 原始 `GetParam`
- `ReadMetaFlow` 输出的 `QueryMetaInfoPb`、`not_exist_ids`、`metaPayloads`
- data transport

职责：

- 遍历或按 data address 分组 `QueryMetaInfoPb`。
- 处理 not found。
- 处理 inline payload。
- 处理 remote payload。
- 生成 `GetRspPb::PayloadInfoPb`。
- 维护 payload `part_index`。
- 保持 `objectKeys` 顺序和 buffers 对齐。
- 聚合 data phase 错误，返回可诊断 `Status`。

要求：

- Client direct read 和至少一个 worker read path 都要实际复用这一层。
- 不要只定义接口但没有真实调用方。
- 批量 Get 至少按 data worker/address 分组并复用 transport；能并发则更好。

### 3. DataTransport：差异层

`ClientRemoteTcpDataTransport`：

- Client 直连 data worker。
- 调用 worker `GetObjectRemote` TCP。
- 负责 client 侧 RPC channel/stub/credential/signature。

`WorkerLocalDataTransport`：

- Worker 复用现有本地/gateway data 读取能力。
- 不要复制 worker 内部复杂状态机到 common。

Transport 只负责“怎么取数据”，不要处理：

- hash ring refresh
- redirect/moving loop
- client fallback
- local worker cutback
- feature gate

## Timeout 语义

不要继续使用 `(void)subTimeoutMs` 静默丢弃超时语义。

可接受两种实现之一：

1. 如果 `GetObjectRemote` TCP 约定 RPC timeout `0` 表示由服务端订阅/上层 request deadline 控制，则显式设置 `RpcOptions` timeout 为 `0`，并在代码注释中说明 direct data RPC 的收敛边界。
2. 如果 timeout `0` 不能保证收敛，则使用本次 Get 的有效 timeout/deadline。

无论选择哪种，都必须补测试：

- data worker 慢响应或卡住时，direct read 不会无限挂。
- fallback 开启时能按预期 fallback。
- fallback 关闭时能返回可诊断错误。

## Hash Ring 与扩缩容

必须修正 worker snapshot 版本语义：

- `GetClusterStateRspPb` 增加 hash ring 的可比较版本字段。
- Worker 返回当前 ring 对应的 etcd mod revision，或等价的权威版本。
- Client 从 etcd bootstrap 得到的是 etcd mod revision；worker snapshot 也必须进入同一可比较版本空间。
- 不要继续用 `version = -1` 混合 etcd snapshot 和 worker snapshot。

扩缩容测试不能只断言“读成功”。需要验证：

- ring revision 单调更新，旧 worker snapshot 不会覆盖新 snapshot。
- scale up `changed_ranges`。
- scale down。
- meta moving。
- redirect。
- stale route refresh。
- direct read 没有被 fallback 成功掩盖路由问题。

## Client 独有逻辑

这些逻辑应留在 client 层，不进入 common：

- `enable_client_direct_read` feature gate。
- 本地无健康 worker 时是否进入 direct read。
- 本地 worker 恢复后的 cutback。
- direct read 失败后是否 fallback 到 gateway。
- `DirectReadTestHook` / direct read stats。
- client ring source 生命周期和远端 workerApi 选择。

## Client/Worker 应复用的逻辑

这些逻辑应尽量沉到 common：

- QueryMeta 编排。
- redirect/moving 处理。
- meta response 和 payload merge。
- `QueryMetaInfoPb` 到 `GetRspPb::PayloadInfoPb` 的转换。
- inline payload 和 remote payload 的 `part_index` 维护。
- not found / partial object result 的顺序对齐。
- 批量对象按 meta address / data address 分组的框架。
- `ReadDataFlow` 的错误聚合语义。

## 禁止做法

- 不要在 MetaFlow 构造函数里塞 `IObjectReadDataClient`。
- 不要保留 `WorkerNullDataClient` 这种空实现。
- 不要让 common 层依赖 client direct read fallback/cutback 策略。
- 不要把 worker 内部 shm、迁移、引用计数、gateway 专有状态复制到 client。
- 不要为了复用引入未使用接口。
- 不要每个 object 都串行新建 `RpcChannel`/stub 发 data RPC。

## Cursor Prompt

```text
请继续修 PR #1119：client direct read flow。

目标是尽可能复用 client/worker 的 meta/data read 访问流程，但复用必须以真实共同语义为边界，不能用空实现或未使用接口凑复用。

架构拆分：
1. ReadMetaFlow，共用：
   - 按 key 找 meta address。
   - 按 meta address 分组 QueryMeta。
   - 处理 redirect、meta moving、stale route refresh。
   - 合并 QueryMetaRspPb 和 meta payloads。
   - 保持结果和原始 objectKeys 可对齐。
   - client direct read 和 worker gateway 都要实际复用。

2. ReadDataFlow，共用：
   - 消费 GetParam、QueryMetaInfoPb、not_exist_ids、metaPayloads、data transport。
   - 处理 not found、inline payload、remote payload。
   - 生成 GetRspPb::PayloadInfoPb。
   - 维护 payload part_index。
   - 保持 objectKeys 顺序和 buffers 对齐。
   - 聚合 data phase 错误并返回可诊断 Status。
   - client direct read 和至少一个 worker read path 都要实际复用。

3. DataTransport，差异：
   - ClientRemoteTcpDataTransport 调用远端 worker GetObjectRemote TCP。
   - WorkerLocalDataTransport 复用 worker 现有本地/gateway data 读取能力。
   - Transport 只负责怎么取数据，不处理 hash ring refresh、redirect、fallback、cutback。

必须修：
1. GetClusterStateRspPb 增加 hash ring 的可比较版本字段，worker 返回当前 ring 的 etcd mod revision 或等价权威版本，client LoadFromWorker 使用该版本；不要继续 version=-1。
2. ObjectClientImpl 的 directReadRpcAdapter/directReadRingSource/directReadRingWorkerApi 增加明确并发所有权。请求路径不能无锁重置正在被 DirectReadFlow 使用的对象。优先 mutex + shared_ptr。
3. direct data TCP 不要静默丢弃 subTimeoutMs。如果设计要求 RPC timeout=0，请显式设置 0 并注释说明收敛边界；否则使用本次 Get 的有效 timeout/deadline。
4. 批量 data phase 不要逐对象串行新建 channel/stub。至少按 data worker/address 分组并复用 transport；能并发则更好。
5. 移除 MetaFlow 上未使用的 data client 端口，不要保留 WorkerNullDataClient。Data 抽象放到真实 ReadDataFlow + DataTransport 中。

测试要求：
1. 本地无 worker：client 进入 direct read，直接访问远端 meta/data，读到正确数据。
2. 本地有 worker：不走 direct read，走 gateway/local worker。
3. 本地 worker 先挂再恢复：先 direct read，恢复后 cutback 到 gateway/local worker。
4. remote-only client：不要误 cutback。
5. distributed master：hash ring bootstrap、worker refresh、meta moving、redirect 都覆盖。
6. 扩缩容：scale down/up 后 direct read 仍正确，并验证 ring revision 单调更新，不被旧 worker snapshot 回滚。
7. 多 RPC 错误：meta timeout、redirect loop、stale route、data worker unavailable、data worker slow/hang、fallback 开启/关闭。
8. 批量 Get：多 key 顺序保持，not found 和 found 混合结果正确，多个对象落同一 data worker 时不应每个对象都新建 channel/stub 串行读。

自查：
1. common 层是否只包含真正共用语义？
2. client/worker 是否都有真实调用方复用 common flow？
3. 有没有空实现、未使用接口、只为编译通过的 adapter？
4. DirectReadFlow 是否还持有可能悬空的 raw pointer？
5. data RPC timeout 语义是否显式且有测试？
6. scale 测试是否能证明 ring 更新，而不是被 fallback 掩盖？
```

