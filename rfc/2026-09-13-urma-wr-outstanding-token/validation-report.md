# URMA WR 额度控制：验证报告

状态：2026-09-19。当前特性分支验证 HEAD 为 `8789f20c1cef66c48ab9feea9521845977e064d6`，远端 fork 分支已同步；PR #2422 当前 HEAD 已完成门禁触发器 11450：CodeCheck、license、SCA、x86_64、aarch64、openyuanrong 全部 SUCCESS。tiantiyun-80c128g 使用 CMake Release + URMA mock + ccache 的定向回归为 token 17/17、Jetty gate 11/11、fault 48 passed + 1 skipped（真实 provider 用例）、ST 定向筛选 6/6。真实 provider 行为仍不由 mock 回归覆盖。

[RFC #1244](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1244) · [PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422) · [详细设计](detailed-design.md) · [开发视图](developer-view.md)

## 结论与证据范围

接入新主线后的组合回归117/117通过，0失败；fault 专项1项因真实 provider 未配置而跳过，测试进程正常退出。34个变更C++文件与验证机严格SHA256一致。使用指定环境的CMake Release、ccache和URMA mock；不是实际硬件DMA停止、生产P99或pmax的证明。针对主线日志、采样及依赖变化已重跑，并追加真实access采样器27项兼容测试。

## 组合回归

| 范围 | 通过/总数 | 关键覆盖 |
|---|---:|---|
| Token | 17/17 | 容量守恒、FIFO、关闭唤醒、owner析构前归还 |
| 故障及配置 | 48 passed + 1 skipped | 整量预约、过期后不post、部分接受、重试、无普通CQE、关闭排空 |
| Jetty状态机 | 11/11 | post/retire互斥、隔离flush、delete准入 |
| Worker定向 | 7/7 | 父绝对deadline、TBB安全调度、QueryAndGet七种观测情形 |
| 跨Worker系统 | 6/6 | 多WR、超容量拒绝、两端summary、64×64并发BatchGet |
| 主线采样兼容 | 27/27 | request/access独立采样、0/1边界、access guard与记录格式 |

系统套件85.212秒是全部测试墙钟耗时，不是请求延迟。历史644dca738的277项公共/mock测试、17项独立非URMA验证与6项门禁为旧版本记录，不与本轮相加，也不作为新门禁通过证据。

`5bdc13c65` 为两文件内部整理：诊断字段收拢为局部结构、显式lambda捕获、宏参数缩进、并行执行上下文提取到同文件helper；不扩公共接口。受影响49+7+6共62项重跑通过，2个变更文件严格SHA一致；这轮ST为88.755秒，仍不是性能测量。上一轮CodeCheck的4项失败有逐项修复，新远程门禁不能沿用旧结果。

## TDD红绿对照

| 缺陷/边界 | 修复前复现 | 修复后的行为 |
|---|---|---|
| 同一传输WR未同时满足 | 容量2、占用1、两WR请求仍post第一项 | 整体预约成功前零提交；超容量直接拒绝 |
| owner析构拖延额度 | buffer析构屏障期间已完成额度仍被占用 | 先归还token，再析构owner；批量结清先返还全部额度 |
| 准入后deadline到期 | 注入延迟后仍调用provider | 提交前再次复核，回滚全部未提交登记 |
| 并行deadline和调度 | 子任务丢失绝对deadline；64×64基线TBB出现崩溃 | 继承精确deadline，TBB任务入队到其线程，调用bthread协作等待 |
| modify失败无人重试 | 清除故障后无新流量仍隔离 | 既有后台恢复线程重试，gate不重开 |
| 隔离flush丢失 | 隔离期间通知未锁存 | 后续成功modify使用已锁存通知，成功delete后结清 |
| 关闭过早停poll | 延迟flush/缺失flush/首次delete失败三例失败 | poll保留到有界排空；超时明确失败且保留不安全所有权 |
| Clear持锁join | 提前flush、modify延迟、排空超时后8秒watchdog终止 | 锁内移出执行器，锁外join；无法继续删除则隔离保留 |
| QueryAndGet采集/摘要缺失 | 无start tick及access摘要分别红灯 | 成功/错误共用现有采样access出口，无额外快请求INFO |

QueryAndGet定向UT模拟两次11us等待贡献，验证access累计22；覆盖0、缺失、未采样、配置关闭和phase丢弃2。实际完成日志也核对NA/0/22、trace状态和丢弃计数，采样快请求没有额外完成INFO。它验证入口和序列化衔接；不把模拟等待值当作硬件排队测量。持续满桶的实际准入测试另将20次返回waited_us之和与summary精确对比。

最新主线将request与access独立采样：request日志被保留，不代表该请求一定有access等待摘要；request采样率较低但access采样率为1时，access仍可全量采集。分析缺失字段必须同时检查access采样配置、latency开关和丢弃计数，不能将缺失解释为0us。

## WR、请求、inflight与chip

容量是进程级软件预约加尚未安全结清的WR额度上限。一个逻辑read/write/gather的全部WR同时预约，随后转入逐WR账本；不是跨节点整个GET/SET/BatchGet的分布式原子预约。

本桶约束本进程经URMA manager发起的提交；同芯片其他进程和对端入站操作不共享该桶，比较设备统计前需要对齐进程、方向和时间窗口。

Event在准入前创建、可保留或先于硬件结束删除；因此Event峰值329与容量100不能直接证明超发。chip字段也是Event关联生命周期统计，不是bucket/ledger，更不是厂商芯片内部WQE计数。观察实际bucket的capacity/outstanding_snapshot/peak/waiting_snapshot，并关联request_id、申请WR数、预算、Acquire耗时与状态。快照不等于过去准入瞬间的原子快照。

0917报告共2436条超过20ms trace，其中特性组1331条：199条有明确准入失败、884条只有准备/完成/唤醒阶段线索、248条没有直接令牌证据；其余1105条为基线。缺部署SHA，分类不能当作逐条根因或失败率。通知后恢复79ms之类窗口不能归入DMA或token等待。

### 0918 3x/10x 补充证据

0918报告按各时延区间定额采样，区间条数不能外推全量发生率。10x采样包含797条`wr_token_wait_timeout`，原始日志中的等待为约5003--5140us，并出现`outstanding=96..100 capacity=100`；这是令牌触顶和5ms准入预算生效的直接证据。报告中`urma_inflight_wr_count`为event map快照，10x采样p99为305、最大321，不能与单个进程Resource的token容量直接比较。

3x对照同样关键：3x没有令牌等待超时，Client最大15.644ms，但event map最大值已经达到130。10x才出现令牌触顶和URMA超时，event map最大321。因此130/321是Event生命周期或完成通知积压的观测，不是100个令牌或provider硬件QD的观测。令牌按WR数量计费，不按字节计费；只有每个WR固定为4MiB时，100个令牌才可换算为400MiB的逻辑payload窗口。

具体普通`UrmaWrite`样本`getBuffer-89-2599-00075195;2e28bc639b76`显示：WR 1025809的`post->completion=20.279ms`，JFC `poll_begin-post`约20.257ms，`pre_completed_before_wait=1`且wait仅0.00073ms；后续WR 1026179在4.910ms超时，记录`pendingWrs=1, orphanWrs=1`并force release。该链路支持“JFC poll/完成观察延迟+未收到CQE的orphan WR”这一候选根因，不支持把Gather Write作为必要解释。CQE结算会先归还token，event通知和event map删除可滞后，因此高event/chip快照与真实provider outstanding不能混用。

PR 8789f20c1在URMA完成慢日志中新增`urma_event_map_size`和同刻`wr_token{outstanding,capacity,waiting,peak}`，保留旧`urma_inflight_wr_cnt`字段兼容现有报告解析。后续3x/10x验收必须以同一进程、方向、时间窗口的token snapshot确认100上限，并单独关联provider post、CQE、poll、orphan和Jetty重建；不能用采样区间计数或event-map最大值证明硬件QD超发。

完成性边界：源码和mock/st用例已经证明普通读写入口汇聚到`PostReservedWr`，部分提交、CQE、flush、orphan和Jetty重建不会提前归还不确定WR的额度。若provider永久不产生CQE或`FLUSH_ERR_DONE`，实现会保守保留token并隔离Jetty，进入fail-closed背压；这保证令牌不泄露，但不能仅凭软件用例宣称硬件场景一定自动恢复。真实验收必须注入或观测持续poll/CQE/flush故障，验证恢复后token回到基线，永久无安全证据时则验证其不提前下降。

## 安全归还合同

普通CQE恰好结清一次；异常路径必须确认Jetty静默且provider删除成功后才能释放剩余token与buffer。部分post只有明确未接受后缀可以撤销，未知bad_wr保守保留。无普通CQE或无新流量可以触发退役/重建；永无flush、永久provider失败且没有设备停止/reset证明时仍须保留所有权，不能用超时清零冒充回收。

关闭排空预算复用恢复阈值，包含收发Jetty及退休登记窗口；预算不覆盖可能卡住的provider调用、post许可退出或最终线程join。恢复验证通过不等于所有设备故障都有硬实时退出保证。

## 性能开销与计时变化

- 一次逻辑传输从逐chunk/window获取额度改为一次整量预约，减少准入次数，但未提交后缀也暂占额度；FIFO队首大请求可能阻挡小请求。
- 无排队路径仍有bucket锁和引用计数；逐WR账本有锁、哈希节点分配、时间读取、owner引用；完成有registry查找、账本移除和归还；50ms后台扫描有O(J+W)成本。
- 并行BatchGet新增TBB入队、完成通知、每range上下文复制及可选phase合并；并行等待累计可能大于墙钟时间。
- QueryAndGet复用既有采样access日志，增加phase合并、summary字符串构造/分配和字节量，不新增快请求完成INFO。access关闭时快请求没有该累计信号。
- 普通write逐chunk elapsed/postUs/urma_write_latency现在从整量预约之后计时，不再包含准入等待；整个write/gather/上层transport仍包含。逐chunk数值下降不证明设备变快。
- wait_us只在实际走过条件等待时记录正值，起点为Acquire加锁前，因此也包含初始锁竞争和重新获锁；立即准入记0，acquire_us覆盖完整Acquire；重试退避消耗共享预算但不计入该phase。等待已经嵌套在父窗口内，不能再与total相加。

默认5000us约束一次逻辑传输的准入尝试及退避预算，不是整体请求20ms上界。mutex、调度、provider与完成等待可使墙钟超过预算。尚未完成同机同负载的真实硬件baseline/capacity0/capacity100 A/B，不能宣称没有性能回退。

## 独立微基准是什么

它是只测软件bucket/ledger操作的独立可执行程序，并非业务路径、硬件压测或必要运行依赖。为控制PR范围，174行源码和12行构建入口已移出；历史结果仍保留为参考。

历史条件为Release+WITH_TESTS、每线程100万次、共享容量64、每线程独立ledger。单线程acquire/reset约208.07ns/op，track/complete约388.63ns/op；8线程聚合wall ns/op分别947.57和1280.94，对应线程平均均值5977.85和8106.34ns。聚合值不是单请求延迟；未覆盖实际CQE、饱和排队、同Jetty账本竞争、非空owner析构或新修订，不能据此评估本次pmax。

## 可复现的软件验证入口

```bash
cmake --build build --target ds_ut_urma_send_jetty_fault ds_ut_urma_wr_token ds_ut_urma_jetty_gate ds_st_urma_wr_token -j8
DS_URMA_DEV_NAME=bonding_mock0 build/tests/ut/ds_ut_urma_send_jetty_fault
build/tests/ut/ds_ut_urma_wr_token
build/tests/ut/ds_ut_urma_jetty_gate
DS_URMA_DEV_NAME=bonding_mock0 build/tests/st/ds_st_urma_wr_token --gtest_filter='UrmaWrToken*Test.*:UrmaSendJettyPoolStTest.BatchGetGatherWriteSharesOneLaneAcrossOverlappingGroups:UrmaSendJettyPoolMaxParallelStTest.LEVEL1_Concurrent64BatchGetsWith64ObjectsKeepLanePoolUsable'
```

Worker定向7例来自现有worker_oc_service_impl_test.cpp，使用外部CMake定向链接以减少大测试目标编译开销，复用原target编译参数/库、ccache；未向PR添加新测试框架或独立微基准目标。日志、XML、TDD失败基线和源码指纹分别保存。

## 门禁与交付状态

新增提交保留旧历史。主线合并后的117项回归已通过，PR已推送并重新触发门禁；四条检视讨论已回复并回读确认关闭；当前6项启用门禁全部通过，不把旧门禁或禁用任务计入本轮。

## 当前提交门禁

历史 `97430360503d025b9f4954bb576848cd498f3701` 的6项启用门禁全部SUCCESS，PR结果评论 `190311134`；当前 `8789f20c1cef66c48ab9feea9521845977e064d6` 新增了11行观测字段，不能复用该历史结果，必须重新触发当前HEAD门禁。三个构建任务的合入日志均核对到同一提交；禁用的Bazel ARM任务不计为通过。

| 门禁 | 结果 | 同轮证据 |
|---|---|---|
| CodeCheck | SUCCESS | [trigger #11435](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/trigger/job/yuanrong-datasystem/11435/console) |
| license | SUCCESS | 同上 |
| SCA | SUCCESS | 同上 |
| x86_64 | SUCCESS | [#11611](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/x86-64/job/yuanrong-datasystem/11611/console) |
| aarch64 | SUCCESS | [#11545](https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/aarch64/job/yuanrong-datasystem/11545/console) |
| Bazel x86 | SUCCESS | [#7459](https://ci.openeuler.openatom.cn/job/multiarch/job/manual-jobs/job/openeuler/job/openyuanrong/job/Test_Datasystem_Bazel_x86/7459/console) |

ARM两组C++测试首轮分别为5396项中2项异常、200项中3项异常，官方自动重跑分别2/2、3/3通过，随后Python检查报告140项、状态OK（7项跳过），example通过。保留首轮失败记录，不称为全程零失败。这5项为既有Exist、PubSub、Eviction与Stream/Producer用例；未证实其失败与WR特性的因果关系。Exist用例在指定验证环境重复1000次未复现，未为此扩展特性生产代码。以上远程门禁与本地WR专项117项/62项是不同证据，不相加冒充特性覆盖数。
