# 6. 结果验证与最终验收

状态：**正式证据矩阵已完成，交付验收进行中**。r7独立重算132有效、18原生失败、30 N/A，180槽与36服务周期齐全、0issues；evidence_verified=true、passed=false。页面、下载包与公网验收尚待完成，不能将完整执行写成所有API成功。

## 6.1 实际输入与比较边界

当前主配置为 `/home/worktrees/kvc-batch-tcp-20261020-r7`，本地证据根为 `/home/t14s/workspace/scratch/2026-10-20-kvc-batch-tcp-r7`。协议为 `separate-read-and-fresh-put-v1`；stage记录121项冻结执行输入，SHA256为`991692d50b4fc05ae1150671da3f47d17dd53919ac24cfda2f51649b08b059eb`，已按采集原件逐SHA/size/mode核验。产品复用固定源码构建：DataSystem rc44、Mooncake v0.3.13.post1、LMCache v0.5.6；不是本轮重新编译。Mooncake queued/pending均8192，属于容量适配profile，不能标为默认配置。R5等早期记录在后文按其原执行身份保留。

| 检查项 | 已确认事实与限制 |
|---|---|
| 每次调用的 key 数 | B=1/16/256/4096；DataSystem/Mooncake 调用原生 batch；一次公开 API 不等于一次内部 RPC |
| 相同实际数据 | 每 key 1,024 bytes、C8；正式 harness 的相同 `run_id/client_index/phase/index` 产生相同 key，payload 由固定 SHAKE256 规则产生，不因产品或 B 改变 |
| payload 区分度 | 性能工作集每客户端使用四种基础 payload，UPSERT 使用另一组四种 payload；不是每个性能 key 都有独一无二内容。独立 gate 另使用按 key/版本派生的独立 bytes 验证映射和顺序 |
| 固定热工作集 | 每产品/重复的read epoch仅65,536 keys / 64 MiB，每客户端私有8,192 keys；跨B读同一池，GET/UPSERT/EXIST hit使用该池，EXIST miss用独立absent namespace。read阶段不继承任何fresh PUT数据 |
| fresh PUT | 每个支持B独立重启双服务和预置同一65,536-key池，再固定预热32,768新keys（每客户端4,096，按实际B原生批写），正式前resident=98,304 keys / 96 MiB。随后正式写262,144新keys / 256 MiB；smoke写32,768新keys / 32 MiB。不同B不累积前序PUT，LM不支持的B不做scalar padding |
| 更新与后续读 | UPSERT 交替两组真实内容，计时后验证最后版本，并在窗口外完整恢复/读回基础工作集；GET 不应继承未知更新版本 |
| 资源 | server/client 各 8 CPU、12 GiB，monitor 独立 2 CPU、1 GiB，swap=0；两个服务共用服务配额。原始cgroup/配置和150个SDK窗口的采样归属已核验，缺样/干扰边界见资源结果 |
| 计量 | 吞吐包含同步起点至最晚客户端结束；API 延迟为整个 batch 完成时间，含适配器 O(B) 校验，不能除以 B 后称单 key P99 |
| LMCache N/A | 仅 B1 支持本次等价 PUT/GET；所有 B 的 UPSERT 和 B>1 的 PUT/GET 明确 N/A。EXIST 为原生 P2P 独立 key 向量，含命中锁释放 ACK 的成本，不是零成本布尔缓存 |
| 服务epoch | 正式完成9个read启动+27个fresh PUT启动=36个epoch；smoke完成12个epoch。DS含gate1、smoke5、正式15，共21次独立集群启动/42个Worker实例；独立部署审计已核验启动与日志，live只覆盖gate1/21 |

工作负载依据冻结 `batch_benchmark.py` 与 `parameters.json`；能力依据三个 adapter 的 metadata 和 [接口设计](test-design.md)。同 key、同字节、同配额减少输入差异，但不保证各产品内部 RPC 数、锁、分配和复制相同。

## 6.2 r3 原生正确性门禁

`batch-gate-001/summary.json`：2026-10-20 **04:20:51.766565–04:23:19.454293 UTC**，147.687730193 秒，`valid=true`、`failed=[]`，三个产品组均完成。结束时 parent/server/client/monitor 四个 cgroup 成员均为空。

本地对10份真实客户端 JSON 重新执行 component 合同校验，覆盖如下；这次校验读取已执行原件，不重新调用产品。

| 产品 | 初始端点 × 写模式执行 | B 组合 | 步骤失败 |
|---|---:|---:|---:|
| DataSystem | 2 × insert/upsert = 4 | 16 | 0 |
| Mooncake | 2 × insert/upsert = 4 | 16 | 0 |
| LMCache | 2 × insert = 2 | 8 | 0 |
| 合计 | 10 | 40 | 0 |

原件共478个 passed 步骤和34个明确 not_applicable 步骤。检查包括完整 bytes、反序/子集 GET、EXIST 全命中/稀疏混合/反序/全缺失向量、已有 key 重写后的旧值或新值语义、初始化和 close。LM 不支持的 B>1 PUT/GET 仅用标明的 scalar setup/spotcheck 完成正确性准备，不改写成原生批量性能。

门禁 `summary.json` SHA256：`49868fd9e557ae6ee2d79f69301a5e850075223b05c5170b0bf2e9420e81036b`。门禁与 smoke 的 `batch-stage.json` 字节 SHA 均为 `2cc67095ed2e9e1f0319ff60190d30eccdbbef544ecd3ba527171e5d73e28c3d`；它们使用同一冻结 stage。

## 6.3 r3 C8 编排 smoke

`batch-smoke-001/summary.json`：2026-10-20 **04:25:23.437817–04:30:02.123699 UTC**，278.685888789 秒，`valid=true`、`failed=[]`；monitor 与 interference 退出码均0，结束四个 cgroup 排空。其 SHA256 为 `7dfad7d4b5ce98026fbc76143b8bc7618561cea2eb99a992235a6c1284430545`。

本地读取全部60份操作 JSON，确认50个不同的有效身份、10个 N/A：DataSystem20、Mooncake20、LMCache10个有效结果；失败 batch 为0。所有有效结果均为C8、1 KiB、65,536总热 key、每客户端8,192 key；客户端身份0–7齐全、400个客户端进程退出码均0，close 确认齐全。合并 histogram 样本数等于成功 batch 数，`success_keys=success×B`；每个 fresh PUT 均完成32,768个新 key。两个支持 UPSERT 的产品在四档 B 后各累计完整恢复/验证262,144个 key（同一65,536池重复四次），不是额外扩大工作集。

smoke 的时间型操作仅1秒、预热0.1秒，fresh PUT 固定32 MiB；正式合同为20秒、预热3秒和256 MiB fresh PUT。B4096 在短窗口内可能只有少量 batch，smoke 只证明接口编排可执行，不提供正式中位数、P99或资源排名。

## 6.4 r3 批量 TCP 载荷证明

新的 `datasystem-batch-tcp-verification.json` 是本配置 gate 的独立离线审计：`valid=true`，16个 case、32个 PUT/GET 方向通过，解析10.107045058秒。文件11,623,370 bytes，SHA256 `922c976705d143e57b62726dd18a86fdfbfaecff171e243d03320e5fdb378427`；记录144份输入身份和3份 parser 源码 SHA。

规则同时要求同 case/version0 的任一完整1,024-byte payload、实际成功 syscall 返回范围、正确 PUT/GET 方向、操作时窗及真实 Worker TCP endpoint，且方向总成功 TCP 字节量至少 `B×1024`。保留的 live 配置证明关闭 client/worker SHM 传输及 RDMA/URMA；EXIST 为不传 value 的元数据查询，不套用 payload 字节下限。

方向总量含协议开销；一批内找到完整确定性 payload 不证明每个 key 的全部字节都被 `-s2048` 文本打印，也不证明所有 Worker 间字节、确切 RPC 数或正式窗口持续 strace。正式压测不携带该跟踪开销。更完整的源码链和旧审计限制见 [datasystem.md](datasystem.md)。

## 6.5 默认配置失败历史不能省略

`history/default-r2` 保留原始 gate1 失败50.821004702秒、独立无跟踪诊断通过73.304707536秒、证据组合2.118175008秒、默认 C8 smoke 失败199.051957721秒，以及容量机制诊断96.501955708秒。组合是选择并核对既有正确性证据，不是重新执行 gate；默认 C8 B4096 UPSERT 的 `TRANSFER_FAIL(-800)` 仍真实存在，不能用组合抹去。

r2 的旧 key0-prefix TCP 审计12/16与后续完整 any-key payload 审计16/16分别保留。容量机制诊断观察到 default 失败、8192配置成功，但未捕获明确 queue-full/rejection counter；只允许写配置观测差异，不能把队列已满当成已确认根因。r3 是新根目录和新冻结配置，其通过结果不覆盖默认配置的失败。

## 6.6 正式结果与最终验收清单

| 验收项 | 实际状态 / 证据 |
|---|---|
| r7资格验证 | gate137.246847605s、12-epoch smoke437.495450633s通过；TCP16case/32方向限定证明通过 |
| 正式三重复完整身份 | 2026-10-20 06:02:02.821030–07:11:54.524434 UTC，4191.703410091s；9组、36epoch、180槽完整 |
| 各操作成功与失败 | 独立重算132有效、18有证据原生失败、30 N/A；0missing/invalid、0issues。18失败均DS B16/256/4096 EXIST hit/miss的三重复，不用失败窗口速率排名 |
| 有效性与证据完整性 | execution_complete=true、valid=false、evidence_verified=true、passed=false；suite failed=[]/fatal=null是无编排失败，不是无原生错误；runner返回1原样保留 |
| 统计口径 | 全部三轮有效才给中位数；21个有效窗口低于1000 batch、21个有效窗口短于10s；合并直方图整批桶上界，不除以B |
| CPU/内存/IO覆盖 | 20,924样本；实际间隔P50 200.268978ms、P95 200.2896885ms、最大222.396611ms；所有150个SDK窗均至少11样本，不能把样本最大值当连续真实峰值 |
| 部署关闭项 | 21集群/42独立状态目录、pure-KV旗标一致、无DB特征文件；42份stdout指定存储错误匹配0；live只覆盖gate1/21，其余20为启动记录/日志 |
| 资源与干扰 | server/client内存事件与swap为0；client仅一个SDK窗有1次/1003µs限流；monitor memory.max事件+4568且无OOM，parent传播不重复相加；背景leader/主机差额单列，见资源结果 |
| 恢复 | environment-restored.json：0.209946995s、errors=[]，cgroups_before/after均空；setroubleshootd在restore前已active，不能宣称全程停用 |
| 报告/下载包/浏览器 | 正式生成与本地/公开验收进行中，尚不填最终SHA或验收计数 |
| 公开地址与提交 | 预定 https://yche.me/perf/kvc-batch-tcp-20261020/index.html；阶段7233cca、30c77d9已提交，交付提交尚待实际产生 |

正式r3尝试213.962156825秒后失败；DataSystem B16 EXIST hit的8客户端各1个deadline错误，两个Worker此前已出现同一默认RocksDB目录的文件缺失/大小不符错误。门禁和smoke的成功只对其执行步骤成立，不能认证长窗口或目录隔离。默认目录问题、启动脚本最小修复及旧单key基线的影响详见 [verification.md](verification.md) 第5.6–5.7节；旧DS值不得作为修正后batch收益的公平基准，修正后的B1需随新矩阵重测。

即使后续全部通过，结论也只覆盖固定版本、CPU TCP、单节点双服务、1 KiB/key、C8、四档 B 和所披露的容量配置。未验证RDMA/UB、GPU、跨节点、故障恢复、持久化或其它对象尺寸，不据此泛化。

## 6.7 r4通过的协议预检与r5更正

本地r4原件确认：gate145.847667354秒、smoke277.512075568秒、DataSystem TCP审计9.914668323秒均通过；环境恢复0.217329077秒，`errors=[]`、记录服务active且四cgroup为空。r4没有`formal-batch-001`执行，是目录隔离后的正确性/协议预检，不是失败formal，也不贡献正式重复。

r4的读操作仍可能继承前序PUT背景，且单批warm PUT使额外key数随B变化。r5因此将全部计时READ/UPSERT/EXIST放在仅固定池的read epoch，随后每个支持B单独fresh PUT epoch，统一32,768 warm keys。N/A不填零，也不补scalar PUT冒充原生能力；修正后的B1随同一协议矩阵重测。

r4 gate/smoke/TCP原件SHA依次为 `ba8023cd5ae8f365e1db4978e43760954169c06c194e536c030d1418a73ed12c`、`449718a959c075b91c6a0a6b78a666150d89a6dc35d7753402cedbb79057022b`、`07e169794a1bc86363ed14047d81f9b9bb9fe8422ea215aa436f1a95e620e624`。它们及r3失败分别归档，不重新标注为r5结果。

## 6.8 r5实际通过与新失败的独立边界

本地完整采集确认gate147.334149718秒通过；smoke534.366434989秒、12个epoch齐全、50有效支持格/10 N/A、0接口失败。两个通过summary的SHA依次`99361a8baaec0bc3a208a0f2baadf82bf24ac0184744284124e9aa0e1dff7431`、`aa6d2383559fabb2d79139065990cefec2932cfcff16a364c102209749977027`。

TCP首次离线审计14/16源于两个B1 GET的完整1KiB payload跨同一成功`readv`相邻iov，而旧parser只查单iov。旧失败文件保留；仅本地parser补充严格成功字节边界与截断屏障，27项fixture通过后新`-v2.json`16/16、32方向通过，耗时28.440561944秒，SHA `7f233fa749786a3d2397f26741937a3a6341b4d6ac2fb677fa403c6dc3ac5589`。不修改native或冻结测量输入，不扩展为整批每字节持续跟踪。

r5正式05:16:59.135590–05:20:06.445844 UTC，187.310265231秒后在B16 EXIST hit失败：6.407281222秒中121,709成功batch、8个客户端各1个deadline异常。此时元数据目录已独立，两Worker stdout均0 bytes；不能再以R3共享RocksDB错误解释本次运行，也不能把新失败抹掉。固定rc44静态审查发现GET在共享线程池初始化deadline而未恢复，EXIST多owner任务复用该池但未传播deadline，发RPC前读到旧负预算的机制与现象吻合；TLS动态状态和因果仍需独立诊断。完整源码锚与纯内存候选见 [verification.md](verification.md) 5.10–5.12。

后续用户要求关闭RocksDB和stream。候选仅增加官方flag关闭DB、stream和非必要指标/请求访问日志，保留元数据/服务发现/心跳与10,000ms请求预算；不删除GET校验、刻意选单owner或给失败补零。若新配置仍存在原生read错误，应保留完整failure/exit/SHA并继续其余独立操作，区分`execution_complete`与接口`valid`，不将真实错误场景纳入成功三重复排名。

## 6.9 r6纯KV诊断已执行，整矩阵仍待验收

纯KV对照实际完成：A EXIST-only13.027326933s、248/248成功且两端有TCP活动；B GET16后190成功+1异常，10.019303674s报本地line44 deadline；C GET16后等待10.1s，EXIST16在0.021342848s失败，实际窗口0条正TCP发送；D同样先GET16/等待后EXIST1在0.001553996s成功。SDK46.339531674s、外层63.659922266s、monitor0和四cgroup空。无RocksDB制品，两Worker保留普通26B eviction状态文件，四客户端close后socket快照均0。165份输入逐SHA/size、221独立本地检查通过；动态证据与未直接观测TLS的边界见 [verification.md](verification.md) 5.13。

这是单客户端低速诊断，不纳入性能重复。R6后续gate39.699639772s失败仅因Mooncake launcher Permission denied，DS gate已完成，MC/LM门禁尚未完整执行。复制overlay后x位恢复和mode inventory的5项本地回归通过，必须在fresh根继续验证；原r6失败不覆盖，不以部署错误给产品填零或N/A。完整矩阵、资源覆盖、包和浏览器验收仍待实际执行。

## 6.10 r7资格阶段记录（正式终态见6.6/6.12）

本地原件gate137.246847605s、smoke437.495450633s、TCP13.311996920s均valid；smoke12epoch齐全，独立重算50有效/10 N/A/0issues，TCP16case/32方向限定证明。精确summary和stage SHA见 [verification.md](verification.md) 5.15。它们不证明20秒EXIST已修复，也不替代正式三重复；运行期目标节点不作额外采集。

新增构建直达分支是后续复现能力，当前只通过20项本地fixture；r7仍使用既有核验构建物。新分支不伪造旧smoke或formal，不因缺少默认参数失败历史就编造容量配置收益；严格核验builds_provenance后，SDK正确性仍必须由其自身fresh batch gate/smoke/TCP证明。

## 6.11 正式运行期间的本地交付工具复核

控制机执行 `python3 -m unittest discover -s . -p 'test_batch*.py'`：348项通过，unittest报告42.182秒；这是工具fixture的框架耗时，未记录该命令外层起点，不倒推起止时间。另执行 `python3 -m unittest test_lmcache_profile test_mooncake_pure_kv_launcher`：7项通过、框架0.319秒；两份JS的 `node --check` 与 `git diff --check` 通过。最初误用子目录discover返回NO TESTS RAN/退出5，原操作记录保留，纠正为实际模块后才得到7项结果。

本次检查的是构建输入准入、失败分类、资源/部署审计、报告和交付工具，没有修改DataSystem/Mooncake/LMCache的原生代码，也未把本地工具测试写成产品UT/ST、恢复或性能证据。R7执行输入仍以其冻结清单为准；后增加的复现分支、失败回执链接和浏览器断言是交付工具修订，不能追认成目标机已执行源码。共享suite锁、PID/start-ticks所有权、事务回滚和目录隔离由相关fixture覆盖；没有产品持久化/并发协议或模块结构变化，`.repo_context`无需更新。此处记录正式期间的工具检查；正式终态与实际交付验收见6.12以后。

补充fixture后再次执行同一discover命令，355项通过（框架34.148秒）；7项profile测试通过（0.560秒），此次由`workflow.run`保存实际外层起止与耗时。4项新增回执/DOM片段测试覆盖0成功原生失败、CSV链接、错误/状态/回执篡改及HTML转义；它们是synthetic矩阵，不充当正式运行数据。

## 6.12 r7终态输入与独立核验

正式summary SHA256为 `3a797fe66006d5f6cadeeb5643f16e9d52be4be79dc6ecf387724fd6e148d24d`；部署审计SHA256为 `936de7a4f005c5bf6639678e0ab39cd0605f5cd18926806ff0c1e494e0e78cea`。三方原生源码未修改。采集完成后分别执行analyzer、resource audit、deployment audit；各自原件、执行helper和输入SHA由最终报告重新绑定，不能由本文数字替代原始证据。完整结果与残余背景观测见后续交付文档。

## 6.13 首次本地网页与ZIP验收

实际本地浏览器347检查通过，1440/900/390视口无横向溢出与运行错误；核对180槽/36epoch、失败原文与回执链接、吞吐/整批延迟散点与柱图、资源窗口、9图caption、左侧导航与移动菜单。还实际HTTP GET了带SHA查询参数的ZIP，确认正文SHA等于唯一链接及清单值。此验收针对页面数据和交付字节，不代替独立原始SDK重算，也不提前宣称公网通过。

实际清洁解包验收1822成员、134348850 bytes，CRC/路径/文件类型/逐成员SHA/原模式通过；121份冻结输入精确集合和1696份公开镜像字节匹配。6项CLI help、756项Python编译、56项shell语法、4项Node语法通过。ZIP提供脚本、模板、文档和精选证据，未包含最终index/data.js/data.json；离线网页需保留整个公开报告目录的相对依赖，不能把此ZIP标为最终网页离线包。

实际工具源码分别为SHA256 `71ee3dbeced313764d102fcc2db2dcdf5045b00443489f94c64cb921ee491d8a`（executed-qa.mjs）、`e3ee0c97ef1f5a37e8aa8fa6a43c9f284a1dc10229f7e33a878e858ebc2a4c46`（package_qa.py）。原件保存在R7的final-local-browser-qa与final-package-qa；后续交付快照按内容SHA保存，不替换原冻结测量或生成器源码。公开发布、旧页面提示与最终文档刷新仍按实际命令继续登记。
