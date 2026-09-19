# WR 额度：关键概念与开发视图

状态：2026-09-19。当前特性分支验证 HEAD 为 `8789f20c1cef66c48ab9feea9521845977e064d6`，远端 fork 分支已同步；PR #2422 当前 HEAD 已完成门禁触发器 11450：CodeCheck、license、SCA、x86_64、aarch64、openyuanrong 全部 SUCCESS。tiantiyun-80c128g 使用 CMake Release + URMA mock + ccache 的定向回归为 token 17/17、Jetty gate 11/11、fault 48 passed + 1 skipped（真实 provider 用例）、ST 定向筛选 6/6。真实 provider 行为仍不由 mock 回归覆盖。

[RFC #1244](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1244) · [PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422) · [详细设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [关键概念与开发视图](https://yche.me/design/urma-wr-token-developer-view-20260917.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

## 先分清请求、WR 和令牌

一个业务请求 Req 可以拆成多个 WR。WR 是向 URMA provider 提交的实际工作单元，令牌按照 WR 数量计费。例如一个 Req 切成两段写入，就是两个 WR、两个唯一 requestId、两份额度；不能按一个 Req、一个 Event 或一个 Jetty 只扣一次。

额度的作用域是本进程经该URMA manager发起的read/write/gather提交。同一芯片上其他进程的提交、对端发起的入站操作，不会自动进入这个桶；芯片级统计必须先对齐进程、方向和时间窗口，不能拿总量与单进程capacity直接比较。

Jetty 是提交和恢复的资源，不是令牌。JFC 是完成队列，poll 读出完成通知；poll 暂时没有结果并不意味着 WR 已停止。Event 用来等待或通知业务调用者，业务超时或 Event 删除也不意味着 DMA 已停止。

| 概念 | 职责 | 不能代替什么 |
|---|---|---|
| `WrTokenBucket` | 进程级可用额度与 FIFO 等待；容量 0 旁路 | 不证明某个 WR 已完成 |
| `WrToken` | 可移动的额度所有权；析构归还仍由它持有的额度 | 不能在已提交后仅随业务栈退出而销毁 |
| `WrTokenLedger` | 每个 Jetty 按 WR 唯一标识持有 token 与 buffer owner | Event 的数量不是该账本的大小 |
| post permit | 防止提交与 Jetty 关闭/删除并发 | 不代替 WR 完成证明 |
| lane lease | 业务发送通道及 peer inflight 生命周期 | 不代表硬件只允许一个 WR |
| buffer owner | 确保本地内存覆盖最后一个可能的 DMA | 不应在第一个子 WR 完成时释放整份数据 |

## 一条写入如何流转

`UrmaWrite/UrmaRead/Gather` 切分工作 → 一次预约本次逻辑传输的全部额度 → 获得 post permit → 逐 WR 登记账本 → 调用 provider → 保留已接受的 WR，撤销明确未接受的后缀 → 完成通知或安全删除结清账本。

令牌遵守守恒关系：`outstanding = 尚未提交但已预留的额度 + 账本中尚未结清的额度`。它始终不超过配置容量。成功登记会把一个令牌从 reservation 移到账本，不会增加总数。重复完成只查到第一次，不能重复归还。

登记第 N 个 WR 失败时，provider 还没有被调用。前缀回滚守卫撤销前 N-1 个登记，失败项与剩余 reservation 各自归还持有的令牌；守卫本身不分配内存。返回错误后，由上层继续清理未提交 Event 和 lane。

provider 返回明确 bad WR 时，只归还从该 WR 开始的未接受后缀。若无法确定接受边界，则保留所有可能已接受的 WR；猜测边界会造成超发或提前释放内存。

## 一个 Req 拆成两个 WR

容量至少为 2 且一次提交两项时，两项各扣一个 token、共享同一份 buffer owner。第二项先完成只归还第二项的 token，owner 必须保留到第一项也安全结清。重复完成不能改变 outstanding。

容量为1时，含两个WR的传输立即拒绝；容量为2而已占1时，两个WR都不能提前提交。Gather同样按实际整链WR数预约。该边界是单次逻辑URMA读、写或gather；不宣称跨节点API或BatchGet全部对象同时预约。

预约成功后逐WR转交账本；中途失败时，未提交预约由RAII归还，已接受或不确定的前缀仍保留令牌与owner。准入重试只发生在任何WR提交前，不能重放已提交前缀。

## 超时并不是安全停止证明

默认容量100；一次逻辑传输的预约最多3次尝试，连同100/200us退避共用5000us预算，并受API剩余时间限制。提交前再次检查deadline。这限制预约等待和过期后的新提交，不保证包含调度、provider和完成等待的整个调用在5000us或20ms内返回。

恢复阈值默认 5000ms。后台发现账本长时间无进展，会关闭旧 Jetty 的提交入口，发起错误态/flush，并补充可用 Jetty。确认 flush 且成功删除后才能归还剩余额度；确认 flush 后删除失败会保留所有权，并按恢复间隔重试删除。modify失败也按原恢复线程重试，隔离期间flush会锁存。关闭时保持poll/恢复循环，先有界排空再停止；超时返回错误并保留不安全资源，Clear在锁外join删除执行器。

没有 flush、修改失败或 provider 无法证明停止时，旧资源必须隔离、继续计入额度和 live-resource 上限。这样可能降低可用容量，但不能靠清零计数把仍可能 DMA 的资源伪装成已归还。真实设备的停止/reset 合同仍需设备验证；mock 测试不能替代这一证据。

## 可观测性合同

已在普通GET等路径实现并验证 `urma.wr_token_wait`：单位 us，累计同一请求本地及返回的下游额度等待，包括失败尝试。它通过既有 latency phase 协议进入 worker 和 client summary；QueryAndGet现已通过同Trace的Worker采样access日志补充累计等待，原完成慢日志显示等待/NA与丢弃计数，不扩响应协议或Client解码；Client分段缺失仍不能当作零。它是 transport 总耗时中的重叠细项，不能与总耗时相加，也不再从派生阶段重复扣除。


最新主线将request与access独立采样：request日志被保留，不代表该请求一定有access等待摘要；request采样率较低但access采样率为1时，access仍可全量采集。分析缺失字段必须同时检查access采样配置、latency开关和丢弃计数，不能将缺失解释为0us。

同一请求的多个 WR/重试应累加到同一个固定容量槽，不应为每次等待增加一对 tick。未启动 latency tracing 的请求不收集该项。沿用 summary 的采样、慢日志阈值和有界丢弃规则。

`wr_token_wait_us` 为进程级等待分布；新增 `wr_token_wait_failed_total` 统计每次未取得额度的尝试，而不是业务失败请求数。限频等待失败日志保留原因、等待耗时和 outstanding。READ/WRITE 在 provider 尚未调用时返回原错误，不输出“provider post failed, ret=0”。

## 开发修改位置与验收

| 变更 | 主要位置 | 必须核对的行为 |
|---|---|---|
| 容量、等待、FIFO | `urma_wr_token.cpp` | 边界、deadline、关闭唤醒、异常回滚、计数守恒 |
| 提交事务与恢复 | `urma_resource.cpp` | 逐 WR 记账、部分接受、无 CQE 后台恢复、删除失败重试 |
| Req 切分与清理 | `urma_manager.cpp` | READ/WRITE/Gather、整次预约和有界重试、Event/lane 清理 |
| 请求 summary | `trace.cpp`、`latency_phase.cpp` | 多 WR 累加、下游传递、关闭旁路、避免双重扣时 |
| 配置入口 | flags、CLI JSON、Helm、说明文档 | 默认值和上下界同步；us 与 ms 不混淆 |

TDD 验收必须包含：第二区间登记分配失败的 RED/GREEN；两个 WR 乱序/重复完成与共享 owner；容量足够时两WR的跨worker数据一致性、容量不足时零提交；持续额度失败时 provider 零调用且日志限频；client/worker summary 等待耗时；无成功 poll、flush 缺失、删除失败重试与 shutdown。使用 CMake+ccache，在核对源码指纹后运行；每项报告区分组件 mock、系统测试和真实设备证据。


性能与计时口径详见[详细设计 §4.4](detailed-design.md)：立即准入wait=0不代表没有锁/登记成本；WR等待不含重试退避。整量预约移到普通write的chunk循环之前后，逐chunk的`[UrmaWrite] elapsed`、`postUs`及`urma_write_latency`不再包含这次准入等待，而整个write/gather/上层transport窗口仍包含。逐chunk指标下降不能解释成DMA加快。并行入口还增加TBB入队、完成通知、每range上下文复制和可选Trace汇总；并行等待之和可能大于墙钟时间。微基准仅覆盖历史软件桶/账本，尚不能给出本轮真实链路性能结论。

0918新增准入日志包含request_id、requested、capacity、outstanding_snapshot、peak、waiting_snapshot、wait_us、acquire_us、wait_budget_us与api_remaining_at_entry_us。峰值在既有桶锁下维护，快照仅在实际输出日志时加锁读取；不是与准入事件原子绑定的历史水位。Event map和chip Event生命周期计数必须与该额度分开解释。

0918 10x 具体样本 `getBuffer-89-2599-00075195;2e28bc639b76` 为普通 QueryAndGet/UrmaWrite，并无 Gather Write 作为前提：`urma_inflight_wr_count=301`、`postSrcChipInflight={1:145,2:145}`，但 `post` 到 JFC `poll_begin` 约有20.257ms间隔；同一请求随后出现 `pendingWrs=1, orphanWrs=1`。这说明需把 JFC poll/完成观察延迟、event map/chip 生命周期和真实 token/CQE 结算分开关联。PR 8789f20c1 在 URMA 完成慢日志中补充 `urma_event_map_size` 与同刻 `wr_token{outstanding,capacity,waiting,peak}`，保留旧字段供历史报告解析；下一轮运行验收必须按进程、方向和时间窗口比较这些字段，不能用 event map 最大值替代硬件 outstanding WR。


QueryAndGet采样快请求只扩展既有access记录，不新增完成INFO条数；采样收尾有phase合并、字符串构造及日志字节开销。未采样无summary，关闭access日志时快请求没有该信号。已验证NA/0/22及丢弃2的实际日志输出；这不等于真实链路性能A/B通过。无普通CQE可以主动退役，但永无flush且没有设备停止证明时必须保留额度，不能把超时当作DMA停止。
