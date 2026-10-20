# 7. 操作耗时与证据边界

状态：**正式三重复已终态，交付进行中**。正式4191.703410091s，恢复0.209946995s；原生失败与编排失败、SDK窗口与外层命令各自保留，不直接相加。

## 7.1 计时口径

- `start_wall_ns/end_wall_ns` 与 UTC 字符串用于定位执行；同进程 `monotonic_ns` 差值或原件 `duration_s` 表示实际耗时。
- 远端 driver phase 包含其准备、服务启停、门禁/工作负载和清理；操作 JSON 的 SDK窗口仅为吞吐分母，两者单列。后验校验、恢复、close 不加入 SDK窗口。
- 本地 SSH/rsync 外层命令另存 `operation-timings.jsonl`。它们包含连接和传输等待，不能与所包裹的远端 phase 相加当总耗时；跨主机 wall clock 也不能直接相减推断网络开销。
- 错误、独立诊断、离线解析与证据组合分别保留。诊断不是新增正式重复，组合不是再次运行服务。
- 本轮复用此前固定版本的源码构建。原始构建开始/结束与制品 SHA 仍保留，但不计作“本轮批量重新编译耗时”。

## 7.2 r3 已完成阶段

证据根 `/home/t14s/workspace/scratch/2026-10-20-kvc-batch-tcp-r3`。表中开始/结束均为2026-10-20 UTC；北京时间为UTC+8。

| 阶段 | 开始 UTC | 结束 UTC | 实际秒数 | 结果与原件 |
|---|---|---|---:|---|
| 真实原生 batch gate | 04:20:51.766565 | 04:23:19.454293 | 147.687730193 | 三产品组完成、10客户端/模式执行、40个B组合通过；`batch-gate-001/summary.json` |
| C8 编排 smoke | 04:25:23.437817 | 04:30:02.123699 | 278.685888789 | 三产品组完成、50有效结果/10 N/A、0失败；`batch-smoke-001/summary.json` |
| DataSystem严格TCP离线审计 | 04:31:51.904521 | 04:32:02.011565 | 10.107045058 | 16 case/32方向通过；`datasystem-batch-tcp-verification.json` |
| r3正式尝试 | 04:32:12.385088 | 04:35:46.347239 | 213.962156825 | 失败，8有效操作后B16 EXIST hit无效；`formal-batch-001/summary.json`，未完成任何整产品组 |

gate和smoke均在结束记录中确认parent/server/client/monitor四个cgroup无成员。TCP审计读取保留trace进行离线解析，其10.107秒是分析耗时，不是客户端吞吐分母。

正式尝试结束时monitor/interference退出码均0，四cgroup排空。无效EXIST操作自身测量6.407137609秒，不是完整20秒成功窗口；不能将它当作吞吐/P99排名，也不能用8个先前成功操作补成新配置的重复。双Worker共享默认RocksDB路径及973,779条stdout匹配错误见 [verification.md](verification.md)；修正脚本后另建新执行身份，失败尝试的213.962秒仍保留。

## 7.3 r3 外层命令账本

以下直接取自本地 `operation-timings.jsonl` 已完成记录，退出码均0。记录名以完整 argv 为准，不能因文档简写而合并不同执行。

| 外层操作 | 实际秒数 | 与远端phase关系 |
|---|---:|---|
| 创建bundle缓存目录 | 2.251962651 | 只准备脚本传输目录 |
| rsync脚本bundle | 2.667688620 | 传输；不启动产品 |
| stage新r3根目录，Mooncake queue8192 | 2.326634195 | 冻结输入和复用制品；不是源码编译 |
| 运行前环境快照 | 1.887610921 | 只读硬件/进程/配置证据 |
| prepare environment | 2.515996013 | 记录环境准备事务 |
| SSH执行原生gate | 149.636947085 | 包裹147.687730193秒driver phase，不能重复累加 |
| SSH执行C8 smoke | 280.733203652 | 包裹278.685888789秒driver phase，不能重复累加 |

此表是已存在步骤的快照，不是完整最终账本；后续formal、collect、restore、分析、打包、同步与浏览器验收需保留新增记录。不能用上述阶段之和代替首尾墙钟，因为中间审查、等待和其它本地操作也耗时。

## 7.4 默认r2失败与诊断历史

原件位于 `history/default-r2`，始终与r3正式性能分开。

| 阶段 | 实际秒数 | 真实状态与解释 |
|---|---:|---|
| 带跟踪gate1 | 50.821004702 | 失败；DS完成，Mooncake B4096 GET出现空项 |
| 独立无跟踪正确性诊断 | 73.304707536 | 六个Mooncake/LM客户端模式执行通过；没有调queue参数 |
| gate2证据组合 | 2.118175008 | 组合成功；不是再次执行，也不是性能测量 |
| 默认C8 smoke | 199.051957721 | 失败；Mooncake B4096 UPSERT预热TRANSFER_FAIL(-800)，不造吞吐/P99零值 |
| default/queue8192机制诊断 | 96.501955708 | 两配置诊断完整结束；default native_valid=false、queue8192=true，均清理成功；未确认queue-full根因 |
| r2完整payload离线TCP补验 | 15.667086592 | 16 case/32方向通过；旧12/16 key0-prefix审计不改写 |

以上错误并非正式接口失败率的分母，也不能从最终报告删除。当前r3通过说明新冻结配置通过对应门禁与smoke，不能把默认参数失败改成成功。

## 7.5 待闭合的交付计时

| 待完成项 | 后续应保存的真实证据 |
|---|---|
| 正式三重复及SDK窗口 | driver开始/结束/monotonic耗时；每操作同步窗口、客户端退出与后验校验 |
| 采集和环境恢复 | collect命令退出码/耗时；restore JSON及四cgroup排空、被暂停服务恢复状态 |
| 独立重算 | 正式身份完整性、三重复统计、缺样coverage、源码/配置/SHA绑定及实际分析耗时 |
| 报告/原始包/复现ZIP | 生成与压缩耗时、清洁解包验收、逐成员SHA和最终包字节身份 |
| 本地浏览器 | 实际视口、检查数、失败分类、截图与单调耗时 |
| 公开页面与下载 | 实际URL响应、SHA/大小绑定、可选API状态、浏览器检查与下载验收耗时 |
| 最终同步和提交 | 真实命令、退出码、耗时与已产生的commit；不提前填写未来hash |

资源unknown/null是观测边界，不应通过补采样修改旧SDK窗口或用计时文档掩盖。最后阶段若发生工程失败、缓存命中旧包或验收脚本契约修正，也应另记失败与重试耗时，保持性能、预检和交付三类证据的原始边界。

## 7.6 r4通过的协议预检计时

证据根`/home/t14s/workspace/scratch/2026-10-20-kvc-batch-tcp-r4`，并归档至r5的`history/protocol-preflight-r4`。

| 阶段 | 实际秒数 | 原件与边界 |
|---|---:|---|
| 原生batch gate | 145.847667354 | `batch-gate-001/summary.json`，04:44:02.190778–04:46:28.038446 UTC，valid |
| C8 smoke | 277.512075568 | `batch-smoke-001/summary.json`，04:46:29.827898–04:51:07.339965 UTC，valid |
| DataSystem TCP离线审计 | 9.914668323 | `datasystem-batch-tcp-verification.json`，16组合/32方向通过；解析耗时不是吞吐分母 |
| 环境恢复 | 0.217329077 | `batch-smoke-001/environment-restored.json`，errors为空、服务active、四cgroup空；SSH外层1.986956592秒另记，不能叠加 |
| 正式矩阵 | 未执行 | r4在formal前被新协议替代，不记失败formal，不贡献性能重复 |

## 7.7 r5新协议待闭合阶段

协议`separate-read-and-fresh-put-v1`，工具阶段提交`2483b37`；111项stage输入等待最终采集逐SHA绑定。每个read epoch仅65,536-key池；每个支持B的fresh PUT独立启动、同池加固定32,768 warm keys。正式前PUT resident均98,304 keys / 96 MiB，不沿用此前单批warmup随B变化的背景。正式设计36个epoch、smoke设计12个epoch；服务启动/停止、预置、完整恢复校验与清理时间全部计入driver阶段，但不加入SDK吞吐窗口。

| 阶段 | 当前状态 | 待填原件 |
|---|---|---|
| r5原生gate | 147.334149718s，通过 | 05:02:53.766290–05:05:21.100440 UTC；summary SHA见verification5.10 |
| r5 C8 smoke | 534.366434989s，通过 | 05:05:22.953225–05:14:17.319621 UTC，12epoch、50支持/10 N/A；不是20秒性能结果 |
| r5首次TCP离线审计 | 16.743329653s，14/16 | 两个实际成功GET的完整payload跨同readv相邻iov；原失败JSON不覆盖，不当接口失败 |
| 严格同syscall parser修正补验 | 内部28.440561944s / 外层29.251941008s，16/16 | `datasystem-batch-tcp-verification-v2.json`，27项fixture通过，真实trace复验；内外层不重复累加 |
| r5正式尝试 | 187.310265231s，失败 | 05:16:59.135590–05:20:06.445844 UTC；B16 EXIST hit6.407281222s、121709成功batch后8异常；0完整组/epoch，不能补齐设计180格 |
| r5恢复 | 0.2119152s，errors为空 | 父任务实际restore原件；采集已完成，不以恢复成功改写接口失败 |
| 纯KV诊断/新矩阵及部署审计 | 待实际执行 | 21次DS启动/42个Worker仍是完整计划的覆盖目标，最终按actual plan推导；gate-live与其余live-unobserved分列 |
| 报告、包、公开验收 | 待完成 | 只记录本批量报告实际执行的耗时/检查数，不借用单key页面历史验收 |

pure-KV启动配置14项与独立deadline诊断8项本地fixture合计22项通过（0.389s）；pure-KV部署审计24项fixture先通过6.303s，追加实际执行依赖SHA绑定后重跑5.715s通过。它们验证工具的分支、清理与篡改防护，不是SDK实际诊断耗时。新A/B各13秒、C/D各sleep10.1秒是诊断设计上限口径，实际执行耗时以新JSON为准，不能预填46秒为已测结果。

## 7.8 r6纯KV实际诊断与权限预检失败

证据根`/home/t14s/workspace/scratch/2026-10-20-kvc-batch-tcp-r6`。下列均为已产生的原件，不借用R5计时。

| 阶段/SDK观察 | 实际秒数 | 真实结果 |
|---|---:|---|
| 诊断外层启停/监控/清理 | 63.659922266 | 完整，errors=[]，monitor0，四cgroup空；包含SDK阶段，不与下一行累加 |
| 四个fresh SDK场景合计 | 46.339531674 | 诊断完成但并非SDK全通过，B/C原生异常保留 |
| A EXIST-only观察 | 13.027326933 | 248次成功，双TCP端点活动 |
| B GET→EXIST观察 | 10.019303674 | 190成功+1本地deadline失败，非20秒压测完成 |
| C 等待后EXIST16调用 | 0.021342848 | 前置sleep10.100258905秒另记；本次EXIST窗口无正TCP发送 |
| D 等待后EXIST1调用 | 0.001553996 | 前置sleep10.100222394秒另记；单key对照成功 |
| 独立本地原件复核 | 0.195153440 | 165输入前后SHA/size、221检查通过；不是新增SDK测量 |
| 纯KV门禁尝试 | 39.699639772 | DS完成；MC launcher Permission denied；LM未完成；无正式性能数据 |
| qualification恢复 | 0.207646857 | errors=[]；两generic guard都引用同一terminal gate，不算两个实验 |
| overlay权限回归 | 0.228 | 5/5本地fixture，真实copy2最终mode、freshroot/inputs绑定与事务回滚；未启动产品 |

诊断helper实际源码、四case/trace/FD、关闭项与门禁权限错误的精确SHA和边界见 [verification.md](verification.md) 5.13–5.14。后续新根实际资格验证和正式执行耗时尚未填入，不能用该诊断替代公平矩阵。

## 7.9 r7已完成资格与新增重放工具的本地验证

| 阶段 | 实际秒数 | 真实状态 |
|---|---:|---|
| r7原生batch gate | 137.246847605 | 05:50:28.542874–05:52:45.789722 UTC；3gate epoch通过 |
| r7 C8 smoke | 437.495450633 | 05:52:47.526525–06:00:05.021960 UTC；12epoch、50有效支持/10 N/A，独立重算0issues |
| r7 DataSystem TCP离线审计 | 13.311996920 | 16case/32方向通过；解析耗时不加入SDK吞吐分母 |
| r7正式三重复 | 4191.703410091 | 06:02:02.821030–07:11:54.524434 UTC；9组/36epoch，132有效+18原生失败+30N/A，完整执行而非全API通过 |
| 可选from-source stage回归 | 3.673 | 20项本地fixture；没有编译产品/启动服务，不是r7构建或测量阶段 |

r7使用原固定构建物；新增直达分支保留原build command墙钟、metadata及配方/制品SHA，不把未来bootstrap工具时间当成本轮编译耗时。源码到pure-KV的新链及未执行端到端重建的边界见 [script-design.md](script-design.md) 4.3。

## 7.10 正式后的独立审计

环境恢复内部0.209946995s（errors=[]）；analyzer实际外层14.603280237s、deployment audit外层29.227308596s（内部29.106029330s）、resource audit外层42.574841724s，均为本地读取原件，不新增性能重复。三个审计可以重叠执行，不能直接求和为交付墙钟。报告生成、浏览器、公网发布和最终归档耗时待各自完成后更新。
