# 统一 TCP 测试验证结果

Status: Completed，TCP 测量、本地核验与公开核心页面验收已完成；记录干扰排除和资源缺样的 unknown/null 保持原义，不表示所有证据无未知项。

## 1. 已完成的源码与环境准备

复用 DataSystem v0.9.2.rc44、Mooncake v0.3.13.post1、LMCache v0.5.6 的实际源码构建；新环境部署时重新核对 8 个原生制品摘要。DataSystem 安装 SDK 的 160 个非 dist-info wheel 成员逐个核对一致（40 个 Python 文件、33 个动态库，0 缺失、0 差异）。产品源码及二进制保持原状。

正式入口、目标节点和 cgroup 配额见 tcp-plan.md。原生路径完整矩阵与公开报告仍在 validation.md 对应地址，两个 profile 单独统计。

## 2. 实际协议预检历史

各轮均属于正式性能测量之前的协议和编排预检。失败保持失败，不能从成功的局部步骤推断整个门禁完成。

| 尝试 | 实际观察与终止原因 | 处置与证据边界 |
| --- | --- | --- |
| 1 | DataSystem 两端点的 1 MiB 完整字节及 TCP syscall 探测通过；附加六组检查因 Python access 记录没有 `transportType` 字段失败 | 核对安装 SDK 与构建 wheel 一致；保留原失败，改用可观测证据，不修改产品添日志 |
| 2 | 六组 bytes 与 EXIST 检查均真；SDK 活着时等待 access 日志刷出超时；部分 socket 证据还误筛了客户端绑定端点 | 真实 trace 显示一次探测连续 149 次读取 access 文件均返回 0，退出后记录齐全；父进程改为 SDK 正常退出后严格校验，允许对象路由到配置内任一 Worker并记录实际载荷端点 |
| 3 | 修订的 DataSystem 六组完整探测通过；Mooncake launcher 部署模式为 `0644`，无法执行 | 定位为部署执行位问题，不能作为 Mooncake 接口或 TCP 性能失败；保留失败轮与启动日志 |
| 4 | DataSystem 六组再次通过；Mooncake 两个客户端的 1 MiB 完整字节与 TCP 探测通过，随后旧 venv console 的 `execv` 令 `argv[0]` 落到 TCP 根目录外，严格停止器拒绝清理 | 保留 PID/start ticks/exe/cgroup 防护；另行核对关联旧 master 的精确身份后安全 SIGTERM，恢复凭据存于 `formal-tcp-001/linked-master-recovery.json`；本轮整体仍失败 |
| 5 | 部署 `copy2` 再次把 launcher 权限带回 `0644`，后续执行受阻 | 修正部署顺序为复制后设置/核对执行位，增加权限变化审计及仅权限变化的受限重试；随后第六轮完整门禁通过，前五轮失败不改判 |

v3 DataSystem 三尺寸 × 两接入端点的完整探测已多次通过，第六轮三产品整体门禁也已通过（见第 6 节）。主矩阵驱动完成事实见第 7 节；这些后续结果不改变前五轮失败状态。

## 3. DataSystem 证据口径与已确认限制

固定版本 Python Set 从 `kv_client.py:353` 调用 `pybind_register_kv.cpp:276–291`，Get 从 `kv_client.py:595` 调用同文件 `459–498`。这些 pybind AccessRecorder 没有调用 C++ KVClient 中的 `.TrackedTransportType()`；不能把 C++ API 的日志字段假定为 Python API 的实际输出。安装文件与 wheel 一致，失败原因是诊断字段假设不成立。

修订门禁已逐尺寸、逐接入端点、逐 PUT/GET 时间窗口核对直接 TCP 字节及独特 payload 前缀，GET 另与同 key、同尺寸的实际 Materialize 日志、完整读回、Worker flags 绑定。SDK 进程退出后才由父进程读取日志；日志缺失、错误码、字节不足、前缀或实际物化证据不符仍使门禁失败。进程前后共享可写映射与 UNIX socket FD 快照保留，用作本次探测的补充证据，不扩大为所有时刻、所有路径的通用证明。

接入端点不等于路由后的存储端点。第二轮真实日志中，绑定 `127.0.0.1:31531` 的 1 KiB 探测实际在 `127.0.0.1:31532` 完成 PUT/GET；两者均在同一目标节点。修订解析只接受配置内的两个 Worker TCP 地址，单独记录真实携带载荷前缀的 socket 端点。不能用客户端 index 宣称该数据一定存储在对应 index 的 Worker。

第二轮隔离副本的六组原始日志/trace 已重新验证解析，210 个文件摘要与原结果记录一致；这个回归只证明工具能解析真实证据，原 child 退出码和 gate 失败状态均保持原样。后续 v3 完整新探测的通过状态与这一回归分开记录。

## 4. 归档、清理与过程耗时

失败轮保存在目标新实验根目录下 `formal-tcp-001/protocol-history/attempt-NNNN`，修订前后 helper 快照、操作者脚本快照与变更审计位于 `preflight-history`、`preflight-changes.json`。受限事务要求正式测量尚未准备/开始、suite lock 独占且 cgroup 排空；原 stage JSON 及其摘要、产品二进制、adapter、benchmark 和工作负载配置保持不变。第五轮权限修复属于部署模式修正，也应在审计链中保留，不能以内容 SHA 未变为由省略。

失败、停止器拒绝和人工恢复均保留真实退出状态。第四轮的关联 master 不符合本轮严格停止器的入口身份，因此没有放宽规则或按名字批量结束进程；主任务另核验精确 PID、start ticks、exe 与 cgroup 后进行 SIGTERM，并将凭据保存到上表所列恢复 JSON。

用户要求的操作耗时覆盖所有预检尝试、恢复及交付验收，而非只保留成功步骤。每轮 `summary.json` 和 `operations.jsonl`、外层操作 ledger 保留已有 UTC、同进程 monotonic 耗时及返回状态；关联master恢复凭据只有wall起止，按 timing.md 明确标注，不冒称monotonic。重试和归档有各自区间，单列在过程时间线，不进入正式SDK窗口或产品编译时间，嵌套父子耗时不重复相加。打包时的1154项timings快照与后续公开验收操作ledger分别保留，不反填该快照之后的操作或推造未知阶段耗时。

## 5. 交付完成范围

主矩阵、资源专项、环境恢复、本地取证、结果/协议核验以及公开核心页面验收均已完成。报告地址为 [统一 TCP 实验报告](https://yche.me/perf/kvc-tcp-20261020/index.html)。缺少的记录干扰排除证据仍为unknown，20个主窗口CPU/coverage、2个窗口内存和不完整三重复资源中位仍保持null；交付完成不把这些值改为0。验收计数、工程失败历史和文档刷新边界见第12节。

## 6. 第六轮三产品协议门禁通过

`protocol-gate-001/summary.json` 的 `valid=true`，UTC 2026-10-19 20:38:33.764941714 至 20:39:21.148347710，同进程 monotonic 耗时 47.383406466s。DataSystem、Mooncake、LMCache 各两客户端均完成 fresh PUT、GET 完整字节及 EXIST hit/miss；逐客户端直接 TCP syscall 成功字节均超过 8 MiB 门槛，DataSystem 附加六组逐接口实际传输证据通过。结束时四个实验 cgroup 均为空。

前五轮保持原始 `valid=false`，完整保存在 `formal-tcp-001/protocol-history/attempt-0001` 至 `attempt-0005`。第五次修订只修正 staged launcher 的执行位：`changes=[]`，源码 SHA 不变；权限在全部复制后生效，原始 stage 文件未改写。本门禁含 strace，仅验证正确性和协议，不提供 QPS/P99 成绩。随后共同编排 smoke 的 6 cell、18 结果均通过，UTC 2026-10-19 20:41:39 结束；正式矩阵另见下节。

## 7. 主矩阵完成与原始计数

本地已核对 `formal-tcp-001/summary.json`：开始 UTC 2026-10-19 20:41:54.554432，结束 22:42:19.371899，81 cell、216 标准接口 JSON，`failed=[]`、无 `fatal`。UTC 起止差为 7224.817467 秒（120.41362445 分钟），包括准备、服务生命周期、接口命令及收尾；不等于 SDK 测量窗口总和。主任务在主矩阵收尾确认四个实验 cgroup 均空后，启动九格 PUT 资源专项。

本地 `formal-run.log` 另核对到 81 次 `cell_start`、81 次 `cell_complete`、216 次唯一 `(cell, operation)` 的 `measure_complete` 和一次 `suite_complete(completed=81, failed=0)`，未见重复完成身份、`cell_failed` 或 `suite_fatal`。日志 SHA256 为 `27fb23f6706e1ee867009ee426186dff9e1451156943c5143bfcc399f11e9a55`，位置 `/home/t14s/workspace/scratch/2026-10-20-kvc-tcp/formal-run.log`。

下表仅汇总该本地日志的 `success` 计数。各产品分别有 PUT 27、GET 27、EXIST hit 9、EXIST miss 9 个完成窗口，共 72 个接口结果。miss 的 success 表示正确返回不存在，不表示命中。

| 产品 | PUT 成功请求 | GET 成功请求 | EXIST hit 成功请求 | EXIST miss 成功请求 |
| --- | ---: | ---: | ---: | ---: |
| DataSystem | 903168 | 8469904 | 7137995 | 6122708 |
| Mooncake | 903168 | 11411742 | 18936237 | 19137084 |
| LMCache | 903168 | 263541 | 246622 | 360436 |

上述事件计数与全部原始结果交叉核对一致；独立统计及其边界见第9、10节。资源专项不加入主三重复，公开核心页面已通过第12节的严格验收。已存在的阶段提交包括 `966b74a`、`96fc52b`，详细清单见 tcp-acceptance.md。

## 8. 九格 PUT 资源专项执行完成

主任务读取的专项 summary 回报：UTC 2026-10-19 22:42:44.441697 至 22:46:17.881811，UTC 边界墙钟 213.440114 秒；9/9 用例成功，`valid=true`、`failed=[]`、`monitor_exit=0`。监控汇总 21192 个样本、0 次读取错误，9 个 PUT 测量窗口均有资源样本覆盖，单窗最少 58 个样本。专项结束 drain 确认四个实验 cgroup 均空；后续环境恢复和取证由主任务执行。

原始采样与窗口已独立重算一致，覆盖率为 98.3341%–99.9784%。实际周期 p50=9.999999 ms、p95=10.001185 ms、最大 40.048335 ms；读取/组装成本 p50=2.302651 ms，包含 JSON/写入的循环工作 p50=2.607074 ms。monitor cgroup 首末 CPU 差分折合平均约 0.260655 核，不能称为零采集开销，也不能把名义 10 ms 当成每个实际周期。专项资源值与主三重复吞吐/P99 分开，不把 9 个专项结果作为额外主性能重复。

## 9. 独立原始结果、协议和资源核验

本地重新读取 216 个主结果与 9 个专项，核对精确 workload 身份、客户端集合与成功/错误计数，逐桶重建每个 Run 的客户端合并直方图，再重算 P50/P95/P99 桶界、测量时长、QPS 和 MiB/s。216 个主结果全部有效，累计成功 74,795,773 次、接口失败 0；72 个固定接口/尺寸/并发/产品分组均恰有三次重复。PUT 后验抽验共 2106 次、GET 内容抽验 20011 次；这是抽验数量，不宣称逐个成功请求都做了内容比对。

LMCache 负责人的独立审计另外核对 223 个核心 JSON 前后 SHA 未变、1586 个失败预检归档文件的 SHA/尺寸、25 项执行输入和 8 个原生制品。六个客户端 TCP probe 的成功 socket 字节均超过各自 8 MiB 门槛，DataSystem 六组逐尺寸 probe 通过；`verify_tcp` 为 7/7。本证据覆盖所捕获 probe 和已保存配置/快照，不代表正式压测请求全程运行 strace，也不扩大为 worker 间对象传输或所有时刻无 SHM 的证明。

| 检查项 | 实际结果与保留边界 |
| --- | --- |
| `verify_results` | passed=true、216/216；`evidence_complete=false`，`recorded_interference_excluded` 为 unknown，因为未提供 `interference-audit.json`；不能宣称排除了未记录负载 |
| 主资源采样 | 7217 点，无解析错误或 counter reset；20 个 PUT 窗口不足两点，CPU/覆盖率与窗口事件增量为 null；其中 2 个完全没有窗口内存点 |
| 三重复资源汇总 | 8 组 CPU/覆盖率中位、2 组内存中位缺完整三次数据，保持 null；54 个 EXIST MiB/s 为接口不适用，并非 QPS 缺失 |
| 长窗口覆盖 | GET 约 90.0454%–95.1013%，EXIST 约 95.0658%–95.1025%；资源均值基于窗口内首末采样点，内存为采样峰值，均不冒充连续窗口精确极值 |
| 内存压力 | server/client 的整阶段首末 `oom/oom_kill/max/high` 增量均为 0；不是把历史累计值当作本轮值，也不替代短缺样窗口的事件证据 |
| CPU 限流 | 主阶段 client `throttled_usec` 增量 358628，专项为 12010；server 两阶段均为 0。是整阶段 cgroup 计数，不能分摊为每次请求延迟 |
| 原生工具 | iostat/mpstat/pidstat/dstat 退出 0；perf 以 SIGINT 收尾，退出 -2，保留原始日志，不将此表述为全部硬件计数必然可用 |

完整本地独立审计位于 `independent-audit-lmcache/20261019T225016.729006Z/audit-summary.json`；另一份直接从 raw JSON 和原始 cgroup 采样独立计算的交叉复核位于 `independent-datasystem-analysis/independent-analysis.json`。两者在主结果、三重复统计和资源缺值计数上一致。复现仍使用已保存固定参数及 canonical 验证/报告脚本；没有重跑产品或修改测量输入。

## 10. 本次统一 TCP 的主要比较

只比较本机回环 TCP、相同资源配额和 CPU bytes API。24 个共同 workload 条件的三重复 QPS 中位均为 Mooncake > DataSystem > LMCache；这仅陈述本轮观测，不外推到跨机、GPU 或推理。下面展示并发 8 的三重复 MiB/s 中位，完整 2/8/16 并发与单次散点仍应在报告中同时展示。

| 接口/对象 | DataSystem MiB/s | Mooncake MiB/s | LMCache MiB/s |
| --- | ---: | ---: | ---: |
| PUT / 1 KiB | 15.21 | 28.47 | 1.38 |
| PUT / 64 KiB | 675.35 | 1345.66 | 83.00 |
| PUT / 1 MiB | 2036.15 | 3473.83 | 590.38 |
| GET / 1 KiB | 30.72 | 37.62 | 0.55 |
| GET / 64 KiB | 1497.08 | 1710.20 | 40.39 |
| GET / 1 MiB | 2553.78 | 3526.78 | 436.14 |

并发 8 的 EXIST hit QPS 中位分别为 DataSystem 47602.62、Mooncake 110039.35、LMCache 1866.42；miss 为 41250.95、113261.66、2701.78。EXIST 只查询元数据，不能用对象字节数生成带宽成绩。

吞吐与尾延迟有取舍：1 MiB GET、并发 8，DataSystem 的 Run P99 上界中位为 4063.232 μs，Mooncake 为 7012.352 μs，LMCache 为 27000.832 μs；因此 Mooncake 的吞吐领先不能写成所有尾延迟也领先。这里的 P99 先在每个 Run 内合并客户端桶，再对三次上界取中位，不是跨重复请求池 P99。全部分组最大的 QPS `(max-min)/median` 为 19.9477%（DataSystem EXIST miss、并发 2），三次重复不足以给出普遍显著性或稳定性结论。

产品与 cell 在每次重复分别按固定 seed 随机打乱；实际产品顺序为 r0 DataSystem→LMCache→Mooncake，r1/r2 DataSystem→Mooncake→LMCache。随机结果未实现位置完全平衡，DataSystem 三轮均首位，不能称为 Latin-square 或已排除时序漂移；这些数值是固定资源下三次随机重复的观测。

以下是独立 1 MiB PUT、并发 8 资源专项的单次观测；CPU 在窗口内首末点间平均，内存先按 server+client 相加再取窗口采样峰值，各列峰值未必发生在同一时刻。

| 产品 | server CPU 核 | client CPU 核 | 匿名+SHM 峰值 GiB | 总计费峰值 GiB |
| --- | ---: | ---: | ---: | ---: |
| DataSystem | 5.335 | 3.163 | 3.444 | 3.979 |
| Mooncake | 1.931 | 6.941 | 2.611 | 3.130 |
| LMCache | 2.086 | 2.308 | 10.113 | 10.637 |

这些是服务池、SDK 和工作集共同成本，不是净对象载荷或内存放大率。LMCache 非 SHM pool 启动时触页且 pickle/tensor 构造包含在接口成本中；产品分配方式不同，不能仅据峰值推断对象存储效率。DataSystem Worker 内部池可以被计作 shmem，TCP 要求禁止的是客户端与服务端以共享映射传值；`memory.current` 已含其计费，不再与 `file/shmem` 叠加。匿名+SHM 与总计费两条曲线应分别解释。

主矩阵有 57/81 个 PUT 窗口短于 10 秒，实际固定总量为 1 KiB 时 64 MiB、64 KiB/1 MiB 时 2 GiB；不能称为全部长稳态写入。PUT→GET→EXIST 的既定顺序和前序 PUT 背景保持不变。设备 `253:2` 的主阶段 server/client 首末写入增量约 19.012/11.801 MiB，专项约 1.697/0.395 MiB；关闭 payload 持久化不等于系统零磁盘 I/O，日志、元数据及延迟写回也有成本。此为单个设备 cgroup 计数，既不与底层设备重复相加，也不推断为持久化吞吐。

## 11. 恢复与归档

本地 `formal-tcp-001/environment-restored.json` 记录 UTC 2026-10-19 22:48:11.976953→22:48:12.200926，记录区间的 monotonic 墙钟为 0.22397715 秒；`systemctl start setroubleshootd.service` 退出 0，命令耗时 0.012424898 秒，后续观察为 active，`errors=[]`，四个实验 cgroup 均空。这是结束时观察，不保证未来持续状态，也没有据此删除源码、原始归档或共享内存数据。

原始证据 tar.gz 已独立比对 4384/4384 个普通文件的 SHA 与尺寸，gzip EOF/CRC 通过，未发现遗漏、额外、不安全路径或重复成员；压缩包为 94,315,457 字节，SHA256 `5d7c67c37dcae056a6b18651c577cd107a8c8397b25f468cedae0e8bd849ea30`。包含 current 协议 449 文件、历史协议 1586 文件，以及正式/专项结果与监控等证据；原始日志压缩包不进入 Git 提交。

复现 ZIP 经过清洁解压，88个成员、47个Python文件编译、15项CLI、14个bash语法与74项测试通过。后续文档、操作ledger和ZIP刷新继续同步摘要；核心HTML/JS/CSS/数据、原始结果与证明不因收尾文档而替换。网站Git提交以实际仓库记录为准，本文件不编造尚未产生的哈希。

## 12. 本地与公开浏览器验收及工程历史

公开页面：[https://yche.me/perf/kvc-tcp-20261020/index.html](https://yche.me/perf/kvc-tcp-20261020/index.html)。本地检查实际为405/405（1440/900/390px分别137/134/134），耗时115.821344770秒、退出0。最终严格公开v3为408/408（138/135/135），耗时125.743963344秒、退出0；225个主结果/资源专项原始JSON绑定均通过，`failed=[]`、`errors=[]`，各视口pageErrors与failedRequests均为空。公开多出的3项为各视口的原生基线公开链接检查，不借用原生页面此前的369项验收。

| 交付验收步骤 | 实际结果 | 同进程命令墙钟/退出状态 |
| --- | --- | --- |
| 公开v1 | 405/408通过；3项失败均为原生HTML的HEAD返回200但无Content-Length，被脚本错误要求长度大于0；当前TCP页面、225个原始结果绑定及大tar检查通过 | 131.612607360秒，退出1 |
| 公开v2 | 弱版回退启动后加强身份要求，仅终止测试者自有Node浏览器进程，保留interruption和ledger；未完成全量验收，不能计为通过 | 73.308238423秒，退出1 |
| 最终公开v3 | HTML的HEAD缺长度时GET并严格核对原生页面完整标题身份；大tar仍要求HEAD正长度；408项与225绑定全部通过 | 125.743963344秒，退出0 |
| 页头/首页审计首轮 | 脚本误期待“发布时间/修改时间”，与现网实际“发布/修订”标签不同，保留原断言失败 | 8.100055279秒，退出1 |
| 页头/首页审计重试 | 3/3通过：发布/修订均为2026-10-20；首页287条记录中TCP精确1条、原生精确1条，标题/日期与metadata一致 | 10.960354532秒，退出0 |

两次验收脚本误判失败和一次因强化规则而主动中断都属于交付验收工程过程，不是站点/产品接口缺陷；v2没有中断目标机或实验服务。它们不加入前五轮协议预检失败，也不改变正式接口失败0。各轮原脚本、JSON、interruption与操作ledger保留，不覆盖原失败。

公开必需HTTP资源/请求失败为0，但并非所有HTTP均为200：每个视口的 `https://yche.me/site-api/admin/pages` 返回401，明确分类为匿名用户管理metadata被拒绝的 `optional-site-feature`，报告内容无需该接口。没有泛化忽略其他失败请求。

左侧导航、6个编号章节、8张ECharts图、下方居中caption、筛选/重置、resize、移动端表格访问、图表与实际数据、下载/协议证明绑定均通过实际页面检查。日期与首页的额外3项独立于408项，单列记录。报告首次文档刷新已确认254个核心文件字节及mode不变；最终文档/operations/ZIP刷新与摘要校验继续按同一边界执行，不重跑或改写已验核心。工作台已存在 `966b74a`（紧凑数据绑定完整TCP证明）、`96fc52b`（已验主矩阵/资源与阶段耗时文档），网站最终提交哈希不预填。
