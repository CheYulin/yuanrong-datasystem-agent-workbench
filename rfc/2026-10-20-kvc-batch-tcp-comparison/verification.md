# 5. 阶段验证记录

当前正式证据矩阵已完成：r7独立重算132有效、18原生失败、30 N/A、0issues；9组/36epoch齐全，evidence_verified=true而passed=false。部署和资源审计已完成，交付浏览器/公开下载验收进行中。历史失败原样保留，不把短窗口通过当作原生问题已修复。

## 5.1 运行前自检

- 请求匹配：每次 API 调用 B=1/16/256/4096，原生 batch 与 N/A 分开；1KiB、C8、三轮、TCP、两服务。
- 离线机制验证：`python3 -m unittest discover -s scripts/experiments/kvc-fair -p 'test_batch_*.py'`，78 项通过（0.558s）。覆盖三适配器、18 个 gate fixture 与 harness 的私有工作集、fresh key 唯一性、upsert 恢复读回、失效 baseline 拒绝、监控进程部分启动失败等。
- `python3 -m py_compile scripts/experiments/kvc-fair/batch_*.py` 通过；`git diff --check` 与新文件 no-index whitespace review 无问题。
- 已修复准备期发现的 harness 风险：LM callable metadata；完整恢复读回；失败子进程升级 kill；服务/监控 finally 清理；SIGTERM 清理；baseline 完整 81 格身份与 gate 同输入绑定。
- 热路径：没有产品代码变动。适配器 O(B) guard 计入延迟；请求列表构造和抽验计入端到端吞吐但在 API 延迟边界外；正式采样额外开销隔离到 monitor cgroup。
- 并发：8 客户端私有 key，共享双服务；SDK 同步完成后才计成功；LM 锁 finally 释放并等 ACK，异常不当 miss。
- 持久化/恢复：无产品行为改动；只使用本任务 namespace，PID/start-ticks/cmdline 身份保护原样保留；不宣称此性能测试证明容灾能力。
- `.repo_context`：本任务不修改产品职责、结构或构建/测试路径，无对应模块上下文变更。
- **尚未证明**：离线 fake SDK 不能证明部署版支持 4096 或实际 TCP/完整字节；须以目标机 gate、smoke 和正式结果独立验收。

## 5.2 已保留的准备期失败

第一次全量 harness fixture 执行出现一处 fixture 缺 baseline plan、另一处 fake clock 恰好结束于原始字节版本，无法触发预期的损坏恢复；均为测试夹具缺陷，补齐81格plan并调整模拟时钟后通过。另一次命令最后的 `rg huge` 无匹配退出1，前置 unittest/compile 已通过；删除未经实际 runtime 证实的文档 flag 表述。这些失败不属于目标机实验数据。

第一版 stage 在尚未 prepare、启动服务或测试前完成，随后只读审查发现 symlink parent 目录约束需要物理路径判断，并将 gate strace 改为分线程 hex 输出以支持新 batch TCP 载荷证明。第一版目录原样保留，正式环境改用全新 `-r2` 根目录，重新记录 staging 命令和构建物 SHA，不修改已冻结输入。

## 5.3 真实门禁与后续自检

- 准备：完整硬件/进程/磁盘快照0.129320229s；无旧tmpfs待清理项，保留/删除目录数0。cgroup仍为server/client各8CPU/12GiB、monitor2CPU/1GiB、swap0。
- gate1真实失败：50.821004702s，DS四客户端/模式均通过，Mooncase B4096 GET中10项为空；结束四cgroup排空。
- 无跟踪独立诊断6/6通过：73.304707536s；没有调TCP queue参数；源码队列解释是推断。
- gate2为证据组合，不是新测量：2.118175008s。
- 针对运行/门禁/协议审计/组合脚本的107项离线机制检查通过（2.583s）；QA脚本Node语法检查通过。报告分析器仍在独立审查，未将其草稿fixture当作此运行验收。
- DS实际TCP审计：16组合中12满足指定key0前缀+字节量；4个B4096 GET均达到至少4MiB直接TCP成功读取，但-s2048截取未见指定key0前缀。此证据暂不完整，不把它改写成4个数据正确性失败；原门禁完整bytes/顺序已通过，保留原审计结果及范围。

## 5.4 默认 C8 smoke 与容量适配决策

默认r2 smoke在199.051957721s后终止：DS完整通过，Mooncake B4096首次PUT通过，但UPSERT在预热报TRANSFER_FAIL(-800)，未进入该op正式SDK窗口；两client失败具体keys在pool尾部。原始日志/JSON完整保留，未把失败当P99或吞吐零。四cgroup排空后恢复先前暂停的setroubleshootd，恢复操作0.219884131s、无错误、观察为active；随后新r3会重新准备并记录。

容量适配仅增大Mooncake每peer queued/pending为8192，lane数、admission timeout、构建物、资源、客户端数不变。必须先通过新profile的原生门禁及C8 smoke后才开始主矩阵；不能把旧r2成功部分直接充当r3重复。具体QUEUE_FULL归因仍待warning诊断，不由尾部failed indices推断为已证明。

## 5.5 容量机制诊断与严格 TCP 补验

同冻结r2输入的queue-capacity-diagnostic-001共96.501955708s，default native_valid=false、queue8192 native_valid=true；两组均完成、清理成功。仅客户端声明GLOG1，旧server env.sh仍可设置2；未观察到明确QUEUE_FULL/rejection日志，不能把generic TRANSFER_FAIL直接写成已定位的queue-full。首版实际probe源码SHA为ec0b735a63c6f44d9125132b3d2ed19f8bbd1c0714179fb05ff6aa0dd3b405d4，原件在probe-source.py；canonical8e9后补静态controls与目录复用保护，后续复现使用，不能冒称本次执行源码。

严格TCP离线补验以同case/version0任一完整1KiB payload、正确方向/endpoint/窗口、正返回实际字节范围和整批TCP字节下限为合同，16/16组合（32方向）通过，耗时15.667086592s。旧key0未捕获审计原件不改。新审计SHA0fa183e20cec830a0b1b0b54378786379864ad28e65b130c97a5ffcb49e84a12；不扩大为整批每字节均被跟踪打印证明。

## 5.6 r3 正式执行失败与双 Worker 元数据目录冲突

r3 的真实 gate、smoke 和批量 TCP 审计均通过，具体记录见 [validation.md](validation.md)；它们不证明长窗口不会失败。`formal-batch-001/summary.json` 记录正式尝试于2026-10-20 04:32:12.385088 UTC开始，04:35:46.347239结束，耗时213.962156825秒，`valid=false`、未完成任何整产品组。已产生的8个操作JSON有效，第9个 DataSystem B16 EXIST hit 失败；完整尝试和原件保留，不补成三重复结果。

失败操作测量6.407137609秒，8客户端合计120,419个成功batch后各出现1次 `RPC deadline exceeded`，`worker_rpc_client.cpp:44` 的剩余时间为-121至-234微秒。该错误发生在本地发起RPC前的deadline检查，不能仅凭错误名判断Worker实际等待了10秒或推断队列拥塞。固定rc44的 `object_client_impl.cpp:4915` 每次Exist均建立 `ApiDeadlineGuard(requestTimeoutMs_)`；当前没有证据证明公开入口漏reset，因此不将其定性为产品deadline缺陷。

采集到的 `run/run-r0-datasystem/worker0.stdout.log` 有480,625条Rocks错误，worker1有493,154条，共973,779条（仅这两份stdout中的匹配记录，不重复累计glog副本）。两个不同Worker均访问 `/root/datasystem/rocksdb/object_metadata/000029`：worker0报`.ldb`缺失，worker1报`.sst`预期24,802,172 bytes、实际0。首批错误04:34:37.625/38.293 UTC即已出现，早于EXIST测量失败。

两份实际 `wN/worker.flags` 都未配置 `rocksdb_store_dir`。固定rc44归档源码的 `data_worker.cpp:192–206` 使用同一FLAGS目录做pre-init，`worker_oc_server.cpp:2560–2564` 将该目录直接传入metadata manager。由实际错误路径与启动参数可确认本次两个Worker未隔离元数据目录；这足以否定“每次fresh run连同元数据都独立”的前提。是否它单独解释全部deadline错误，尚须隔离后复核，不扩大根因归属。

最小修复仅在启动脚本给每个Worker显式配置 `<fresh run>/wN/rocksdb`，路径写入manifest，拒绝共享/越界/symlink和旧目录重放；不改产品、不调timeout、不关闭原有元数据写入模式。11项本地回归已验证实际mock launch argv/flagfile/manifest绑定、独立目录与拒绝路径，另用真实sleep进程验证PID/start-ticks保护仍有效。修正配置须使用全新根目录重新gate、C8 smoke和全矩阵，不能将r3前8个成功操作补入新重复。

## 5.7 旧单 key 基线的影响边界

本地保存的native `formal-001/build-and-script-evidence/scripts/datasystem/runtime.py`（SHA256 `78164d670aa604e4557964502bd4f2519d5a824effe40ea894a6de635905b78d`）与TCP `formal-tcp-001` 对应文件（`2f8781437faf4fe90a288062b5e102645766077bf3b6b8d5c25c6d5aa871f459`）同样没有显式RocksDB目录；两份products启动命令也未补该参数。因此旧DS结果不能作为独立元数据目录的公平基线，不应用它们与修正后的batch结果计算收益。

旧成功请求、字节校验、采样和公开页面验收仍是当时实际执行证据，原始结果不改写为零或全部失败；但缺少每格Worker stdout的历史原件，无法从现有本地材料量化旧共享目录的污染范围、偏差方向或幅度。新比较必须明确披露这一限制，并以修正后的同配置B1作为批量大小的基线。

## 5.8 r4隔离目录后的通过预检

r4新根目录实际完成gate145.847667354秒、C8 smoke277.512075568秒、严格TCP审计9.914668323秒，均valid；TCP仍为16组合/32方向的限定证明。环境恢复原件记录0.217329077秒、`errors=[]`、setroubleshootd观察为active，四cgroup为空。其原件保留于r5的`history/protocol-preflight-r4`，SHA见 [validation.md](validation.md) 第6.7节。

r4没有执行正式矩阵，不能列为“又一次formal失败”或新增性能重复。它证明目录隔离配置在该gate/smoke步骤可工作，但原协议的读阶段仍继承不同PUT背景，PUT warm namespace还随B增长。因此在formal开始前切换r5新协议，未覆盖r4冻结输入或改写其通过事实。

## 5.9 r5独立read与fresh PUT协议

阶段提交`2483b37`固定协议工具；新stage记录111项执行输入，目标根`/home/worktrees/kvc-batch-tcp-20261020-r5`。三方固定构建、双服务、C8、TCP与资源配额沿用；只更正测试编排与输入背景，不修改产品或增加timeout。

- 每产品/重复先启动一个read epoch，只预置65,536 keys / 64 MiB，完成全部B的READ/UPSERT/EXIST；UPSERT的真实更新及恢复读回仍在原合同内。
- 随后每个支持B启动独立fresh PUT epoch，预置相同池，统一预热32,768新keys（4,096/client），正式前同为98,304 resident keys / 96 MiB。正式fresh量为262,144 keys / 256 MiB，smoke为32,768 keys / 32 MiB。
- LM无等价原生能力的PUT/GET/upsert仍N/A；不执行无支持B的服务epoch，不通过scalar padding补背景。
- 正式设计36个epoch=9 read+27 fresh PUT、150有效接口格+30 N/A；smoke设计12个epoch。以上是期望覆盖量，待实际summary的epochs_expected/completed和原始180份结果验证，不能预填成功。
- DataSystem gate1+smoke5+正式15，设计21次独立集群启动/42个Worker。离线部署审计从实际plan推导路径并核manifest/flags/启动argv、各epoch启停与drain、错误日志全SHA；只有gate有额外procfs live观察，其余20次如实为launch-only/live-unobserved，不声明全窗口持续实读。

实际r5阶段结果见5.10。原19项离线部署审计fixture属于后处理逻辑验证，不是产品运行计数；随后pure-KV后处理防护新增到24项。

## 5.10 r5实际资格结果、离线parser修正与正式失败

r5原生gate147.334149718秒通过（summary SHA `99361a8baaec0bc3a208a0f2baadf82bf24ac0184744284124e9aa0e1dff7431`）；C8 smoke534.366434989秒通过，12个epoch齐全、50支持结果与10 N/A、0接口失败（SHA `aa6d2383559fabb2d79139065990cefec2932cfcff16a364c102209749977027`）。这些只证明实际短窗口，不认证正式持续运行。

首次离线TCP审计14/16：client1 insert/upsert的B1 GET均实际成功读取1,237 TCP bytes，但完整1KiB payload分布在同一次`readv`的相邻两个iovec，旧parser只在单个iovec查找，因此未匹配。真实insert行`wire-client-1-insert/strace.2008965:708`完整payload于syscall偏移165开始、跨30+994 bytes；upsert行`wire-client-1-upsert/strace.2009043:707`跨34+990 bytes。它们是完整SDK bytes通过后的观察解析缺口，不是GET结果错误。

新parser只连接同一正返回syscall中相邻、实际成功且完整打印的iovec前缀，截断/未知指针/未成功字节均为屏障，不跨syscall、方向、端点或case/version。27项离线fixtures通过；原失败JSON SHA `631c4dd023286de38303d340db1116bbf426a7b0a9f0fe3d2f4f9c31f76f5ade`未覆盖。新`datasystem-batch-tcp-verification-v2.json`为16/16组合、32方向通过，解析28.440561944秒，SHA `7f233fa749786a3d2397f26741937a3a6341b4d6ac2fb677fa403c6dc3ac5589`；执行parser SHA `57a1921a91723410ec91cbe019fd4447913125931ba83b8d9160c5f3388cdbca`及snapshot/receipt另存，未修改目标冻结输入。

r5正式尝试05:16:59.135590–05:20:06.445844 UTC，187.310265231秒后终止，整组和epoch完成数均0。B16 EXIST hit测量6.407281222秒，8客户端合计121,709个成功batch后各报1次deadline错误，剩余预算为负百余微秒；测量不是20秒完成结果，不能用作有效三重复或把后续未测量项目填零。此前已修正两个Worker各自独立DB目录，本次两份worker stdout均0 bytes；这排除了本次已采集stdout出现上一轮同样SST错误的说法，但不把R3共享目录定为其全部deadline失败的唯一原因。恢复记录0.2119152秒、`errors=[]`。

## 5.11 固定rc44的deadline传播审查

以下均来自父任务以`git archive 2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6 src/datasystem`保存的固定源码，非当前main。静态机制与现象吻合，尚未动态读取线程TLS，不能写成已完成产品修复。

1. `client/object_cache/object_client_impl.cpp:4915`每次公开EXIST在调用线程建立`ApiDeadlineGuard(requestTimeoutMs_)`；因此公开入口有guard，不能据失败行断言入口漏reset。
2. 同文件690–693硬编码8个`async_get_rpc`线程；709–711把池交给TransportLayer；`transport/transport_layer.cpp:395–397`再交ReplicaReader和ObjectReadFlow。
3. `transport/object_read/replica_reader.cpp:538–549`及`object_read_flow.cpp:103–119`在GET派发时捕获剩余预算，并在池任务内调用`InitTimeoutsFromDispatch`。`common/rpc/api_deadline_helpers.cpp:45–46`同时初始化该执行上下文的`reqTimeoutDuration`和`ApiDeadline`，上述GET lambda没有Push/Pop或Reset恢复。
4. `routed_mode.cpp:1101`的EXIST同样使用该池。`exist_handler.cpp:381–388`在多owner时进入并行；408–410的任务没有初始化/恢复deadline或安装`ScopedRequestContext`。`transport/rpc/worker_rpc_client.cpp:40–44`在发RPC前读取当前deadline，为负即在本地返回本次相同错误。
5. `common/rpc/api_deadline.cpp:29–36`无活跃请求时使用thread_local fallback，39–57设置并持续扣减绝对时间；`thread_pool.h:71–95`和`.cpp:56–105`入队/运行任务，不额外清理deadline。相邻安全实现`object_client_impl.cpp:3380–3382`在另一路批量GET任务明确Push+RAII Pop后初始化，提供现有局部参考模式。

benchmark每个非PUT进程ready前都做GET完整bytes抽验，随后预热3秒与测量；GET可能把约10秒预算留在后续EXIST复用的线程中。B1通常只有一个owner组、在调用线程执行；B16可进入并行。当前源码中池线程数和该分支均无已发现的官方关闭选项；不能删除preGET、改成单owner key、改成本地缓存或增大timeout掩盖。独立诊断用fresh SDK对照EXIST-only、GET→EXIST、GET→sleep10.1→B16/B1，逐调用保留时间与异常，20calls/s仅用于区分状态与高负载，绝不作为性能排名。

## 5.12 纯内存KV TCP候选配置与依赖边界

用户明确不需要RocksDB与stream。启动脚本新增显式`--profile pure-kv`，默认`baseline`不改变既有flag字节；新stage与旧冻结目录独立，产品二进制不重建、不修改。

| 特性 | rc44默认/此前实际值 | 候选值 | 固定源码与意义 |
|---|---|---|---|
| RocksDB元数据落盘 | 默认async；r5未显式覆盖 | `rocksdb_write_mode=none` | `common/kvstore/rocksdb/rocks_store.cpp:52,131–145,228–242`：none跳过DB::Open、async写池及写入；`worker/data_worker.cpp:190–208`跳过pre-init |
| stream服务 | regular/stream socket各16 | 两个`sc_*_socket_num=0` | `common/metrics/res_metric_collector.cpp:39–44`；`worker/worker_oc_server.cpp:567–569`及796/818/842/926、1022/1118条件跳过构造/初始化 |
| legacy/JSON指标 | log_monitor已false，json默认true | 两者false | `common/flags/common_flag_define.cpp:249–251`；`metrics.cpp:379,400`、`object_client_impl.cpp:4727`要求两者false才跳过指标工作 |
| 强制性能trace | 默认false，但可被环境开启 | 显式false | `common/log/logging.cpp:66,141`；SDK环境确切名`DATASYSTEM_PERF_TRACE_LOG=false` |
| request/access采样 | 各1.0 | 各0 | `common/log/logging.cpp:144–157`；诊断采样仍默认1，minloglevel仍2，保留ERROR/FATAL |
| L2/spill/本地缓存/高速卡 | 已none/空+0/false/false | 保持原值 | 原adapter `NONE_L2_CACHE`、`enable_local_cache=False`，worker `l2_cache_type=none`、spill0、RDMA/URMAfalse、IPC共享传输false |
| jemalloc HTTP统计/事件 | 默认false/空disabled | 保持 | `worker/worker_flags.cpp:31`、`common_flag_define.cpp:312`，没有无依据新增开关 |
| slowlog | process2000us、RPC5000us | 保持阈值与ERROR策略 | `common_flag_define.cpp:321–325,369–373`，`common/log/log.h:67–105`仍先检查severity；不以取消错误日志制造低成本假象 |

SDK已确认支持`DATASYSTEM_LOG_MONITOR_ENABLE=false`、`DATASYSTEM_PERF_TRACE_LOG=false`、`DATASYSTEM_LOG_V=0`、`DATASYSTEM_MIN_LOG_LEVEL=2`（`logging.h:48,56–58`、`logging.cpp:414–425,455–456`）。未发现`DATASYSTEM_JSON_LOG_MONITOR_ENABLE`或sampler环境读取；固定Python构造也未暴露其参数，不能把无效env写成客户端已全关闭。客户端JSON指标残余成本需如实列明。

KV内存元数据、hash ring、服务发现、必需RPC和心跳仍保留；etcd及其控制数据/log依然计入服务cgroup。关闭RocksDB降低元数据持久化/重启恢复能力，本实验不测试这些能力。`worker_oc_server.cpp:1088`仍将独立目录用于eviction_policy状态，日志/ready文件也可存在，故只要求无RocksDB DB/MANIFEST/SST/WAL等制品，不能说整个目录无文件或全进程零磁盘IO。

`RocksStore::GetInstance:118–120`在none判断前仍会执行`InitRocksOptions`，其中79–80调用`options.IncreaseParallelism`；因此源码只能确认跳过DB::Open和该store的异步写池，不能静态宣称Rocks库初始化或所有环境线程成本为零。实际残余线程/FD及资源消耗以采集为准，不另改产品或调整线程参数。

r5实际原flags完整保存在`run/run-r0-datasystem-read/w0/worker.flags`与w1对应文件，SHA依次`3b70953b416df4fea2b039317da76f5bbea1dd5b05ada901bc31aa0a4a2c0f8a`、`e15e16eb7821ee03ffcabe6f24f577e48fe29b7aa7f879674b017ed16b757afc`；worker地址/日志/ready/UDS/独立DB路径和etcd配置全部保留，未覆盖。候选runtime通过14项启动边界fixture，独立诊断8项、纯KV部署审计24项均为本地机制检查；r6实际诊断见5.13。

## 5.13 r6纯KV真实deadline对照与独立复核

本地完整原件位于`/home/t14s/workspace/scratch/2026-10-20-kvc-batch-tcp-r6/deadline-diagnostic-001`。四场景均fresh SDK进程、client index0、相同16个key及逐key确定性1KiB payload、原10,000ms请求超时、20calls/s限速；两个Worker同时存在，沿用TCP与同配额。D也先GET16，再用其中一个key作EXIST1对照。此次不是C8性能实验，不贡献正式重复、吞吐或P99排名。

| 场景 | 实际结果 | EXIST调用与观察窗口 | 观察到的EXIST窗口正TCP发送 |
|---|---|---|---|
| A：EXIST-only | 通过 | 248/248成功，13.027326933s | 31531端249行、31532端248行 |
| B：GET16→连续EXIST16 | 失败如实保留 | 190成功后1失败，10.019303674s；line44，remaining -4398us | 两端各190行 |
| C：GET16→sleep10.1→EXIST16 | 失败如实保留 | sleep10.100258905s后唯一调用0.021342848s报line44，remaining -106448us | 0行；失败在本次已记录窗口内没有实际正TCPsend |
| D：GET16→sleep10.1→EXIST1 | 通过 | sleep10.100222394s后唯一调用0.001553996s成功 | 31531端1行 |

TCP计数是成功syscall行，含RPC之外可能的控制活动，不能当精确RPC数，也不证明每个batch逐个调用了两个owner。独立解析包括unfinished/resumed匹配与操作起止wall窗口，未发现unmatched resumed记录；C确实没有窗口内正发送，并非只相信helper摘要。结合固定源码中的本地发RPC前line44检查、GET初始化共享池deadline且EXIST未刷新，以及B/C约10秒失效和D单key对照，证据强支持旧deadline跨任务残留；未用调试器读取TLS中的`deadline_`或原生调用栈，故仍明确不是直接TLS内存观测，不能宣称产品已修复。

SDK阶段46.339531674s、`diagnostic_complete=true`而`all_sdk_steps_passed=false`；外层63.659922266s，`errors=[]`、monitor退出0、最终4个cgroup空。四case均记录close成功且子进程reaped；client socket快照为A 0→3→0、B/C/D 0→2→0，maps未见`/dev/shm`/memfd/DB映射。这里只证明保存时刻，不扩展为持续FD泄漏检测。worker保存的PID/ticks/live argv与manifest/flagfile SHA逐一绑定，明确Rocks none、stream两个0及TCP关闭SHM/RDMA/URMA；每Worker最终仅有26-byte `eviction_policy/worker-state.pb`（SHA `28378cc50bf6e71a37bf3c032b577dfd22425a864bfe9f8c63ee90103a2fe47f`），无RocksDB制品。

独立本地审查对SDK清单158份制品及额外flags/manifest等共165份输入逐SHA/size检查并前后重核，221项检查通过；记录`deadline-independent-audit-datasystem/summary.json`。原SDK summary SHA `cd9cfe4c24afd43bc42ecef244588f4a1d26a6f2d24cbc5087bf6575ce5d81dc`；外层summary SHA `bb8fdaf0e003f88e15abfa4ac2a9085ed8c25e781051335e4ba103078a03044d`。实际执行helper SHA `32edcedbef6e7912119e1596fa5838c350ad141ab815acac3d73be4f97f70bf5`及原件冻结保留，不以随后增强清理的canonical版本替换历史执行身份。

## 5.14 r6门禁启动权限失败与fresh-stage防护

r6门禁39.699639772s后失败，completed仅`r0-datasystem`及其gate epoch；Mooncake尚未执行接口门禁。真实`run/gate-r0-mooncake/master.log:1`明确`nohup ... launch_master.sh: Permission denied`。stage原先先为baseline shell加x，再`copy2`覆盖pure-KV launcher，源0644将目标x移除；这是可重现的部署权限错误，不是Mooncake接口失败，也不能据此宣布三方纯KV门禁通过。

修正只用于全新根：所有shell overlay复制后再按实际mode增加`0o111`，最终inputs和runtime_overrides记录整数mode，运行/诊断/采集检查权限变化；原r6输入、失败日志和诊断证据不改。独立`test_batch_stage_overrides.py`用临时完整baseline验证0644→0755、0640→0751且源权限/字节不变、最终mode/SHA/size绑定、freshroot/alias拒绝、缺失/symlink覆盖事务回滚及native制品变更拒绝，5/5通过0.228s；没有执行目标服务。

r6恢复原件记录0.207646857s、`errors=[]`（SHA `510835caea13499d1f75cc41692d327452d06a8860ce8a4114fa3fe60acbf7c6`）。qualification restore的两项generic guard引用同一terminal gate，不得写成两个独立已完成实验；新根门禁和正式结果仍以其实际原件另行验收。

## 5.15 r7实际资格结果与构建直达分支的界限

从本地已采集原件确认r7 gate137.246847605秒、3个gate epoch通过；C8 smoke437.495450633秒、12个epoch通过；独立重算50有效支持结果、10 N/A、0issues。TCP审计13.311996920秒、16组合/32方向通过。summary SHA依次`00d0fbf0568a304140a1f95d26b4595ea76d9eb880040f8a1d85084047ff295b`、`9ca51eee53225b6077d41999fa20dbc4cc48bf3855ce8cdf4c4b40ca9a3db203`，TCP证明SHA `4f8aff3a0b517cff731aed16e3f6817eb9933b7cf4050a0734ca216eafa8781d`；stage SHA `991692d50b4fc05ae1150671da3f47d17dd53919ac24cfda2f51649b08b059eb`。

此节保留资格阶段记录；正式终态及132有效/18原生失败/30 N/A见5.16。正式运行期间没有额外SSH、rsync、扫描源码或哈希目标制品。r7真实复用既有固定构建物，没有走新增from-source bootstrap或重新编译；新可选`--builds-only-native-root`当时完成本地20项机制测试，后续又补充准入fixture，目的是让后续重放不被无关旧native/TCP formal阻断，不能冒称r7实际执行入口。完整命令与构建元数据/配方/现态/制品绑定合同见 [script-design.md](script-design.md) 4.3。

## 5.16 r7正式终态与部署/资源独立审计

正式4191.703410091秒，18个原生失败均有rc1、SDK原件SHA、原生错误、measure_failed事件、epoch及suite有序清单，既不是N/A也不是编排fatal。部署审计29.106029330秒，431输入前后重核，21集群/42状态目录、7项pure-KV旗标一致；DB特征文件0，42份stdout共1800B、指定存储文件错误匹配0，42份普通eviction状态各26B。live仍仅gate1/21。当前实际batch_analyze源码与未执行的冻结副本不同，实际4个依赖SHA/bytes另存audit，保留121份冻结输入而不覆盖。

20924个原始采样、150个实际SDK窗均完成资源核对；server/client无内存事件及swap，monitor上限事件、client少量限流与外部背景CPU另行披露，详见[资源结果](resource-results.md)。本地工具回归355+7项与29项报告专项检查是工具fixture，不替代产品UT/ST、原生并发/持久化恢复测试；本任务无产品代码改动和模块结构变化，repo_context无需更新。
