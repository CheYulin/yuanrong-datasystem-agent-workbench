# URMA outstanding WR 控制与无完成恢复：详细设计

状态：2026-09-19。当前特性分支验证 HEAD 为 `8789f20c1cef66c48ab9feea9521845977e064d6`，远端 fork 分支已同步；PR #2422 当前 HEAD 已完成门禁触发器 11450：CodeCheck、license、SCA、x86_64、aarch64、openyuanrong 全部 SUCCESS。tiantiyun-80c128g 使用 CMake Release + URMA mock + ccache 的定向回归为 token 17/17、Jetty gate 11/11、fault 48 passed + 1 skipped（真实 provider 用例）、ST 定向筛选 6/6。真实 provider 行为仍不由 mock 回归覆盖。

[实现 PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422) · [HTML 设计页](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html)。保留旧提交历史，以新增提交交付修复。报告未提供部署 SHA，源码缺陷与现场 trace 根因分别陈述。

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
| N2 | 一次逻辑传输的全部WR共用一次准入预算及父API绝对截止时间；最多3次尝试，退避也扣除配置总预算，零等待只尝试一次；协作式检查不是硬实时返回保证 |
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

## 2.1 0917 运行报告后的修订验收（2026-09-18，实施中）

本节是新增提交的验收要求，不能用上一轮门禁通过证明已完成。变更限于 WR 准入、账本回收及必要观测，保留原提交历史。

- 完成及确认静默后的额度归还必须先于 buffer owner 析构；退出 ledger 锁后归还，批量静默回收先归还全部 token 再清理 owner。未确认设备停止时禁止回收。普通完成和静默回收测试须在 owner deleter 内验证可立即重新申请全部已归还额度。
- 已定位准入后至 provider 调用前的超期窗口：提交前必须重新检查 API deadline；超期时回滚尚未调用 provider 的整条链，提交数为零。故障注入测试先复现超期仍提交，再验证拒绝和额度归零。这只能阻止已知超期的新提交，不构成抢占或硬实时返回保证。
- chip 计数随 UrmaEvent 构造递增、析构递减，仅覆盖携带有效源 chip counter 的事件；Event 表随插入/删除变化，外部引用可延迟析构。因此 chip 合计不要求等于 Event 表大小，更不等于 token outstanding。不能拿两者验证 QD 硬上限。
- 一个请求全部 WR 同时满足：本轮实现以一次逻辑 URMA 读、写或 gather 为预约边界，在第一个 provider 调用前一次拿齐该次传输全部 WR 的额度；超容量直接拒绝，不再逐 chunk／窗口准入。跨节点 GET/SET 或 BatchGet 多对象调用之间的原子预约不在这个进程级边界内，仍需单独确认，不能宣称已覆盖整个分布式 API。当前已完成软件回归并推送，最新6项远程门禁已全部通过。
- QD 验收针对 bucket reserved + potentially accepted；Event 表及 chip 业务计数单独说明。需要可关联请求的 requested、capacity、outstanding、准入总耗时、结果和归还原因，避免逐 WR 高成本日志。
- 准入日志新增真实 token 快照：`capacity`、`outstanding_snapshot`、`peak`、`waiting_snapshot`。`peak` 是同一 bucket 初始化以来的最大预约额度，在既有桶锁内更新，不能用采样瞬时值代替峰值。快照在日志实际输出时取得，不与此前准入事件原子绑定。
- `wait_us` 保持现有等待分支口径（实际条件等待才为正，从Acquire加锁前计时，含锁竞争）；`acquire_us` 覆盖整个 Acquire 调用（含桶锁等待），不包含后续账本登记/provider/CQE。`request_id`、`requested`、`wait_budget_us`、`api_remaining_at_entry_us` 与结果共同解释准入边界。失败日志继续每秒限频；成功按既有慢阶段阈值或 VLOG 输出。关闭 token 时旁路；格式化及快照锁只在日志输出时执行。新增一次结束时钟读取，正常准入在已有锁内增加峰值比较，无额外堆分配。
- 同一次逻辑传输预约的最多三次尝试共用配置等待预算，重试与退避扣减剩余量；零预算只尝试一次立即准入，等满预算后不重开一个新窗口。瞬时拒绝仍可在剩余预算内重试；已提交前缀不可重放。chunk 循环只拆分已预约额度，不再次等待；未提交的剩余预约由 RAII 归还，已接受或不确定 WR 继续由原账本保有。
- 整次预约的成本与行为变化：一个含N个WR的普通读写从N次准入变为1次，但仍有N次账本登记和完成结清。新增预约来源/数量校验仅比较所有权状态与数量，不增加锁。全额预约会在chunk构造和提交期间暂占未提交额度；FIFO队首的大请求也可能延后后面的小请求。这是“全部WR同时满足”的代价，不能仅因准入调用减少就宣称吞吐改善。超容量请求由可分窗完成改为拒绝，部署容量必须覆盖允许的一次传输最大WR数。
- 并行BatchGet验收：派发前固定父API绝对截止时间，TBB任务建立独立请求上下文并扣除排队耗时；过期父请求不能在外部线程恢复为默认60秒。主调用线程参与任务时也不能污染父上下文。先用真实TBB调度与真实WrTokenBucket验证外部线程过期拒绝，再修复入口；不重构RequestContext；ApiDeadline补充GetDeadline/InitAt绝对截止时间快照与初始化；不能以两次时间读取重建父deadline，既避免相对初始化延长预算，也避免捕获期间的调度停顿被重复扣除。子任务独立收集downstream阶段，join后汇回父Trace，父阶段不复制计入子任务。并行任务等待之和是累计工作时间，可能超过请求墙钟时间，不可拿它当关键路径或与总耗时相加。新增一次派发快照、每个TBB range一个局部上下文及trace状态复制；仅开启trace时分配 `parallelSize * sizeof(DownstreamPhaseResult)` 的结果数组并在join后汇总，实际CPU成本仍需测量。此deadline上下文修复在capacity=0时也保留，关闭token只旁路token专属同步与账本工作。
- 并行执行安全验收：TBB不得直接在可迁移的RPC bthread中执行；令牌条件等待、账本锁竞争和重试退避均可能让出bthread。改用现有TBB arena enqueue到其pthread，调用者通过bthread CountdownEvent等待，全部任务退出后才释放请求引用；packaged_task保留异常并在join后向调用方传播。arena不保留调用线程槽位，parallel_nums=1也必须可完成。新增一次任务入队、future状态分配和完成通知，没有新增线程池；这不是零成本修复。修订前64路BatchGet已复现TBB调度器崩溃；定向测试还观察到128次回调中96次执行在bthread，作为红灯证据。最终绝对deadline版本在指定验证机CMake+ccache下通过6个定向单测及6个URMA mock集成用例，包含64路×64对象并发、parallel_nums=1和任务异常传播；这不是物理设备或生产pmax验证。
- pmax 需逐条核对超过 20ms 的特性相关样本：重复准入预算、登记/桶锁、CQE处理及owner清理、通知后恢复、后台回收。必须区分到期停止新提交、调用返回时间、设备真正停止时间；仅修改状态码或统计口径不能视为时延达标。
- 异常回收审视新增待修复项：modify短暂失败后，现有quarantine只会重试已确认flush的delete，无法自行恢复modify；关闭流程只排空post就停止poll，也可能错过正常provider随后送达的flush。必须分别补充“无新流量、modify首次失败后恢复”和“shutdown期间延迟modify/flush”的红绿测试。修复要复用现有后台执行器并保证post gate持续关闭，不扩大线程池；关闭等待须有界且保持poll运行。永久无flush、无CQE且无可验证reset时，额度只能安全保留，明确服务失效/进程终止边界，不能宣称已经解决所有异常归还。
- TDD 覆盖部分提交、缺失 bad_wr、迟到/重复 CQE、无成功 CQE、flush/delete 失败、业务记录清理、关闭和异常分配。证明总额度守恒与不超配，不能通过超时强制清零使测试通过。
- 每项新语义先有失败测试，再做最小修复；用 CMake 和编译缓存运行相关回归，最终新增 commits 推送 fork 后重新通过 PR 门禁。性能收益和现场 pmax 仍需对应硬件/部署样本证明。

## 3. 外部 Use Cases

| ID | 用户/故障场景 | 预期结果 |
|---|---|---|
| UC01 | 正常小对象读写 | 无排队获得额度，原数据语义不变 |
| UC02 | 多客户端短时拥塞，期间有请求完成 | FIFO 有界等待后继续，不超配 |
| UC03 | 满容量且预约总预算耗尽 | 最多3次尝试共用一次配置预算及API deadline；100/200us退避也扣除预算，零等待只立即尝试一次；耗尽后 K_URMA_TRY_AGAIN |
| UC04 | API 已过期或先于短等期限结束 | K_RPC_DEADLINE_EXCEEDED，不继续新提交 |
| UC05 | 单对象拆多个 chunk | 循环前一次预约全部真实 WR；额度不足时第一个 WR 也不提交，需求超过容量直接拒绝 |
| UC06 | gather 链长超过容量 | 整链拒绝，零 provider 提交；容量足够时整链预约后按原部分提交规则结清 |
| UC07 | 中途注册失败、post 拒绝或部分接受 | 已知未接受部分归还；已接受和不确定部分保持记账 |
| UC08 | 请求超时返回后才收到完成 | 正确释放旧 WR；不能影响已复用 lane 的新请求 |
| UC09 | 保留 Event 被驱逐或 TTL 到期 | 硬件额度仍在，等待者不能因业务记录删除而越过硬顶 |
| UC10 | 某 jetty 不再完成，系统无新流量 | 后台检查启动 retire/refill；有效 flush + delete 后恢复额度 |
| UC11 | 错误完成、重复完成、旧 request ID | 对应 WR 至多结清一次；旧身份不影响替代 jetty |
| UC12 | provider modify/delete 失败或 flush 永不到达 | 隔离保留额度；modify暂时失败按恢复间隔重试，隔离时收到的flush跨重试保留；仍需成功modify和flush后成功delete才归还。永无停止证据保持隔离，不用超时强制清零 |
| UC13 | 服务关闭时仍有排队和在途 WR | 排队者退出，等待已获 post permit 的调用，保持 provider 生命周期安全 |
| UC14 | 配置关闭或非法参数 | capacity=0 旁路 token 预约、指标、CQE 账本查询和扫描，保留 post gate/provider；非法参数启动失败 |
| UC15 | 大对象无法 TCP fallback，首次准入拥塞后缓解 | 只在总预约预算内重试未提交的整次预约，已提交前缀不重放，数据完整返回 |
| UC16 | 持续耗尽或 API 已过期 | 最多 3 次尝试；期限耗尽不继续 post，旧额度仍可恰好结清一次 |
| UC17 | 多段请求提交前发生内存分配失败 | 不触发 provider；撤销所有未提交预留，释放缓冲区，保留健康连接可用性 |
| UC18 | 一个请求恰好拆成两 WR，完成乱序或重复 | 先同时预约2个额度，每项各结清一次，共享内存保持到最后一项；容量1拒绝，容量2且已占1时不得提前提交 |
| UC19 | 持续额度耗尽与真正 provider 故障分别发生 | 拥塞返回原等待错误和限频诊断；真实 provider 故障仍保留诊断；Event/lane 清理不遗漏 |
| UC20 | 开启 client/worker 慢请求 summary，单请求多 WR 与重试 | 汇总等待 us 并传递到调用方，不因每 WR 打 tick 溢出，不重复扣除重叠总耗时；关闭 tracing 时旁路 |

## 4. 组件与调用顺序

```text
统计一次逻辑传输的全部 WR 数（gather 已构造整链）
    -> 一次预约全部额度（此时不持有 PostPermit）
    -> 读写逐 chunk 创建 Event 并拆分预约，gather 保持整链
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

gather 先构造完整描述符及Event，再一次预约整链额度。整链超过capacity直接拒绝，不再分窗口流式提交；普通read/write在切片循环前预约全部额度。已知未接受后缀与未提交的预约分别回收；未知接受范围仍保留账本。gather的准入前Event也说明Event数不能作为token实际占用数。

### 4.1 等待与错误

使用 steady_clock 计算绝对截止，假唤醒不能重置预算。直接采用 min(wait_us, API remaining)，不引入原稿
未经验证的固定 3000us reserve / 1500us 分叉，避免小 API 预算时参数看似有效却不等待。

容量满且 wait_us=0 也返回统一的 wr_token_wait_timeout，消除旧稿 exhausted 与 timeout 文案冲突。
错误中包含 waited_us、outstanding、capacity；关闭与 API 到期具有可辨认原因。
正常路径通过指标/trace 记录等待时间，避免无条件热路径日志。

`ReserveWrTokens` 为整次逻辑传输固定一个配置预算终点，最多3次零提交准入尝试共用默认5000us；100/200us退避也扣除剩余预算，wait_us=0只立即尝试一次。provider错误、部分提交和未知接受范围不触发重试。每次申请受父API绝对截止时间约束，紧邻provider调用前再次检查；这不能抢占正在阻塞的provider或OS调度。

### 4.2 恢复与生命周期

复用 `UrmaJetty` 的 ACTIVE -> QUIESCING -> MODIFYING -> WAIT_FLUSH -> DELETE_READY -> DELETING -> DESTROYED。
失败进入 QUARANTINED。复用 `RetireJetty`、post gate 和 `ScheduleDeleteJetty`，不另建与它们竞争的删除器。

在既有 refill 控制线程的 50ms 周期检查最老未结清 WR。默认 recovery_timeout_ms=5000，作为独立启动参数；
因为这是在途恢复时间，不应复用几百微秒的准入等待参数。检查时间达到阈值时关闭旧 jetty，不等待新的成功 CQE。
新 jetty 可先按原有 live/refill 上限创建，但创建本身不释放旧 WR token。

确认 flush 后的 provider delete 失败会设置独立的可重试标记。后台按恢复阈值的间隔，将该类 QUARANTINED 状态重新送入原删除执行器；post gate 始终关闭，成功后移除隔离引用并结清账本。modify失败采用独立标记，按同一恢复间隔转回QUIESCING并复用原modify执行器；后续成功modify清除此标记。隔离期间收到的flush也锁存，避免重试成功后依赖第二次通知。缺少flush时仍不得删除；永久modify失败或永无flush仍需设备停止/reset证据或明确进程终止边界。删除持续失败时额度保持可追踪，不能将其视为已释放。该修订经CMake+ccache通过45个故障用例和11个状态机用例（URMA mock，含正常进程退出）；关闭lifecycle drain补充验证如下，不能据此宣称真实设备所有异常归还已完成。
#### 关闭排空补充契约（09/18，软件验证通过，已推送）

Stop先关闭准入并等待现有post许可退出，随后保留CQE poll、modify/delete执行器和恢复重试，在recovery_timeout_ms预算内等待全部注册Jetty完成删除。不能只等pending集合为空：并发退休可能刚关闭gate、尚未插入pending。成功条件同时涵盖注册表与pending/quarantine，包含收发Jetty。正常删除完成才唤醒等待；正常调用热路径不增加锁。

关闭期间禁止新Jetty注册/发布，但原有50ms后台循环继续重试失败modify/delete，不受正常恢复间隔阻挡；Clear最终停止后台线程。缺flush或永久provider失败时Stop返回明确超时，Clear保留未证明静默的Jetty、owner和provider依赖，禁止强行清零额度。该预算不包含可能卡住的provider post/调用或最终线程join，不是整个进程关闭的硬实时上限。重复Stop在poll已停止时不得重新等待不可完成的排空。

验收以独立进程覆盖延迟flush、缺失flush、首次delete失败；旧实现三例均失败。另复现超时后Clear持执行器锁join的死锁（已锁存flush、modify延迟）：8秒watchdog终止；改为锁内移出执行器、锁外join后通过。最终CMake+ccache通过49个故障用例及11个状态机用例，整进程退出码0，正常清理JFC/JFCE/context，无此前残留警告；4个变更C++文件远端与本地SHA相同。mock只证明生命周期和所有权顺序，不证明实际硬件停止DMA。


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

### 4.4 性能开销、耗时口径与发布边界

以下按本地修订稿说明；独立微基准仍是历史 `644dca738` 测量，不代表本轮性能。容量控制会增加 CPU、锁竞争和内存驻留开销；当前证据不足以宣称“无性能影响”。这里区分源码可确定的成本、已测软件微基准和尚未测量的真实链路影响。

#### 提交、完成与后台成本

| 路径 | 增加的工作 | 成本与边界 |
|---|---|---|
| 无排队准入 | 时间读取、进程共享 bucket 的 bthread mutex、计数及 shared_ptr 引用操作；每次尝试记录直方图 | 一次逻辑传输一次整量预约，可减少多chunk的准入次数；未提交部分会暂占额度。没有等待队列节点分配，但不是无锁路径 |
| 逐 WR 登记 | 每 Jetty ledger mutex、requestId 查找/插入、unordered_map 节点分配、posted 时间戳和 owner 引用 | 按实际 WR 数发生；坏链校验及登记为提交链长度的线性遍历。栈上回滚守卫本身不分配 |
| 额度拥塞 | FIFO list 节点分配、条件变量挂起/唤醒、重新获取锁、到期检查 | bthread 等待而非忙轮询；调度和锁竞争可能使实际返回晚于配置预算。大的整量预约位于队首时，后续小预约也需等待 |
| 匹配 CQE | Jetty registry mutex/查找/weak_ptr 提升，ledger mutex/查找/摘除；退出ledger锁后先获取bucket锁归还token并通知队首，再释放owner | 这是轮询完成处理的新增成本，会影响通知前处理时间；不是只有提交端多一次计数。最后一个 owner 的析构/内存回收也可能在此发生 |
| 后台恢复 | 复用现有 RefillLoop，通常每50ms醒来，通知可更早唤醒；收集有效 Jetty 引用并逐账本检查过期 | 不新建线程，但增加扫描、临时vector分配和锁竞争；一次扫描上界按 O(J+W) 理解，J为注册Jetty数、W为检查到的账本条目数，不是每轮固定O(1) |
| 恢复重试 | 暂时modify失败，或已确认flush后删除失败的隔离项，按 recovery_timeout_ms 间隔重新调度原执行器 | 默认5000ms为检查/重试阈值，实际恢复还取决于调度、post drain、flush与delete，不能解释为5秒内必定归还 |

不增加有效载荷复制，但会延长 buffer owner 的持有时间。账本条目/额度受capacity约束；buffer字节数并不因此有同样的上限，Gather共享集合还可能持有整个批次的源内存。需要观察驻留内存与释放延迟。

capacity=0 会跳过新增的 token Acquire、等待直方图/summary、逐WR登记、CQE账本查找和恢复扫描。它不等于与基线机器指令完全一致：统一post封装/链校验、owner参数转递、Gather源owner集合构造，以及Jetty账本对象初始化仍可能存在。关闭tracing只跳过summary归集，不关闭已启用额度路径的指标和账本。

#### 耗时与日志的变化点

| 观测项 | 当前精确口径 | 不包含或不能推断的内容 |
|---|---|---|
| `wr_token_wait_us`，histogram ID146，us | 每次启用额度的准入尝试记录一次，包括失败与0；只有实际走过条件变量等待才记正耗时，采样从Acquire加锁前start到等待分支退出 | 立即准入记0，即使获取mutex/计数有成本；不等于整个Acquire耗时。排队样本可包含初始锁竞争和唤醒重新获锁时间 |
| `urma.wr_token_wait`，phase31，us | 复用上述waitedUs，同一Trace内多个逻辑传输和失败尝试求和，再通过既有phase协议合并下游，进入client/worker summary | 不含100/200us重试退避、账本登记、provider调用、CQE处理和后台恢复。并行分支的累计量不保证等于请求关键路径时长 |
| `wr_token_wait_failed_total`，counter ID147 | 每次Acquire返回错误递增，包括预算耗尽、deadline或关闭等错误 | 不是最终失败请求数；一次请求多次尝试可多次计数。不要与普通完成失败混用 |
| `[URMA_WR_TOKEN_WAIT_FAILED]` | 同一调用点每秒限频输出waited_us、outstanding、requested和原status | 日志条数不能代替失败计数；限频减少输出，但失败状态构造和计数仍有成本 |
| 普通write的 `[UrmaWrite] elapsed` / `postUs` | 原子预约改到chunk循环之前，逐chunk的Timer及postUs在预约成功后设置；现在不包含整量准入等待，仍含登记、NUMA设置、provider及调度 | 旧644逐chunk准入包含在此窗口中；修订后该值下降不证明DMA变快。`postUs`仍不是实际进入provider的时刻 |
| 普通write的 `urma_write_latency` | 每chunk的ScopedTimer位于整量预约之后，不包含循环前准入；包含该chunk的post及其作用域内工作 | 必须与旧版计时边界区别，不能用它替代整请求流控成本 |
| `Urma total write.` / `Urma gather write.` / 上层transport窗口 | 普通write总Timer在预约前；gather提交Timer也在整链预约前。上层覆盖整个调用的区间仍包含准入等待 | 失败路径可能不输出成功总耗时；必须结合失败诊断和token累计字段，不把等待再次加到父窗口 |
| 完成与恢复 | CQE回调在通知业务前结清账本；恢复日志标识retire/flush/delete阶段 | 当前没有独立的ledger CPU、bucket锁等待、provider-only post或总恢复耗时分解，不能从wr_token_wait反推出这些值 |

summary仅在已有Trace包含tick时记录，仍受原有采样/慢日志策略约束。实现扫描已有固定容量数组（最多12个downstream phase），找到WR项后累加，不为每个WR新增tick或动态容器；首次加入时若槽位已满仍可能drop，应结合phase_dropped判断，字段缺失不能直接当作0。`URMA_WR_TOKEN_WAIT`加入process慢日志判定，可能使原来未打印的请求触发summary输出；格式化/I/O量也可能变化。

WR等待是transport总耗时的重叠细项，未加入derived减法列表。不要把它再次加到总耗时，也不要在不同作用域/并行累计口径下简单相减以估计DMA时间。一个逻辑传输拆两WR时只预约一次，其失败尝试的waitedUs累加到同一槽；额度释放仍按各WR分别进行。QueryAndGet改为同Trace的Worker采样access摘要及原慢日志字段，未扩展Client协议；缺失不能解释为0。

默认最多3次准入共同使用5000us总预算，100/200us退避从其中扣除，并受API绝对截止时间限制。旧644的15300us描述不再适用于本地修订。实际返回仍可能因锁、调度及provider阻塞超过预算；summary只合计上述口径的waitedUs，不包含退避，acquire_us也不包含重试之间的退避。

#### 已有微基准与不足

2026-09-18 封版收缩：独立 `urma_wr_token_bench` 已从产品 PR 移出，连同 CMake/Bazel 入口减少 186 行、3 个变更文件；历史软件测量记录保留。复核旧基准应使用历史提交 `644dca738`，不能在修订分支运行已移除的 target。该工具不参与功能回归，也不代表硬件链路性能。

下表复用已保存测量，不是本次文档更新重新跑出的结果。验证环境为x86_64、CMake Release+ccache、WITH_TESTS=ON；每线程100万次操作，共享capacity64桶，每线程独立ledger。基准直接调用桶/账本，没有真实provider和非空buffer owner。

| 场景 | 1线程 wall ns/op | 8线程 wall ns/op | 8线程 cpu ns/op |
|---|---:|---:|---:|
| baseline：计时/校验循环 | 30.46 | 3.94 | 29.93 |
| disabled_acquire | 57.58 | 7.43 | 49.85 |
| acquire_reset | 208.07 | 947.57 | 5977.85 |
| acquire_track_complete | 388.63 | 1280.94 | 8106.34 |

全部场景errors=0、最终outstanding=0。wall ns/op为总墙钟除以总操作数，即聚合吞吐倒数；8线程1280.94ns/op不能表述为“每个请求只增加1.28us”，也不是P99。cpu ns/op为进程CPU增量除以总操作数，包含并发线程的CPU消耗。baseline包含计时和循环开销，不是旧版真实传输基线。

8线程结果提示共享bucket竞争成本值得关注，但它没有把锁、分配、引用计数分别归因；也未覆盖同一Jetty的多线程ledger竞争、真实CQE registry/轮询、等待饱和、非空owner析构、指标/summary/日志和后台扫描。capacity64下最多8个并发单额度操作，不是在测容量耗尽的排队性能。disabled基准直接调用Acquire，实际生产提交路径关闭额度时会跳过Acquire，两者不能等同。含测试注入检查的结果不能替代生产构建与真实UB设备A/B。

封版前如需性能结论，应在同一机器/同一负载对比基线、当前capacity0与capacity100，分别控制tracing/metrics配置；至少覆盖小对象高QPS、1.5MiB两WR、Gather、容量饱和和恢复，记录吞吐、P50/P95/P99、CPU、分配/驻留内存、等待分布、失败/fallback及恢复时间。上述真实链路性能对比尚未完成，门禁通过不能充当“无性能退化”结论。

源码依据：`urma_wr_token.cpp`的Acquire/WaitForCapacity/Track/Complete，`urma_resource.cpp`的RecordWrTokenWait/PostSendWr/RefillLoop/GetJettyById，`urma_manager.cpp`的UrmaWriteImpl/CheckCompletionRecordStatus，`trace.cpp`的AddDownstreamPhase和`latency_phase.cpp`的process gate。容量0只能在初始化配置时回退，不支持热缩容；Pipeline H2D兼容性和真实设备安全停止边界仍按本RFC其他章节执行。

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
| urma_wr_token_wait_us | 5000 | 一次逻辑传输的全部准入尝试与退避共享预算，范围 [0, 60000]us；0只立即尝试，启动生效 |
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

`b04ca4c8f`（已合入主线 `3d31acd54`）完成的组合回归，后续 `5bdc13c65` 重跑受影响的故障49、Worker7、系统6共62项，全部通过：

| 验证范围 | 通过 / 总数 | 覆盖 |
|---|---:|---|
| WR token | 17/17 | FIFO、容量守恒、取消、owner析构顺序 |
| 故障与配置 | 48 passed + 1 skipped | 整量预约、部分post、无普通CQE恢复、modify/delete失败重试、关闭排空和超时保留；跳过项需真实 provider |
| Jetty gate | 11/11 | post/retire/delete顺序、隔离期间flush锁存 |
| Worker定向 | 7/7 | 精确父deadline、TBB调度、QueryAndGet七种采样/等待状态 |
| 跨Worker系统 | 6/6 | 多WR预约、超容量拒绝、两端等待摘要、64×64并发BatchGet |
| 主线采样兼容 | 27/27 | request/access独立采样、0/1边界、access guard与记录格式 |

CMake Release + ccache，URMA mock；当前定向结果为 token 17/17、Jetty gate 11/11、fault 48 passed + 1 skipped、ST 6/6。系统套件耗时是测试墙钟时间，不是请求性能指标。PR当前 HEAD 的六项远程门禁由触发器11450全部通过；真实 provider 用例仍未在该环境执行。

历史 `644dca738` 曾通过277项mock/公共组件与17项独立非URMA测试及6项启用门禁；这些只作为历史记录，不计入本轮117项，也不证明新提交门禁已过。独立微基准及其构建入口已从特性PR移除，历史测量保留为参考。

新增红绿证据包含：整量预约之前提前提交、过期后post、完成token受owner析构拖延、TBB丢失父deadline、modify失败无新流量不重试、隔离flush丢失、关闭先停poll、Clear持锁join死锁、QueryAndGet采集/摘要缺失。具体边界见[验证报告](validation-report.md)。

### Use Case 到最终测试的对应

| Use Case | 已通过的代表测试 |
|---|---|
| UC01 | `MultiChunkRemoteGetUsesWrAdmission`、`GatherLargerThanCapacityRejectsBeforeSubmission`（0918组合回归通过） |
| UC02 | `FifoPreventsSmallRequestBypassingHead`、`ReleaseWakesWaitingCaller` |
| UC03 | `WaitBudgetExpiresWithoutLeaking`、`AdmissionRetryIsBoundedByAttemptsAndDeadline` |
| UC04 | `DeadlineWinsEvenWhenCapacityIsFree`、`ApiDeadlineBoundsLongerConfiguredWait` |
| UC05 | `MultiChunkRemoteGetUsesWrAdmission` |
| UC06 | `GatherLargerThanCapacityRejectsBeforeSubmission`（0918组合回归通过） |
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

旧版已完成默认值扩大、按窗口重试及删除失败恢复。本轮改为逻辑传输全量预约、共享等待预算、精确deadline传播、及时归还、modify重试和关闭排空；QueryAndGet补同Trace的Worker等待信号。讨论只能在对应修复推送并核对后标记解决。

验证中修正了测试枚举清单、后台探测计数混入及进程日志路径，不据此扩大生产修复结论。源码指纹核对、CMake 编译缓存和非 URMA 独立构建均有证据。真实 UB 的安全停止/reset 与生产性能仍未验证。

### QueryAndGet等待观测补充（09/18，软件验证通过）

该入口在AccessRecorder确定采样后启用现有Trace tick，沿用同一请求上下文的URMA_WR_TOKEN_WAIT累计；成功和错误共用收尾，在既有采样access日志中写入累计phase（us）及丢弃计数，不改变该日志条数。快请求也通过该access日志诊断；完成INFO保留原慢阈值/VLOG条件，避免把所有采样快请求标成慢日志。只有真实phase存在时打印数字（包括实测0），未采集/未进入准入/关闭令牌路径在完成日志打印NA，并附traceEnabled和phaseDroppedCount；有丢弃时不能宣称累计完整。没有phase且没有丢弃的access summary保持缺失，不输出伪造0。关闭access日志时，快请求没有该累计信号。

不扩protobuf或客户端解码；客户端QueryAndGet耗时没有新增worker等待phase，排查通过同TraceID的Worker完成日志关联。wait_us嵌套于localRead/URMA write/total，acquire_us包含wait_us，均不能再相加。新成本为采样入口一个tick、采样收尾合并至多12个downstream phase并格式化summary（包含字符串构造/分配），以及access日志字节增量；完成日志只在原慢阈值/VLOG决定输出时扫描phase并格式化等待字段。未采样不构造access summary，未输出完成日志不构造等待字符串，无新增计时器、线程或锁。未进行真实硬件A/B，不能把有界软件操作数称为零开销。

### 代码规模与封版取舍

相对最新主线 `3d31acd54` 的最终代码统计：生产C++ 24文件，新增1204、删除241，净增963；旧版为23文件、新增956、删除217，净增739。不能因删除微基准就声称生产代码缩小。本轮必要修复增加了逻辑传输预约、deadline继承、异常恢复和关闭排空；新触及的生产文件为QueryAndGet实现。独立174行微基准及12行构建入口已移出特性PR，另删除无生产调用者的20行提交封装。进一步删去ledger、post gate或安全删除证明会削弱QD/内存所有权不变量，不作为封版减行方案。测试10文件新增1809/删除30；构建6文件新增56/删除7；文档配置6文件新增104/删除1。总计46文件新增3173/删除279；主线自带变更不计入本PR。

生产改动集中在8个文件，另外16个文件合计净增73行。以下均相对主线 `3d31acd54`，加减行包含移动和替换，不能把新增行全部当作新运行逻辑：

| 生产范围 | 文件数 | 新增/删除 | 净增 | 必要性及可收缩边界 |
|---|---:|---:|---:|---|
| `urma_wr_token.{h,cpp}` | 2 | 377/0 | 377 | 容量、FIFO、预约RAII及逐WR账本；删除会失去守恒和所有权基础 |
| `urma_resource.{h,cpp}` | 2 | 419/12 | 407 | 提交许可、部分接受、后台退役、隔离重试和安全删除；已去掉20行无生产调用封装 |
| `urma_manager.{h,cpp}` | 2 | 212/168 | 44 | 读写/gather接入整量预约并传递owner；多数为替换旧提交循环 |
| 并行BatchGet实现 | 1 | 60/16 | 44 | 继承绝对deadline并避开TBB在可迁移bthread执行的问题；只提取同文件helper |
| QueryAndGet实现 | 1 | 23/5 | 18 | 补齐入口采样、Worker等待摘要与NA/丢弃诊断；未扩protobuf |
| 其余配置、计时及调用适配 | 16 | 113/40 | 73 | 配置贯通、phase定义、deadline访问和owner参数传播 |

核心bucket/ledger、resource与manager合计净增828行，占生产净增963行的约86%。可以将独立测量工具移出PR，也已执行；若再移除账本或异常关闭机制，会改变本特性的安全合同。真实硬件性能A/B尚缺，不能据此宣称当前实现已经达到最小成本。

### 主线采样兼容边界

最新主线将request与access独立采样：request日志被保留，不代表该请求一定有access等待摘要；request采样率较低但access采样率为1时，access仍可全量采集。分析缺失字段必须同时检查access采样配置、latency开关和丢弃计数，不能将缺失解释为0us。

## 当前提交门禁

历史 `97430360503d025b9f4954bb576848cd498f3701` 的6项启用门禁全部SUCCESS，PR结果评论 `190311134`；当前 `8789f20c1cef66c48ab9feea9521845977e064d6` 新增11行观测字段，当前 HEAD 已由触发器 11450 重新验证：六项启用门禁全部 SUCCESS。三个构建任务的合入日志均核对到同一提交；禁用的Bazel ARM任务不计为通过。

| 门禁 | 结果 | 同轮证据 |
|---|---|---|
| CodeCheck | SUCCESS | [trigger #11435](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/trigger/job/yuanrong-datasystem/11435/console) |
| license | SUCCESS | 同上 |
| SCA | SUCCESS | 同上 |
| x86_64 | SUCCESS | [#11611](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/x86-64/job/yuanrong-datasystem/11611/console) |
| aarch64 | SUCCESS | [#11545](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/aarch64/job/yuanrong-datasystem/11545/console) |
| Bazel x86 | SUCCESS | [#7459](https://ci.openeuler.openatom.cn/job/multiarch/job/manual-jobs/job/openeuler/job/openyuanrong/job/Test_Datasystem_Bazel_x86/7459/console) |

ARM两组C++测试首轮分别为5396项中2项异常、200项中3项异常，官方自动重跑分别2/2、3/3通过，随后Python检查报告140项、状态OK（7项跳过），example通过。保留首轮失败记录，不称为全程零失败。这5项为既有Exist、PubSub、Eviction与Stream/Producer用例；未证实其失败与WR特性的因果关系。Exist用例在指定验证环境重复1000次未复现，未为此扩展特性生产代码。以上远程门禁与本地WR专项117项/62项是不同证据，不相加冒充特性覆盖数。
