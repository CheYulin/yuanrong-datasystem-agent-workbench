# WR 额度：关键概念与开发视图

状态：2026-09-17，相关 CMake 回归通过，当前提交 6 项启用门禁全部通过。关联 [实现 PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422) 和 [详细 RFC](detailed-design.md)。

[RFC #1244](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1244) · [PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422) · [详细设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [关键概念与开发视图](https://yche.me/design/urma-wr-token-developer-view-20260917.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

## 先分清请求、WR 和令牌

一个业务请求 Req 可以拆成多个 WR。WR 是向 URMA provider 提交的实际工作单元，令牌按照 WR 数量计费。例如一个 Req 切成两段写入，就是两个 WR、两个唯一 requestId、两份额度；不能按一个 Req、一个 Event 或一个 Jetty 只扣一次。

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

`UrmaWrite/UrmaRead/Gather` 切分工作 → 为当前提交窗口申请额度 → 获得 post permit → 逐 WR 登记账本 → 调用 provider → 保留已接受的 WR，撤销明确未接受的后缀 → 完成通知或安全删除结清账本。

令牌遵守守恒关系：`outstanding = 尚未提交但已预留的额度 + 账本中尚未结清的额度`。它始终不超过配置容量。成功登记会把一个令牌从 reservation 移到账本，不会增加总数。重复完成只查到第一次，不能重复归还。

登记第 N 个 WR 失败时，provider 还没有被调用。前缀回滚守卫撤销前 N-1 个登记，失败项与剩余 reservation 各自归还持有的令牌；守卫本身不分配内存。返回错误后，由上层继续清理未提交 Event 和 lane。

provider 返回明确 bad WR 时，只归还从该 WR 开始的未接受后缀。若无法确定接受边界，则保留所有可能已接受的 WR；猜测边界会造成超发或提前释放内存。

## 一个 Req 拆成两个 WR

容量至少为 2 且一次提交两项时，两项各扣一个 token、共享同一份 buffer owner。第二项先完成只归还第二项的 token，owner 必须保留到第一项也安全结清。重复完成不能改变 outstanding。

容量为 1 时，切分后的请求应按窗口推进：第一项提交并结清后，第二项再获得额度。不能一次申请超过容量的两项并永远等待。Gather 同样按实际 WR 数裁剪窗口，而不是按逻辑对象数估计。

如果第二次申请额度超时，已经提交的第一项仍保留令牌与 owner。重试仅针对明确未提交的当前窗口，不能从整个 Req 的起点重放。

## 超时并不是安全停止证明

默认容量 100；每次额度等待最多 5000us；最多 3 次明确准入拒绝重试，之间退避 100/200us，并受 API 剩余时间约束。5000us 是单次等待上限，不能当成整次调用上限。

恢复阈值默认 5000ms。后台发现账本长时间无进展，会关闭旧 Jetty 的提交入口，发起错误态/flush，并补充可用 Jetty。确认 flush 且成功删除后才能归还剩余额度；确认 flush 后删除失败会保留所有权，并按恢复间隔重试删除。

没有 flush、修改失败或 provider 无法证明停止时，旧资源必须隔离、继续计入额度和 live-resource 上限。这样可能降低可用容量，但不能靠清零计数把仍可能 DMA 的资源伪装成已归还。真实设备的停止/reset 合同仍需设备验证；mock 测试不能替代这一证据。

## 可观测性合同

已实现并验证 `urma.wr_token_wait`：单位 us，累计同一请求本地及返回的下游额度等待，包括失败尝试。它通过既有 latency phase 协议进入 worker 和 client summary。它是 transport 总耗时中的重叠细项，不能与总耗时相加，也不再从派生阶段重复扣除。

同一请求的多个 WR/重试应累加到同一个固定容量槽，不应为每次等待增加一对 tick。未启动 latency tracing 的请求不收集该项。沿用 summary 的采样、慢日志阈值和有界丢弃规则。

`wr_token_wait_us` 为进程级等待分布；新增 `wr_token_wait_failed_total` 统计每次未取得额度的尝试，而不是业务失败请求数。限频等待失败日志保留原因、等待耗时和 outstanding。READ/WRITE 在 provider 尚未调用时返回原错误，不输出“provider post failed, ret=0”。

## 开发修改位置与验收

| 变更 | 主要位置 | 必须核对的行为 |
|---|---|---|
| 容量、等待、FIFO | `urma_wr_token.cpp` | 边界、deadline、关闭唤醒、异常回滚、计数守恒 |
| 提交事务与恢复 | `urma_resource.cpp` | 逐 WR 记账、部分接受、无 CQE 后台恢复、删除失败重试 |
| Req 切分与清理 | `urma_manager.cpp` | READ/WRITE/Gather、当前窗口重试、Event/lane 清理 |
| 请求 summary | `trace.cpp`、`latency_phase.cpp` | 多 WR 累加、下游传递、关闭旁路、避免双重扣时 |
| 配置入口 | flags、CLI JSON、Helm、说明文档 | 默认值和上下界同步；us 与 ms 不混淆 |

TDD 验收必须包含：第二区间登记分配失败的 RED/GREEN；两个 WR 乱序/重复完成与共享 owner；容量 1 下恰好两 WR 的跨 worker 数据一致性；持续额度失败时 provider 零调用且日志限频；client/worker summary 等待耗时；无成功 poll、flush 缺失、删除失败重试与 shutdown。使用 CMake+ccache，在核对源码指纹后运行；每项报告区分组件 mock、系统测试和真实设备证据。


性能与计时口径详见[详细设计 §4.4](detailed-design.md)：立即准入wait=0不代表没有锁/登记成本；WR等待不含重试退避；既有post/transport区间包含新增准入。微基准只覆盖软件桶/账本，尚不能给出真实链路性能结论。
