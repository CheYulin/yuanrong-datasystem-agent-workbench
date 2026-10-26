# 4. 测试验证结果与操作耗时

## 4.1 范围与当前状态

本轮是 tiantiyun 单物理机实验控制台。基础组、worker和client扫描已终态并通过独立验收；中心配额与逻辑节点扫描继续运行。下列控制链路结果已实际发生，不能将功能探针的单轮结果当作正式扩展性结论。

| 验证项 | 实际结果 | 证据 |
| --- | --- | --- |
| 应用回归 | 153 项通过；包含14项原生审核、9项IO审核、6项IO组合、11项报告绑定测试 | 本地 tests 与外部验证日志 |
| 匿名保护 | 15 条 UI / CSS / JS / API / 指南 / 文档 / 源码 / job下载路由均401；no-store、Vary Cookie通过 | install004-readonly-qa-SiPUfg |
| 授权下载 | 当前与历史任务源码包、命令、资源采样、manifest 均200；逐字节数与 SHA 对应 | 同上 |
| 源码解包 | 355 文件逐 SHA、Python AST、模块导入通过；3篇文档与2图可重建 | source-audit-fixed-qk3c_45s |
| 页面回收 | 原任务 cancelled；stop任务 succeeded；owned cgroup无PID | ed77380dcca847358dc05a37e6675a88 / cancel-proof-004 |
| 恢复 | controller SIGKILL后10.164秒恢复active；原远端PID/startticks不变；日志缺失0、重复0，cursor闭合 | 7bf4ff6d6e634a3ca552aa705d357d13 / recovery-proof-004 |
| 离线独立审核 | 恢复探针3条结果，194项检查通过；SDK直方图和资源窗口重算一致 | audit-recovery-probe-004-repository001.json |
| 首页注册 | 293→294，仅添加1项；保留其余条目，4文件及gzip同步；仅新卡片复用现有管理员显示条件 | registration-apply-004.json |

源码包 SHA256：`61c5c2e3151b227d5cc8bc86688164e622027ce953f67ef555ca54c4e044779e`。本轮离线脚本固定这个源包 pin，未来新版本不能自动套用旧审核结论。

仅通过已有登录会话链做真实浏览器验收，没有重新输入账户密码。真实授权会话在进程内取得，账户和 token 不放入代码、公开报告或源码包。

## 4.2 保留的失败与修复

| attempt / 问题 | 结果与处理 |
| --- | --- |
| install001 / 双产品切换端口探测 | TIME_WAIT 导致误判 Address in use；SO_REUSEADDR 修复，原失败记录保留 |
| 单独一跳资格 onehop-002 | 原 SDK 仍产生 MultiCreate+MultiPublish，两次 RPC；失败保留，未改写成一次 |
| 修复后部署 97dfce9a8fd4427c800429621b146b85 | 两产品真实资格通过，DataSystem 两次部署的一跳写资格通过 |
| 5秒预跑 38facc8df5f449a1a88404a751b04f5b | 仅16条DS结果；切MC前cgroup populated异步状态导致回收失败；未纳入正式性能结论 |
| install004 取消探针 | observed=2、missing=6；API完整列出8条状态，终态cancelled；不是8条原生结果 |
| UI 计划ID映射 | plan v01 / runtime v0 曾使真实有效值无法聚合；按真实spec和runtime AST修复，14项+四屏验证通过 |
| 指南资产 | 旧guide别名相对资源路径与Mermaid 404已定位；修复源文件，等待正式实验后的安装与在线复核 |
| 本地序列观察进程退出143 | client原任务仍成功48/48；现场只读核对队列确认尚无metadata后，仅提交未开始组，未重放client或归咎为原生失败 |

## 4.3 安装及各阶段用时

| 操作 | 总耗时 / 秒 |
| --- | ---: |
| install001 | 28.931 |
| install002 | 30.416 |
| install003 | 29.943 |
| install004（含受保护文档、源码包及服务停止/重启） | 91.177 |
| 真实controller异常恢复 | 10.164 |

每个任务的 `steps.json` 记录 verify_builds、stop、deploy、qualification、one-hop、preload、每case及矩阵验收的独立耗时；原始命令还有进程duration、returncode与身份，按任务ID可回查。

## 4.4 同机资源与角色口径

正式前负载采样约0.40/0.63/0.88，实验owned cgroup为空；未发现其它活跃实验。保留普通系统与管理服务，不把 SSH 瞬时活动当作待杀干扰进程。全过程保存 mpstat / pidstat / dstat / iostat 与角色 cgroup 原件。

逻辑节点仅代表独立loopback端点和产品host ID；cgroup隔离的是整套实验与server/client/monitor角色，workers共享服务端总预算，不是每个逻辑节点独立CPU池。中心使用server下的CPU限额，且在server总预算内。两产品顺序测量。

## 4.5 性能结论的验收规则

基础组为三轮、20秒读/EXIST；PUT为同规模有界fresh插入，保留实际时长与短窗口标记。扩展扫描默认单轮10秒，只用于本机配置敏感性观察，不能当作跨物理机扩展性或统计显著性证明。

MC 单中心瓶颈只有在吞吐平台、Master配额饱和/节流与提高中心配额的响应共同出现时才下结论；若客户端饱和或worker预算先限制，分别说明。DS Coordinator低CPU不能直接等同于对象metadata低CPU，因为对象metadata位于workers。

## 4.6 证据位置

- 控制页：`https://yche.me/site-api/kvc-lab/ui/`
- 指南/实时运行记录：`https://yche.me/site-api/kvc-lab/ui/guide.html`
- 构建与设计网页：控制页“构建与用例设计文档网页”链接。
- 站点持久记录：`/var/lib/kvc-lab/jobs/<job_id>`；目标原件：`/home/cache/kvc-lab/jobs/<job_id>`。
- 本地脱敏验证与截图：`/home/t14s/workspace/scratch/2026-10-26-kvc-control-lab/`。

历史复现必须先安装该任务绑定的源码包和固定产品构建，再执行默认生成新UUID的reproduce.sh；`--original`只读原任务状态。没有自动把当前服务端降回历史版本。

## 4.7 已验收的正式基础与 worker 结果

基础任务 `607cc810760d410ca3bc13634ecdffb2`：96/96 valid，缺失/重复/异常/无效均0，6069项独立检查通过。worker任务 `97186e3c67544950b71c7b5224a301cc`：32/32 valid，2296项独立检查通过。二者均使用上文同一个冻结源包，正式数据不混入取消、恢复或5秒功能探针。

下表为基础组每点三轮中位数，单位 kkeys/s。两产品均为2 workers、8 clients、相同总预算与1KiB对象。

| 接口 | B=1 DS | B=1 MC | B=4096 DS | B=4096 MC |
| --- | ---: | ---: | ---: | ---: |
| PUT | 32.15 | 28.90 | 249.12 | 91.33 |
| GET | 45.85 | 34.74 | 490.43 | 87.37 |
| EXIST hit | 60.28 | 90.55 | 1738.73 | 1060.01 |
| EXIST miss | 42.21 | 99.76 | 2170.90 | 2510.31 |

优势随接口与批量而变，不存在本实验范围内的统一赢家。基础组24条有界PUT窗口短于请求时长；9条低样本P99，分别为两产品B4096 PUT的6条与MC B4096 GET的3条。后者原生调用样本数426/429/433，不能按处理keys数夸大P99样本量。所有实际窗口、质量标记与中位数/min/max均保留。

worker固定总资源扫描中，W1→8的MC EXIST hit约93.78→86.96 kkeys/s、miss约97.62→97.91 kkeys/s，Master约2核且出现节流；仍须配额敏感性组共同解释。DS没有呈线性增长，EXIST miss约54.68→27.13 kkeys/s，不能包装成线性scale-out。worker组只有一轮，7条PUT短窗口，无低样本P99。

基础组IO绑定96个case、102份原件；server窗口96个均可计算，五角色432个可计算、48个unknown（MC Master空counter），reset0。DS B1 PUT三轮的server采样区间写delta为4096/4096/8192B；其它server读写delta为0。worker组server32个均可计算，DS W1/W2 PUT写delta4096/8192B。以上仅是窗口内首末样本区间差值，不是完整SDK窗口的精确磁盘总量；不能据此归因到某文件。

| 已结束操作 | 用时 / 秒 | 口径 |
| --- | ---: | --- |
| 基础任务 | 1989.951 | 站点创建至终态更新时间，含排队、部署、资格和测量 |
| 基础步骤合计 | 1931.722 | 原始steps monotonic duration求和，非端到端 |
| 基础最小原件收集 | 3.611 | 首次记录的文件创建/修改观察间隔，非后来补测 |
| 基础离线审核 | 1.649 | monotonic执行时长 |
| worker任务 | 620.687 | 站点创建至终态更新时间 |
| worker最小原件收集 / 审核 | 5.224 / 0.402 | 脚本monotonic时长 |

已收集正式证据：`audit-formal-baseline-004-attempt001.json`、`audit-worker-scaling-004-attempt001.json`、`audit-io-formal-baseline-004-attempt004.json`、`audit-io-worker-scaling-004-attempt001.json`。其余组须完成独立验收后才进入研究网页。

## 4.8 Client 扫描

任务 `575ecfe61a624320a3aa0153705399f1` 的48/48原生结果通过，3522项离线检查通过，无缺失、重复或无效。固定2 workers、server/client各8核、中心2核、16GiB、B1/1KiB；仅clients变化，每点一轮。

| clients | DS GET / kkeys/s | MC GET / kkeys/s | DS EXIST hit / kkeys/s | MC EXIST hit / kkeys/s |
| --- | ---: | ---: | ---: | ---: |
| 1 | 3.34 | 3.97 | 3.27 | 10.46 |
| 2 | 7.32 | 11.14 | 7.63 | 36.41 |
| 4 | 17.73 | 31.55 | 21.02 | 69.19 |
| 8 | 46.59 | 34.77 | 60.76 | 88.91 |
| 16 | 59.46 | 48.59 | 80.11 | 93.79 |
| 32 | 57.29 | 48.00 | 74.02 | 93.49 |

C16→32的GET略回落，两产品客户端CPU均约7.3–7.5/8核，不能将GET平台全部归因于服务端。MC EXIST高并发平台同时出现Master约2核和节流，等待中心配额组检验。6条PUT短窗口，无低样本P99；端到端1363.941秒，步骤合计1345.151秒。原件132份40.53MB，收集68.784秒、离线审核0.859秒，计入实测耗时。
