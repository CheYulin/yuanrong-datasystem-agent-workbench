# 4. 测试验证结果与操作耗时

## 4.1 范围与当前状态

本轮是 tiantiyun 单物理机实验控制台。正式基础和扩展实验在逐项执行，最终性能表以已终态且离线验收通过的任务为准。下列控制链路结果已实际发生，不能将功能探针的单轮结果当作正式扩展性结论。

| 验证项 | 实际结果 | 证据 |
| --- | --- | --- |
| 应用回归 | 121 项通过，2.034 秒；包含14项独立离线审核篡改测试 | 本地 tests 与外部验证日志 |
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
