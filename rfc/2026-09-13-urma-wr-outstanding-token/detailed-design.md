# URMA outstanding WR 控制与无完成恢复：详细设计

状态：2026-09-17，rebase 与本地回归完成，当前提交门禁进行中，尚未合入。旧版门禁结果不代表本次提交。
源码基线：upstream master `fcaace844`。
[实现 PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422)，本次基于最新主线 rebase，最终提交与验证结果见第 8 节。
[HTML 设计页](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html)。
本修订替代 2026-09-13 草案；实现提交 `4a580cf3d318c4d3127d0a2d7d8a75a8dc967c29`。

[RFC #1244](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1244) · [PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422) · [详细设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [关键概念与开发视图](https://yche.me/design/urma-wr-token-developer-view-20260917.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

## 1. 要解决的问题

请求超时不意味着设备 WR 已完成。现有 lane force-release 可以归还逻辑 lane，同时把其 pending WR
转成 orphan；同一个 jetty 随后还可承载新请求。因此 lane 数无法约束进程中尚未结清的 WR 数。
`tbbEventMap_.size()` 又包含业务保留记录，不能当作硬件 outstanding 数。

当前源码证据：`UrmaResource::TryForceReleaseTimedOutSendLane` 转移 orphan 并归还 lane；
`UrmaManager::RegisterRetainedTimeoutEvent` / `PruneRetainedTimeoutEvents` 驱逐、过期删除 Event；
`UrmaManager::CheckCompletionRecordStatus` 按 local_id 和 request ID 处理完成。
相关源码位于 `src/datasystem/common/rdma/urma_resource.cpp` 和 `urma_manager.cpp`。

目标是限制已预留或可能已提交的 WR 数，容量满时只阻塞配置的时间，并且在没有正常完成的故障下主动启动
jetty 恢复，防止额度永远被遗忘。read、write、多 chunk、gather 共用一套额度。

旧文档的 64×4MB/120GB/s 只是理想串行化量纲估算，不能证明真实延迟上界。
4KB 包粒度和链路带宽需以实际部署及设备证据验证；默认容量 100 是按检视意见调整的初值，仍需真实硬件压测。
最新主线 lane pool 默认已为 200，不能沿用历史样本中的 64 当作当前配置。

## 2. 需求与不变量

| 编号 | 合同 |
|---|---|
| N1 | 启用时，0 ≤ reserved + potentially accepted ≤ capacity；每个 WR 一个额度 |
| N2 | 每次准入最多等待 min(配置等待时间, API 剩余时间)；未提交窗口最多尝试 3 次，总体受 API 期限约束 |
| N3 | 同一额度只归还一次；迟到、重复完成不得释放新请求的额度 |
| N4 | Event 删除、TTL、驱逐、force-release、创建替代 jetty 都不是硬件结清证据 |
| N5 | 正常/错误 CQE 可结清对应 WR；确认 flush 且 provider delete 成功可结清旧 jetty 剩余 WR |
| N6 | 恢复触发不依赖成功 CQE，也不依赖后续请求；关闭 post 准入后才能操作旧 jetty |
| N7 | 等待队列按 FIFO 准入，每个 waiter 单独 notify_one；取消队首后继续唤醒下一位 |
| N8 | 每次准入记录 wr_token_wait_us；立即成功为 0；禁止用 Event 数替代额度水位 |

**无法无条件同时保证的边界：** 若 provider 一直不返回任何可用 flush/停止证据，且没有经验证的设备 reset
接口，则无法同时证明“旧 WR 已停止”及“安全归还其物理额度”。这时保留账本和相关 provider 资源、隔离旧
jetty，使用受限补池及 TCP fallback；不能靠超时清零制造空闲额度。真实设备强制 reset 的契约需另行验证。
“没有成功 poll”在本方案中的可恢复路径包括没有成功数据 CQE，但仍能收到错误/flush 控制记录。

## 3. 外部 Use Cases

| ID | 用户/故障场景 | 预期结果 |
|---|---|---|
| UC01 | 正常小对象读写 | 无排队获得额度，原数据语义不变 |
| UC02 | 多客户端短时拥塞，期间有请求完成 | FIFO 有界等待后继续，不超配 |
| UC03 | 满容量且单次等待预算耗尽 | 仅明确未提交窗口做最多 3 次准入，100/200us 退避；耗尽后 K_URMA_TRY_AGAIN，fallback 仍可能被配置或限额拒绝 |
| UC04 | API 已过期或先于短等期限结束 | K_RPC_DEADLINE_EXCEEDED，不继续新提交 |
| UC05 | 单对象拆多个 chunk | 每个真实 WR 单独计数，超过容量仍可分批推进 |
| UC06 | gather 链长超过容量 | 分成有界提交窗口，不等自身尚未提交的预约额度 |
| UC07 | 中途注册失败、post 拒绝或部分接受 | 已知未接受部分归还；已接受和不确定部分保持记账 |
| UC08 | 请求超时返回后才收到完成 | 正确释放旧 WR；不能影响已复用 lane 的新请求 |
| UC09 | 保留 Event 被驱逐或 TTL 到期 | 硬件额度仍在，等待者不能因业务记录删除而越过硬顶 |
| UC10 | 某 jetty 不再完成，系统无新流量 | 后台检查启动 retire/refill；有效 flush + delete 后恢复额度 |
| UC11 | 错误完成、重复完成、旧 request ID | 对应 WR 至多结清一次；旧身份不影响替代 jetty |
| UC12 | provider modify/delete 失败或 flush 永不到达 | 隔离并保留额度；仅确认 flush 后的删除失败允许后台重试，删除成功才归还剩余额度 |
| UC13 | 服务关闭时仍有排队和在途 WR | 排队者退出，等待已获 post permit 的调用，保持 provider 生命周期安全 |
| UC14 | 配置关闭或非法参数 | capacity=0 旁路 token 预约、指标、CQE 账本查询和扫描，保留 post gate/provider；非法参数启动失败 |
| UC15 | 大对象无法 TCP fallback，首次准入拥塞后缓解 | 只重试尚未提交窗口，已提交前缀不重放，数据完整返回 |
| UC16 | 持续耗尽或 API 已过期 | 最多 3 次尝试；期限耗尽不继续 post，旧额度仍可恰好结清一次 |
| UC17 | 多段请求提交前发生内存分配失败 | 不触发 provider；撤销所有未提交预留，释放缓冲区，保留健康连接可用性 |
| UC18 | 一个请求恰好拆成两 WR，完成乱序或重复 | 每项各结清一次，共享内存保持到最后一项；容量 1 也能按窗口完成 |
| UC19 | 持续额度耗尽与真正 provider 故障分别发生 | 拥塞返回原等待错误和限频诊断；真实 provider 故障仍保留诊断；Event/lane 清理不遗漏 |
| UC20 | 开启 client/worker 慢请求 summary，单请求多 WR 与重试 | 汇总等待 us 并传递到调用方，不因每 WR 打 tick 溢出，不重复扣除重叠总耗时；关闭 tracing 时旁路 |

## 4. 组件与调用顺序

```text
WR 已构造 / Event 已注册
    -> 按提交窗口申请额度（此时不持有 PostPermit）
    -> 获得 jetty PostPermit
    -> 将每个额度移入该 jetty 的 request-ID 账本
    -> provider post
        -> 成功：账本继续持有
        -> 已知拒绝：撤回未接受后缀
        -> bad_wr 缺失或不属于链：全部按可能接受保留
普通 CQE -> 查实际 jetty + request ID -> 账本结清 -> 定向唤醒
后台过期扫描 -> retire -> drain posts -> ERROR -> FLUSH_ERR_DONE
    -> provider delete 成功 -> 剩余账本结清
```

`WrTokenBucket` 是进程资源层的容量状态；`WrToken` 可移动、不可复制，支持把一次批量预约拆成单 WR。
`WrTokenLedger` 属于物理 jetty 实例，业务 Event 不拥有已提交额度。与旧草案“Event 析构即归还”相比，
这避免了 TTL/驱逐的提前释放，也能在业务等待者调度迟缓时及时处理硬件完成。

gather 先构造完整描述符但不预占额度，再逐窗口申请并提交。每个窗口大小不大于 capacity；已知未接受
后缀与根本未开始的窗口都执行原有 Event/lane 清理。未知接受范围不能按全部拒绝处理。

### 4.1 等待与错误

使用 steady_clock 计算绝对截止，假唤醒不能重置预算。直接采用 min(wait_us, API remaining)，不引入原稿
未经验证的固定 3000us reserve / 1500us 分叉，避免小 API 预算时参数看似有效却不等待。

容量满且 wait_us=0 也返回统一的 wr_token_wait_timeout，消除旧稿 exhausted 与 timeout 文案冲突。
错误中包含 waited_us、outstanding、capacity；关闭与 API 到期具有可辨认原因。
正常路径通过指标/trace 记录等待时间，避免无条件热路径日志。

`PostSendWrWithAdmissionRetry` 只根据本地 `admissionRejected` 标记重试零提交窗口；provider 错误、部分提交和未知接受范围不触发此重试。默认最多等待 3×5000us 加 100/200us 退避（调度延迟另计），且每次均检查 API 剩余预算。wait_us=0 关闭单次等待，但不关闭这两次有界重试。

### 4.2 恢复与生命周期

复用 `UrmaJetty` 的 ACTIVE -> QUIESCING -> MODIFYING -> WAIT_FLUSH -> DELETE_READY -> DELETING -> DESTROYED。
失败进入 QUARANTINED。复用 `RetireJetty`、post gate 和 `ScheduleDeleteJetty`，不另建与它们竞争的删除器。

在既有 refill 控制线程的 50ms 周期检查最老未结清 WR。默认 recovery_timeout_ms=5000，作为独立启动参数；
因为这是在途恢复时间，不应复用几百微秒的准入等待参数。检查时间达到阈值时关闭旧 jetty，不等待新的成功 CQE。
新 jetty 可先按原有 live/refill 上限创建，但创建本身不释放旧 WR token。

确认 flush 后的 provider delete 失败会设置独立的可重试标记。后台按恢复阈值的间隔，将该类 QUARANTINED 状态重新送入原删除执行器；post gate 始终关闭，成功后移除隔离引用并结清账本。modify 失败或没有 flush 的隔离对象不具备此资格。删除持续失败时额度保持可追踪，不能将其视为已释放。

### 4.3 并发与内存安全

- bucket 和 FIFO 队列使用 bthread mutex，waiter 使用各自条件变量；普通释放只通知可满足的队首。
- token 持有共享的 bucket 状态，句柄可以晚于 bucket facade 析构；Close 唤醒排队者。
- jetty 账本单独互斥；在锁内摘除条目，在锁外销毁 token，避免嵌套持锁唤醒。
- 申请额度时不得持有 provider PostPermit；否则拥塞等待可能挡住 retire 的 drain。
- 发布账本早于 provider post；CQE 可先于 post 返回，不能用返回后的 Commit 重新加入已结清记录。
- 每个 WR 账本还持有独立的 localBufferOwner：worker ShmUnit、client BufferHandle 或 gather 实际源
  ShmUnit 集合。已知未提交、对应 CQE 或安全删除时在锁外释放 owner；Event 删除不释放它。
- 本地 owner 只防止内存析构/归还，不保证调用者不会改写同一内存，也不能单方面保护对端内存；
  对端超时/复用仍需沿传输协议验证，不能由 token 守恒推导。
- 仅 provider 删除成功后批量结清；ERROR 设置成功本身仍不等于 flush 完成。

### 4.4 性能、风险与回滚

不增加数据复制或 IO；每 WR 增加一次准入操作及账本更新。立即成功不分配 FIFO waiter 节点；
拥塞时使用有界期限，后台扫描范围受 live jetty 和 token 容量限制。需测锁争用、分配开销与吞吐影响。

这是共享传输层的高风险变更，需要 URMA 生命周期及并发领域维护者评审。无持久化格式变更。
启动 capacity=0 可回退到既有 lane 路径；不允许热缩容。上线先小范围，观测 token 水位、等待分布、fallback、
retire/flush/delete 失败；真实硬件压测结果决定最终默认值。

## 5. 组件接口与配置

业务 SDK 没有新增公开方法。下列是额度组件面向传输调用方的接口，签名与当前头文件一致；本节只列本特性新增/变更的配置。

```cpp
// WrTokenBucket：成功时 token 持有 count 份预约；失败不取得额度，waitedUs 返回实际排队耗时。
Status Acquire(uint32_t count, uint64_t waitUs, std::chrono::steady_clock::time_point deadline,
               WrToken &token, uint64_t &waitedUs);
// WrTokenLedger：转入一个 WR 的 token 与 owner，Complete 仅在首次匹配时返回 true。
Status Track(uint64_t requestId, WrToken token, std::chrono::steady_clock::time_point now,
             std::shared_ptr<void> localBufferOwner = {});
bool Complete(uint64_t requestId);
```



| 参数 | 默认 | 语义 |
|---|---|---|
| urma_wr_token_capacity | 100 | 进程级 WR 额度，范围 [0, 65536]，0 关闭，启动生效 |
| urma_wr_token_wait_us | 5000 | 单次准入等待预算，范围 [0, 60000]us，0 不等，启动生效 |
| urma_wr_token_recovery_timeout_ms | 5000 | 范围 [1, 60000]ms，WR 未结清触发退役的年龄阈值，启动生效；不等于保证完成时间 |

Worker flags、部署 JSON 和 Helm 使用同名参数及范围。客户端首次 URMA 初始化前支持
`DATASYSTEM_URMA_WR_TOKEN_CAPACITY`、`DATASYSTEM_URMA_WR_TOKEN_WAIT_US`、
`DATASYSTEM_URMA_WR_TOKEN_RECOVERY_TIMEOUT_MS`；未设置保留现值，非法值初始化失败，Worker 忽略这些客户端环境变量。

流水线 H2D 内部还有独立 provider 提交，不具备逐 WR 记账接口。本次实现明确拒绝与启用 token 的组合，
并拒绝带 pipeline 标记的混配请求，防止绕过硬顶；不能宣称此类错误自动 TCP fallback。
启动配置、pipeline 初始化入口和带 pipeline 标记的请求均已实施拒绝，故障测试覆盖该边界。
旧 pipeline 运行模式可选择 capacity=0；关闭模式同时关闭本次账本的 buffer owner 保活，恢复原有传输行为。

## 6. 修改边界

`src/datasystem/common/rdma/urma_wr_token.*`：额度和账本；
`urma_resource.*`：资源持有、提交封装、恢复扫描、删除后结清；
`urma_manager.cpp`：两类提交入口与完成分派；flags/config/metrics：参数与可观测性；
CMake/BUILD.bazel、URMA UT/ST 和 `.repo_context` 同步。
workbench 仅更新 RFC，不增加或依赖 workbench 脚本。

## 7. 验证矩阵

| 维度 | 必须验证 |
|---|---|
| 容量 | 并发硬顶、0 关闭、批量拆分、超大预约不消耗 |
| 排队 | FIFO、队首取消、假唤醒、API 到期、关闭唤醒、bthread 协作调度 |
| 所有权 | move/reset、失败早退、重复 request ID、重复 CQE、旧 jetty 身份 |
| 提交 | READ/WRITE、多 chunk、gather 超容量、bad_wr 首/中/尾/空/链外 |
| 恢复 | 无成功 CQE、无新流量、flush 在 modify 内到达、provider post 尚未返回 |
| 失败保护 | modify/delete 失败、永久无 flush、不提前释放、资源数不无限增长 |
| 业务集成 | TCP fallback、超时状态不变、数据正确、buffer 不提前重用 |
| 构建 | 目标环境 CMake + ccache，mock 与非 URMA 构建，Bazel 元数据一致 |
| 性能 | 默认配置与基线比较吞吐、尾延迟、锁争用；不得把 mock 吞吐当真实 UB 结果 |

## 8. 当前实现与验证

| 验证范围 | 通过 / 总数 | 证据文件 |
|---|---:|---|
| WR token | 17/17 | `final-summary-token.xml` |
| 故障 / 客户端配置 | 42/42 | `final-summary-fault.xml` |
| Jetty gate | 10/10 | `final-summary-gate.xml` |
| 默认值 / 参数边界 | 3/3 | `final-summary-flags.xml` |
| 跨 Worker 系统 | 4/4 | `final-summary-st.xml` |
| tracing / metrics / topology | 201/201 | `final-summary-common-green.xml` |
| 非 URMA token | 17/17 | `final-summary-no-urma.xml` |

实现提交 `4a580cf3d318c4d3127d0a2d7d8a75a8dc967c29`，相关 CMake 回归通过，当前提交门禁进行中。完整红绿对照、两 WR 请求级证据、微基准与设备边界见 [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)。

账本前缀回滚使用仅含引用/指针的栈上守卫；第 N 项登记失败不会遗留前 N-1 个未提交项。READ/WRITE 清理后返回前置原错误，真正 provider 错误才进入 provider 诊断。

`urma.wr_token_wait` 追加 phase ID 31，复用现有 phase 协议。每个 Trace 按一个槽累计本地与下游等待，旧端忽略未知 ID；不加入派生阶段扣减集合。`wr_token_wait_failed_total`（ID 147）统计失败尝试，日志限频。更多概念和开发位置见 [开发视图](developer-view.md)。


### Use Case 到最终测试的对应

| Use Case | 已通过的代表测试 |
|---|---|
| UC01 | `MultiChunkRemoteGetUsesWrAdmission`、`GatherLargerThanCapacityMakesProgress` |
| UC02 | `FifoPreventsSmallRequestBypassingHead`、`ReleaseWakesWaitingCaller` |
| UC03 | `WaitBudgetExpiresWithoutLeaking`、`AdmissionRetryIsBoundedByAttemptsAndDeadline` |
| UC04 | `DeadlineWinsEvenWhenCapacityIsFree`、`ApiDeadlineBoundsLongerConfiguredWait` |
| UC05 | `MultiChunkRemoteGetUsesWrAdmission` |
| UC06 | `GatherLargerThanCapacityMakesProgress` |
| UC07 | `PartialPostReturnsOnlyUnacceptedSuffixCredits`、`MissingBadWrPreservesAllPotentiallyAcceptedCredits` |
| UC08 | `CompletionReturnsCreditOnceAfterEventDeletion` |
| UC09 | `PostAdmissionCountsWrRatherThanEvents`、`CompletionReturnsCreditOnceAfterEventDeletion` |
| UC10 | `StalledWrRetiresWithoutSuccessfulCompletion`、`BackgroundRecoveryReturnsCreditsWithoutNewTraffic` |
| UC11 | `SplitReservationAndSettleExactRequestIdentity`、`CompletionReturnsCreditOnceAfterEventDeletion` |
| UC12 | `FailedDeleteRetriesWithoutLosingCredits`、`MissingFlushAndQuarantineRetainCreditsAndBuffer` |
| UC13 | `ShutdownWakesBlockedCallerWithoutReturningHeldCredit`、`ClearWithPendingJettyDefersCompleteProviderDependencyClosure` |
| UC14 | `DisabledTokensBypassClosedBucket`、`UrmaWrTokenConfigurationBoundaries`、`UrmaWrTokenProductionDefaults` |
| UC15 | `AdmissionRetryResumesOnlyUnsubmittedWindow`、`RemoteGetRetriesAdmissionWithoutTcpFallback` |
| UC16 | `AdmissionRetryIsBoundedByAttemptsAndDeadline` |
| UC17 | `LedgerAllocationFailureReturnsUnsubmittedPrefix` |
| UC18 | `TwoWrSharedBufferLivesUntilBothCompletions`、`TwoWrWaitAppearsInClientAndWorkerSummary` |
| UC19 | `ReadAndWriteAdmissionFailuresPreserveStatusAndCleanup` |
| UC20 | `RepeatedAdmissionFailuresRemainInRequestSummary`、`WrWaitAggregatesAcrossWrAndDownstreamWithoutDoubleSubtracting`、`TwoWrWaitAppearsInClientAndWorkerSummary` |

## 9. 修订与限制

此前已完成最新主线 rebase、默认值扩大、仅未提交窗口的有界重试、关闭快路径和确认 flush 后删除失败重试。此次补齐异常回滚、等待可观测性及两 WR 取证；两个检视讨论已回复并确认解决。

验证中修正了测试枚举清单、后台探测计数混入及进程日志路径，不据此扩大生产修复结论。源码指纹核对、CMake 编译缓存和非 URMA 独立构建均有证据。真实 UB 的安全停止/reset 与生产性能仍未验证。
