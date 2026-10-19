# 统一 TCP 实验交付核对

Status: Completed；TCP测量、本地核验及公开核心页面验收已完成，记录外部操作排除项仍为unknown、缺样资源仍为null。此文不替代原始日志或独立验证器。

## 1. 已完成的准备与预检

| 用户要求 | 当前证据与边界 |
|---|---|
| 发布源码构建 | build.md 与 formal-tcp-001/builds：复用已实际完成的三个固定SHA构建，新部署重新核对8个原生制品；本阶段没有重新编译 |
| 同节点双存储服务 | tcp-plan.md 与 products/config：DataSystem双KVC Worker+ETCD，Mooncake双4GiB peer+Master，LMCache双4GiB CPU MP Server |
| 所有通信先TCP、无RDMA/UB | protocol-evidence：三产品两客户端真实完整内容与TCP syscall证据通过；DS补充三尺寸、Worker flags、实际GET materialization |
| 公平资源与干扰控制 | 环境快照、cgroup-settings、相同16个客户端/服务物理核与NUMA0、12GiB+12GiB、独立监控核；后台服务恢复已留证，未记录干扰排除保持unknown |
| exist/put/get与kvtest参考 | test-design.md：共同fresh-key bytes接口、同步完成、独立SDK、多进程同步窗口、固定种子和三次重复；真实共同smoke6cells18结果全部通过 |
| 主矩阵驱动阶段 | 驱动 summary 回报 81 cell/216 标准结果、failed=[]、无 fatal；UTC 2026-10-19 20:41:54.554432→22:42:19.371899，7224.817467s；本地日志另核到216唯一完成事件，原始结果独立复核通过 |
| 九格 PUT 资源专项执行 | summary 回报 UTC 2026-10-19 22:42:44.441697→22:46:17.881811，213.440114s；9/9、valid=true、failed=[]、monitor_exit=0；21192样本、0读取错误，9窗口覆盖、单窗至少58样本；收尾四cgroup均空，原始数据独立复核通过 |
| 失败与中间耗时 | 前五轮失败protocol-history、preflight-history、不可变stage、修订SHA链、恢复receipt、操作JSONL；从正式性能窗口排除 |
| 本地独立结果/协议核验 | 216主结果/72组三重复、9专项及TCP7/7通过；74,795,773成功、0接口失败；223核心JSON与1586历史文件/25输入/8制品已核对，缺interference-audit的排除项仍unknown |
| 收尾恢复 | environment-restored.json：setroubleshootd.service start退出0、后验active，errors=[]且四cgroup空；0.22397715s为记录恢复区间，非SDK耗时 |
| 原始证据归档 | rawtar 4384/4384普通文件逐SHA/尺寸校验、gzip EOF/CRC通过，无遗漏/额外/unsafe/重复；94,315,457B，SHA256见tcp-validation.md；原始日志包不进Git |
| 阶段提交 | 已存在966b74a、96fc52b等，清单见第4节；网站最终提交仅以实际Git记录为准，不预填哈希 |

## 2. 实际交付验收与保留边界

| 项目 | 完成证据要求 |
|---|---|
| 结果与资源边界（已核对，报告须保留） | verify_results通过但evidence_complete=false，记录干扰排除unknown；20短PUT窗口CPU/coverage未知、2窗口无内存点，8组CPU/2组内存中位留空；专项独立于主三重复，不用于补齐主组 |
| CPU/内存/磁盘与短PUT | 7217主样本、21192专项样本，窗口差分/匿名+SHM/总计费口径分开；专项覆盖98.3341%–99.9784%，实际周期p50约10ms，观察者约0.260655核，不能声称零开销；资源图按实际页面核对 |
| 耗时复盘 | timings.json/CSV分开SDK、命令、启动准备合计、关闭估计、门禁与修订；不把复用构建计为本轮编译 |
| 文档与脚本 | build/test-design/tcp-validation/script-design/timing及reproduce.zip；Mermaid时序；锁定参数、版本、环境和命令 |
| 页面与发布 | [统一TCP公开报告](https://yche.me/perf/kvc-tcp-20261020/index.html)，位于xqyun-32c32g:/var/www/html/perf/kvc-tcp-20261020；左侧导航、6个编号章节、ECharts八图、居中下caption及独立原生基线链接已实测 |
| 实际浏览器和下载验收 | 本地405/405、115.821344770s；严格公开v3为408/408、125.743963344s；1440/900/390px、筛选/resize、图点与原始数据绑定、225原始结果、TCP证明与下载链接通过；页头/首页额外3项另计 |
| 复现包与核心保全 | ZIP清洁解压88成员、47py编译/15CLI/14bash/74tests通过；首次文档刷新254核心文件字节/mode不变，最终docs/operations/ZIP及摘要刷新单独记账，不改变测量核心 |
| 公开网络分类 | 必需HTTP/请求失败、pageErrors为0；每视口admin/pages的401明确列为匿名管理权限被拒绝的可选功能，不声称所有HTTP均200 |
| 恢复后成果提交 | 恢复与公开核心页面验收已闭合；工作台/网站仅本任务范围提交，实际提交以Git记录为准，原始源码/基线报告保全 |

## 3. 结论边界

单节点回环TCP、CPU原始bytes、内存缓存与同步客户端接口。事务轮数、pickle副本、元数据拓扑和内存池触页差异如实同屏说明；结果不外推为跨机RDMA/UB、GPU KV tensor、推理TTFT、容量溢出、持久化或故障恢复表现。原生与TCP两套结果分别统计，不能混合补点。

每次重复独立按固定seed随机排列产品与cell；实际r0产品为DataSystem→LMCache→Mooncake，r1/r2为DataSystem→Mooncake→LMCache，DataSystem三轮均首位。产品位置未完全平衡，三次随机重复不能证明已排除时序漂移。

主矩阵、专项和恢复凭据分别记录四个实验cgroup为空；后验服务active仅表示记录时刻状态。结果比较和资源解释见tcp-validation.md，限定于本机固定配置。没有interference-audit.json，因此不宣称已排除任意未记录干扰；Completed表示测量和公开核心页面验收完成，不把未知证据或缺样补成0。

公开v1为405/408、131.612607360秒，3项失败是原生HTML的HEAD长度规则误判；v2为加强严格原生身份规则而只中断自有Node进程，73.308238423秒，不计为通过；最终v3严格重跑408/408，125.743963344秒、退出0。页头标签审计首轮8.100055279秒失败，按实际“发布/修订”标签重试10.960354532秒、3/3通过，首页287条中TCP与原生各精确1条，日期/metadata一致。这两次脚本误判和一次主动中断按交付工程过程保留，不混入5轮协议预检失败或0次正式接口失败。

## 4. 已记录的阶段提交

- `50df91a`：独立 TCP 入口与适配器。
- `9f9d027`：真实三产品协议门禁和预检修订审计。
- `6f4225e`：TCP 复现说明与全部预检耗时后处理。
- `0e7616f`：TCP 交付需求与原生基线分别核对。
- `3589f0c`：报告区分协议预检失败和正式性能失败。
- `ada452c`：独立 TCP 复现支持与验收命令。
- `966b74a`：完整原始 TCP 证明独立保存并以 SHA 绑定紧凑报告数据，同时保留浏览器验证支持。
- `96fc52b`：已核验 TCP 主矩阵、资源专项、比较限制和阶段耗时文档。

以上为当前仓库已存在的阶段提交；公开核心页面已通过独立验收，最终文档/ZIP/ledger刷新按核心不变规则另留证据。网站提交以其实际Git记录为准，不编造哈希。
