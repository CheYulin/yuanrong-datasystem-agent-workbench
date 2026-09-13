# URMA outstanding WR 控制与无完成恢复：详细设计

状态：实现与软件验证完成，启用门禁通过，尚未合入。修订日期：2026-09-14。
源码基线：upstream master `6ca4d4ba2f6fde0a68b99141d791d58bc70f978f`。
[实现 PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422)，当前提交 `18c1543e0`。
[HTML 设计页](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html)。
本修订替代 2026-09-13 草案；测试通过项与待验证项见第 8 节。

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
4KB 包粒度和链路带宽需以实际部署及设备证据验证；默认容量 64 是待压测的初值。
最新主线 lane pool 默认已为 200，不能沿用历史样本中的 64 当作当前配置。

## 2. 需求与不变量

| 编号 | 合同 |
|---|---|
| N1 | 启用时，0 ≤ reserved + potentially accepted ≤ capacity；每个 WR 一个额度 |
| N2 | 超额请求最多等待 min(配置等待时间, API 剩余时间)，API 期限优先 |
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
| UC03 | 满容量且短等预算耗尽 | K_URMA_TRY_AGAIN，wr_token_wait_timeout，可按既有上层策略 fallback |
| UC04 | API 已过期或先于短等期限结束 | K_RPC_DEADLINE_EXCEEDED，不继续新提交 |
| UC05 | 单对象拆多个 chunk | 每个真实 WR 单独计数，超过容量仍可分批推进 |
| UC06 | gather 链长超过容量 | 分成有界提交窗口，不等自身尚未提交的预约额度 |
| UC07 | 中途注册失败、post 拒绝或部分接受 | 已知未接受部分归还；已接受和不确定部分保持记账 |
| UC08 | 请求超时返回后才收到完成 | 正确释放旧 WR；不能影响已复用 lane 的新请求 |
| UC09 | 保留 Event 被驱逐或 TTL 到期 | 硬件额度仍在，等待者不能因业务记录删除而越过硬顶 |
| UC10 | 某 jetty 不再完成，系统无新流量 | 后台检查启动 retire/refill；有效 flush + delete 后恢复额度 |
| UC11 | 错误完成、重复完成、旧 request ID | 对应 WR 至多结清一次；旧身份不影响替代 jetty |
| UC12 | provider modify/delete 失败或 flush 永不到达 | 隔离、限额保留并清楚报错，不伪造恢复成功 |
| UC13 | 服务关闭时仍有排队和在途 WR | 排队者退出，等待已获 post permit 的调用，保持 provider 生命周期安全 |
| UC14 | 配置关闭或非法参数 | 关闭时兼容原路径；非法参数启动校验失败；不支持运行中缩容 |

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

### 4.2 恢复与生命周期

复用 `UrmaJetty` 的 ACTIVE -> QUIESCING -> MODIFYING -> WAIT_FLUSH -> DELETE_READY -> DELETING -> DESTROYED。
失败进入 QUARANTINED。复用 `RetireJetty`、post gate 和 `ScheduleDeleteJetty`，不另建与它们竞争的删除器。

在既有 refill 控制线程的 50ms 周期检查最老未结清 WR。暂定 recovery_timeout_ms=1000，作为独立启动参数；
因为这是在途恢复时间，不应复用几百微秒的准入等待参数。检查时间达到阈值时关闭旧 jetty，不等待新的成功 CQE。
新 jetty 可先按原有 live/refill 上限创建，但创建本身不释放旧 WR token。

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

## 5. 配置

| 参数 | 初始默认 | 语义 |
|---|---|---|
| urma_wr_token_capacity | 64 | 进程级 WR 额度，范围 [0, 65536]，0 关闭，启动生效 |
| urma_wr_token_wait_us | 300 | 单次准入等待预算，范围 [0, 2000]us，0 不等，启动生效 |
| urma_wr_token_recovery_timeout_ms | 1000 | 范围 [1, 60000]ms，WR 未结清触发退役的年龄阈值，启动生效；不等于保证完成时间 |

Worker flags、部署 JSON 和 Helm 使用同名参数及范围。客户端首次 URMA 初始化前支持
`DATASYSTEM_URMA_WR_TOKEN_CAPACITY`、`DATASYSTEM_URMA_WR_TOKEN_WAIT_US`、
`DATASYSTEM_URMA_WR_TOKEN_RECOVERY_TIMEOUT_MS`；未设置保留现值，非法值初始化失败，Worker 忽略这些客户端环境变量。

流水线 H2D 内部还有独立 provider 提交，不具备逐 WR 记账接口。本次实现将明确拒绝与启用 token 的组合，
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

## 8. 已执行证据与待办

结果来自 2026-09-14 的专用分支；尚未合入。下列数字是独立测试目标，存在交叉覆盖，不应相加当作唯一场景数。

| 验证 | 实际结果 | 覆盖及限制 |
|---|---|---|
| CMake token 组件 | 17/17，0 failure | FIFO、关闭、并发守恒、bthread、期限、超大预算、buffer owner |
| CMake URMA mock 故障与客户端配置 | 34/34，0 failure | 30 项故障回归和 4 项环境配置；部分提交、未知 bad_wr、迟到/重复 CQE、后台恢复、pipeline 拒绝 |
| CMake Jetty gate | 10/10，0 failure | post/退休互斥、隔离保留额度和 owner、迟到完成结清 |
| CMake 跨 Worker GET | 2/2，0 failure | capacity=1 的 2 MiB 多 chunk 读；512 个 8 KiB 对象 gather；核对数据及准入入口 |
| CMake 非 URMA | 静态客户端构建成功；token 17/17 | 核查不依赖 provider SDK 的组件和包装接口 |
| Bazel 7.4.1 token | 目标 PASS，17 项组件用例 | 补齐直接头文件和链接依赖；mock 故障 34/34、gate 10/10 均通过 |

SDD/TDD 过程保留了运行期红测证据：期限及 owner 新增测试曾有 4 项失败，客户端配置有 2 项失败，
pipeline/配置集成有 2 项失败；实现修正后以上最终 CMake 结果通过。故障测试中两个旧断言与基线源码不符，
已按时间负值钳制及现有 request ID 日志键校正，不能将其归因为本次新引入的生产缺陷。

构建使用 x86_64 URMA mock 环境、CMake Release 和 ccache 4.5.1，第三方依赖使用显式缓存目录，
已观察到编译缓存命中。CMake 初次缺少 utils.pb.h 时先构建 utils_protos；未在本次改动中扩展修复基线依赖。
CTest 聚合入口因其他未构建测试的发现文件缺失而失败，表内结果来自直接执行 CMake 测试二进制和 XML，
不代表整个 CTest 套件通过。Bazel 9 的 native C++ 规则不兼容，以及 Bazel 6 的平台依赖错误均单独记录；
Bazel 7.4.1 已进入有效构建和测试。

软件微基准提供 baseline、acquire/reset 和 acquire/track/complete 三组，同一进程桶、多线程、逐线程 ledger。
构建结束后每线程 100 万次操作的单次样本如下，所有组 errors=0、最终 outstanding=0：

| 线程 | baseline wall ns/op | acquire/reset wall ns/op | 完整账本 wall ns/op | 完整账本 CPU ns/op |
|---|---:|---:|---:|---:|
| 1 | 31.08 | 138.21 | 235.37 | 234.49 |
| 8 | 4.04 | 711.01 | 1029.67 | 6533.57 |

多线程样本显示共享桶锁争用成本，不能宣称无开销。wall ns/op 是聚合吞吐倒数，CPU ns/op 为进程 CPU
消耗除以操作数；不代表单请求 P99、真实 URMA 链路吞吐，也不是 master 与 PR 的端到端 A/B。
ccache 统计观测到 887 次命中；这是该缓存累计值，不等同于本次所有编译均命中。
Bazel 的两个 URMA 测试目标采用静态链接以解决动态依赖中 allocator 符号缺失；gate 补齐相邻测试使用的
链接依赖，指标测试改用 TEST_TMPDIR，最终故障 34/34 和 gate 10/10 通过。CMake 故障复测仍为 34/34。
配置范围专项测试 1/1 通过，18 份模块元数据有效。

HTML 已发布并在 1440px 和 390px 检查：无页面 JS 异常、无页面横向溢出、目录锚点完整。PR 已创建，当前提交的最终门禁结果见本节末。
尚无真实 UB 设备证据，不能声称物理 DMA 恢复、provider quiescence 契约、真实硬件性能已验证。
完全收不到任何 flush/停止证据的隔离路径保留额度；强制 reset 的设备合同仍为后续硬件验证项。


### PR 门禁修复记录

首轮 CodeCheck 四项问题通过职责拆分与封装修正：隐藏桶状态类型、拆分等待逻辑、取消可修改账本引用、
用明确 WR 描述结构替代长参数列表。第二轮仅剩调用参数缩进，修正后第三轮 CodeCheck 通过。
功能调整后重新通过 CMake token 17/17、fault 34/34、gate 10/10、跨 Worker GET 2/2、非 URMA token 17/17，
以及 Bazel 三目标；该轮格式修正只调整空白；后续另补充下面记录的测试 mock 签名修正。

首轮 x86 构建 11441 完成编译，但 Python 打包因缺少 wheel 失败；该轮日志显示 CI 的 Python 3.9 依赖安装
失败后被忽略。未用修改业务代码掩盖环境问题；后续 x86 构建已通过，最终结论以当前提交门禁为准。


ARM 构建 11377 暴露既有 binmock 用例仍按旧内部函数签名匹配的问题。已同步 13 处 READ/WRITE/gather
mock 参数数量及 3 个回调签名，并断言迁移传入非空本地 buffer owner。最终测试文件通过 CMake 生成规则编译
（使用 ccache）；非 URMA Bazel 配置下迁移 14/14、远程 GET 准入 3/3 通过。
补充完整 worker 单测在 root 下为 119/120，失败项 `RevokeHealthProbeReportsUnlinkFailureAndRetries` 在 chmod 后
仍能成功删除文件。使用同一二进制在独立可写目录以普通用户 nobody 复测后，120/120 通过，另有 1 项原有禁用测试；
XML 为 121 项、0 failures、1 disabled，进程退出码 0。该差异确认了权限测试的 root 环境限制，没有修改健康探针源码或断言。


ARM 11378 编译通过后执行 5311 项测试，暴露 `kv_metric_urma_id_layout_test` 仍断言旧枚举结束值 146。
已补齐新指标 `WR_TOKEN_WAIT_US=146` 的编号及名称校验，并更新 `KV_METRIC_END=147`，已有指标编号保持不变。
目标环境使用 ccache 的 CMake `ds_ut` 构建通过，执行 `--gtest_filter=MetricsTest.*` 得到 104/104、0 failures。
独立 Bazel metrics 目标因既有传输符号依赖缺失未链接成功；上述运行证据来自 CMake 二进制。
当前提交的指标布局用例在 ARM 11379 中通过。


上一轮 ARM 11378 的最终失败还有 `ProducerTest.TestReCreateProducerDiscardData` 超时。
验证机 CMake 构建 `ds_st_stream_cache` 后独立执行该用例，1/1 通过（6.092 秒）；未在此环境复现。
单次本地通过不能替代 ARM 验证；后续连续复测及最终门禁结果见下文。


### 当前提交门禁与交付结论

提交 `18c1543e05dd45fa05cef3eaa344b14ccbf71943` 的六项启用门禁最终均为 SUCCESS：
CodeCheck、License、SCA（trigger 11273），x86 构建 11445，ARM 构建与测试 11379，Bazel x86 7297。
额外 Bazel ARM 作业被 CI 配置跳过，不计为通过。PR 尚未合入，等待维护者评审。

ARM 并非首轮全绿：自动复测后最终通过，包括前轮超时的 producer 重建用例。
该 producer 用例在验证机一次运行通过，随后连续复测也出现 60 秒 CreateProducer 超时；抓栈显示
AddCursorForProducer、RemoteAck、AckCursors 等待 StreamManager 的同一互斥量，尚未证明锁的持有者及根因。
本 PR 与基线在 stream_cache 源码和该测试上没有差异，未用放宽超时或屏蔽用例改变结果；这是需单独追踪的
stream 并发风险，不能用最终门禁成功推导它已修复，也不能据此认定由 WR token 机制引起。

本次交付覆盖 WR 准入、配置等待、独立完成账本、无数据 CQE 后台退役和安全结清、mock 用例、
指定环境 CMake/ccache 验证及双构建门禁。真实 UB provider 停止/reset 契约和硬件 A/B 仍未验证。
